# Phase 01 WriteUp – Infrastruktura bazowa (Terraform foundation)

## Step 01 – Terraform project setup

**Co zrobiono:** Zainicjalizowano strukturę projektu Terraform w dwóch warstwach: `terraform/bootstrap/` (tworzy S3 bucket na state + DynamoDB lock table – uruchamiany raz) oraz `terraform/` (właściwa konfiguracja infra, backend w gitignorowanym `backend.tf`). Skonfigurowano środowisko Python (uv, pytest, ruff) oraz testy infrastrukturalne.

**Dlaczego:** Bootstrapowy S3 + DynamoDB lock to prerequisite dla zdalnego stanu Terraform – bez tego każdy developer ma lokalny state i nie ma możliwości współpracy ani auditu historii zmian.

**Kompromisy:** Backend Terraform ma hardkodowany region (`eu-central-1`) w `backend.tf.example` – dla PoC akceptowalne; produkcja wymagałaby parametryzacji przez `-backend-config`. Terraform w PATH jest ustawiany przez `tests/conftest.py` z hardkodowaną ścieżką WinGet – działa na tym środowisku deweloperskim, w CI wymagany jawny krok instalacji.

**Testy:** 4 testy infra (`terraform init -backend=false` + `terraform validate` + `terraform fmt -check` dla bootstrap i main). Wszystkie zielone. Czas wykonania: ~18s.

## Step 02 – IAM roles + polityki ABAC

**Co zrobiono:** Utworzono dwie role IAM: `iam-gateway-dev-lambda-exec` (zakładana przez Lambda, z dostępem do CloudWatch Logs, DynamoDB project tables i prawem do `sts:AssumeRole` + `sts:TagSession` na roli Bedrock) oraz `iam-gateway-dev-bedrock-scoped` (zakładana przez Lambdę przez STS, z uprawnieniami Bedrock Retrieve/InvokeModel). Obie role wdrożone przez Terraform i zweryfikowane przez boto3.

**Dlaczego:** Podwójna rola (Lambda exec → STS → Bedrock scoped) to fundament ABAC: Lambda musi jawnie przekazać session tags `department` i `clearance_level` przy AssumeRole – bez nich polityka Bedrock scoped blokuje dostęp. Izolacja nie zależy od poprawności kodu aplikacji, lecz od AWS IAM.

**Kompromisy:** Hierarchiczna clearance (`NumericLessThanEquals`) nie jest egzekwowana na poziomie IAM – Bedrock nie wspiera resource-tag conditions na operacjach Retrieve. Egzekwacja clearance hierarchy przeniesiona do warstwy aplikacyjnej (metadataFilter, Phase 05). Udokumentowane jako decyzja architektoniczna w `SecAnalysis.md`.

**Testy:** 7 testów (2 lokalne: validate + fmt; 5 post-apply boto3: istnienie ról, polityki, trust principal, Null condition na session tags). Fix: `get_role_policy` zwraca dict, nie string – usunięto zbędny `json.loads()`. Wszystkie zielone.

## Step 03 – DynamoDB tables

**Co zrobiono:** Utworzono 3 tabele DynamoDB: `sessions` (hash: `session_id`, GSI na `user_id`, TTL, SSE), `conversation-history` (hash: `session_id`, sort: `turn_index`, TTL) oraz `revoked-tokens` (hash: `jti`, TTL). Wszystkie PAY_PER_REQUEST, szyfrowanie AWS-owned keys.

**Dlaczego:** Trzy osobne tabele odpowiadają trzem rozłącznym wzorcom dostępu: zarządzanie sesjami (lookup po session_id i user_id), historia konwersacji (query po session_id DESC z limitem 5), revokacja tokenów (point lookup po jti przy każdym requeście). TTL na `expires_at` zapewnia automatyczne czyszczenie bez dodatkowego kodu.

**Kompromisy:** SSE używa AWS-owned keys (domyślne, zero koszt) zamiast customer-managed KMS. Dla PoC akceptowalne; produkcja wymagałaby CMK + CloudTrail visibility na odszyfrowaniach. PITR (point-in-time recovery) wyłączone – włączyć przed prod.

**Testy:** 13 testów (2 lokalne: validate + fmt; 11 post-apply boto3: istnienie, key schema, TTL attribute+status, GSI, SSE dla każdej tabeli). Wszystkie zielone.

## Step 04 – S3 bucket dla Knowledge Base

**Co zrobiono:** Utworzono bucket S3 `iam-gateway-dev-knowledge-base-{account_id}` z: wersjonowaniem (Enabled), szyfrowaniem SSE-KMS z AWS-managed key + `bucket_key_enabled=true` (redukcja kosztów KMS API), pełnym blokowaniem dostępu publicznego (4/4 flagi), polityką bucket DenyNonTLS. Nazwa zawiera account_id dla globalnej unikalności.

**Dlaczego:** Bucket jest źródłem danych dla Bedrock Knowledge Base (Phase 05). Wersjonowanie umożliwia rollback dokumentów po błędnym tagu metadata. SSE-KMS z bucket key to najlepsza praktyka kosztowa przy dużej liczbie obiektów. DenyNonTLS jako hard control – nawet pomyłkowe nieautoryzowane wywołanie HTTP zostaje zablokowane przez AWS zanim dotrze do kodu.

**Kompromisy:** Brak lifecycle policy (usuwanie starych wersji) – dla PoC bez znaczenia, na prod konieczne dla kontroli kosztów. Bedrock service principal (`bedrock.amazonaws.com`) zostanie dodany do bucket policy w Phase 05, gdy znamy ARN KnowledgeBase.

**Testy:** 7 testów (2 lokalne: validate + fmt; 5 post-apply boto3: istnienie bucketu, versioning, SSE algo + bucket key, public access block 4/4, DenyNonTLS w policy). Wszystkie zielone.

## Step 05 – Cognito User Pool z Groups

**Co zrobiono:** Utworzono Cognito User Pool (`iam-gateway-dev-user-pool`) z logowaniem przez email, zakazem self-registration (admin-only), polityką haseł (8 znaków, upper+lower+liczby). Dodano publiczny App Client bez secret (CLI + przyszły Android) z `ALLOW_USER_PASSWORD_AUTH`. Wygenerowano 25 grup ABAC (`dept_{dept}_cl_{level}`) przez `for_each` na produkcie kartezjańskim 5 działów × 5 clearance levels.

**Dlaczego:** Cognito Groups zamiast custom attributes eliminuje ryzyko immutability (grupy są mutowalne, usuwalne). JWT zawiera claim `cognito:groups` – Lambda Authorizer parsuje go regexem `^dept_([a-z]+)_cl_(\d+)$` do wyciągnięcia department i clearance_level bez dodatkowych API calls. 25 grup pokrywa wszystkie możliwe kombinacje.

**Kompromisy:** `describe_user_pool` w nowym Cognito API nie zwraca pola `Status` – test poprawiony na weryfikację `Id`. App Client ma `access_token_validity=1h` – krótkie okno minimalizuje ryzyko przy kradzionym tokenie; JWT revocation table w DynamoDB obsługuje natychmiastową blokadę.

**Testy:** 9 testów (2 lokalne: validate + fmt; 7 post-apply boto3: istnienie puli, admin-only signup, email jako username, brak client secret, auth flows, wszystkie 25 grup, count grup). Wszystkie zielone.

---

## Podsumowanie fazy 01

**Co zostało zbudowane:** Kompletna infrastruktura bazowa AWS jako kod Terraform: state backend (S3+KMS+DynamoDB lock), dwie role IAM z ABAC session tags, trzy tabele DynamoDB (sessions, conversation history, revoked tokens) z TTL, bucket S3 dla Knowledge Base z SSE-KMS i DenyNonTLS, Cognito User Pool z 25 grupami ABAC.

**Jak działa:** Terraform zarządza całą infrastrukturą deterministycznie. Każdy zasób ma odpowiedni zestaw testów boto3 weryfikujących stan po `apply`. IAM jest skonfigurowane tak, że Lambda musi jawnie przekazać session tags przy AssumeRole – bez `department` i `clearance_level` polityka Bedrock scoped blokuje dostęp na poziomie AWS API, niezależnie od kodu aplikacji. Cognito Groups enkodują tożsamość użytkownika w formacie parsowanym przez Lambda Authorizer.

**Co można dalej rozwinąć:**
- CMK (customer-managed KMS key) dla DynamoDB i S3 zamiast AWS-owned/managed keys
- DynamoDB PITR (point-in-time recovery) przed przejściem na produkcję
- Lifecycle policy na S3 bucket (usuwanie starych wersji dokumentów)
- CloudTrail alerts przez EventBridge + SNS dla anomalii bezpieczeństwa (real-time, nie tylko log)

## Step 06 – CloudTrail + CloudWatch Logs

**Co zrobiono:** Skonfigurowano multi-region CloudTrail (`iam-gateway-dev-trail`) logujący do dedykowanego S3 bucket (SSE-KMS, DenyNonTLS, polityka AWSCloudTrailWrite z `SourceArn` condition) oraz CloudWatch Log Group z retencją 30 dni. Dodano dwa metric filters: `UnauthorizedApiCalls` (AccessDenied/UnauthorizedOperation) i `StsAssumeRoleCalls` (AssumeRole) w namespace `IamGateway/Security`.

**Dlaczego:** Każde wywołanie AWS API trafia do CloudTrail – to fundament forensics i compliance. Multi-region trail eliminuje blind spots przy potencjalnej eskalacji do innych regionów. Metric filters na `AccessDenied` i `AssumeRole` to bezpośredni sygnał o próbach naruszenia ABAC – bez nich incydent byłby wykryty tylko przez ręczne przeglądanie logów. Circular dependency bucket policy ↔ trail ARN rozwiązano przez konstrukcję ARN z lokalnych zmiennych.

**Kompromisy:** Retencja 30 dni wystarczy na PoC; produkcja wymaga 90+ dni (compliance). Brak alarmów SNS – metric filters generują metryki w CW, ale nie wysyłają powiadomień. Dane zdarzenia S3/DynamoDB (dodatkowy koszt) wyłączone – management events pokrywają ABAC audit trail.

**Testy:** 9 testów (2 lokalne: validate + fmt; 7 post-apply boto3: trail exists+logging, multi-region, log validation, CW integration, log group retention=30, oba metric filters). Fix: `terraform fmt` przed testem (wyrównanie kolumn w locals). Wszystkie zielone.
