# IAM The Gateway

**Multitenant GenAI Gateway z izolacją IAM i ABAC**

Proof-of-concept bramki dostępu do Amazon Bedrock z wielowarstwową izolacją między dzierżawcami. Każde żądanie do modelu przechodzi przez weryfikację JWT, generowanie tymczasowych poświadczeń STS z tagami sesji ABAC, filtrowanie wiedzy przez metadane dokumentów oraz sanitizację wejścia (PII + prompt injection). Projekt obejmuje pełny stack: infrastruktura Terraform, Lambda backnd, Python CLI, Knowledge Base RAG i kompletna suita testów z trzema poziomami kosztowymi.

---

## Architektura

```
Klient (Python CLI)
    │  Authorization: Bearer {access_token JWT}
    │  client-side regex scan (defense-in-depth)
    ▼
API Gateway REST ──► Lambda Authorizer
    │                    │ weryfikacja RS256 + JWKS Cognito
    │                    │ sprawdzenie jti w DynamoDB (revocation)
    │                    └─ parsowanie grup ABAC → department / clearance_level
    ▼
Lambda Chat Handler
    │  1. Server-side sanitize (PII redaction + injection detection)
    │  2. Sandwich method (prompt otwierający + input + prompt zamykający)
    │  3. sts.assume_role() z session tags (department, clearance_level)
    │  4. Wstrzyknięcie historii konwersacji (DynamoDB, ostatnie 3-5 wymian)
    ▼
Bedrock RetrieveAndGenerate (eu.amazon.nova-lite-v1:0)
    │  metadataFilter: department == X AND clearance_level <= N
    ▼
OpenSearch Serverless (AOSS) ◄── S3 Knowledge Base
                                  dokumenty + sidecar .metadata.json
```

Kluczowe właściwości:
- **Zero hardkodowanych uprawnień** – dostęp wynika wyłącznie z JWT claims i ABAC conditions na roli IAM
- **Revocation w czasie rzeczywistym** – `jti` sprawdzany przy każdym wywołaniu (TTL=0)
- **Izolacja na poziomie danych** – Bedrock filtruje dokumenty przez `clearance_level` i `department` zanim zwróci wynik
- **Defense-in-depth** – sanitizacja po stronie klienta i serwera; serwer traktuje klienta jako untrusted

---

## Stack

| Warstwa | Technologia |
|---------|-------------|
| Infrastruktura | Terraform ≥ 1.7, AWS Provider ~5.50 |
| Autentykacja | Amazon Cognito User Pools + Groups |
| API | API Gateway REST (REGIONAL), Lambda Authorizer TOKEN |
| Autoryzacja | IAM ABAC – session tags przez STS AssumeRole |
| Logika biznesowa | Python 3.12 Lambda |
| Baza danych | DynamoDB (sessions, conversation_history, revoked_tokens TTL) |
| Wiedza (RAG) | Amazon Bedrock Knowledge Base + AOSS + S3 (SSE-KMS) |
| Model generacji | `eu.amazon.nova-lite-v1:0` (cross-region inference profile) |
| Model embeddingów | `amazon.titan-embed-text-v2:0` |
| Klient | Python CLI (Cognito login, keyring, HTTP, client-side scan) |
| Obserwability | CloudTrail (multi-region) + CloudWatch Logs + metric filters |
| CI/CD | GitHub Actions (offline tier + ruff + terraform validate + gitleaks) |
| Testy | Pytest, trójwarstwowe markery: offline / aws / aoss |

---

## Model uprawnień ABAC

Grupy Cognito mają format `dept_{department}_cl_{clearance_level}`:

| Clearance | Wartość | Przykładowa grupa |
|-----------|---------|-------------------|
| unclassified | 0 | `dept_hr_cl_0` |
| classified | 1 | `dept_legal_cl_1` |
| restricted | 2 | `dept_engineering_cl_2` |
| secret | 3 | `dept_finance_cl_3` |
| top_secret | 4 | `dept_security_cl_4` |

Lambda Authorizer parsuje grupę i przekazuje do API Gateway context. Lambda Chat Handler przekazuje tagi do STS, które zostają session tags roli `bedrock_scoped`. Bedrock KB filtruje dokumenty przez te same tagi — izolacja jest egzekwowana dwa razy niezależnie.

---

## Struktura projektu

```
IAMTheGateway/
├── terraform/
│   ├── bootstrap/              # Zdalny backend S3 + DynamoDB lock (jednorazowo)
│   ├── main.tf                 # Provider, locals, tagi domyślne
│   ├── variables.tf            # Region, project_name, environment, model
│   ├── iam.tf                  # Role: lambda_exec + bedrock_scoped (ABAC + GetInferenceProfile)
│   ├── dynamodb.tf             # sessions, conversation_history, revoked_tokens (TTL)
│   ├── s3.tf                   # Knowledge Base bucket (SSE-KMS, deny-non-TLS)
│   ├── cognito.tf              # User Pool + ABAC Groups + App Client
│   ├── cloudtrail.tf           # CloudTrail + CloudWatch metric filters
│   ├── lambda_authorizer.tf    # Lambda Authorizer TOKEN
│   ├── lambda_revoke.tf        # Lambda Revoke + endpoint /revoke
│   ├── lambda_sts.tf           # Lambda Chat Handler (STS + Bedrock + DynamoDB)
│   ├── bedrock_kb.tf           # AOSS collection + KB + data source
│   ├── api_gateway.tf          # REST API, /chat POST, throttling, stage prod
│   └── outputs.tf
│
├── lambda/
│   ├── authorizer/handler.py   # JWT verify (RS256 + JWKS), revocation, ABAC context
│   ├── revoke/handler.py       # Admin: zapisuje jti do revoked_tokens
│   └── sts/handler.py          # Chat: sanitize → STS → sandwich → Bedrock R&G → DynamoDB
│
├── lambda/sanitizer/
│   ├── patterns.py             # Regex: PII (PESEL, email, telefon, IP), injection, jailbreak
│   ├── sanitizer.py            # ScanResult: is_clean, redacted_text, findings
│   ├── sandwich.py             # build_sandwich_prompt() z historią konwersacji
│   └── policy.py               # ClearancePolicy: max_tokens, temperature, topic gate
│
├── cli/
│   ├── auth.py                 # Cognito InitiateAuth → AuthTokens (keyring storage)
│   ├── gateway.py              # send_message() HTTPS → API GW z access_token
│   └── main.py                 # CLI loop: input → scan → send → print
│
├── scripts/
│   ├── create_kb_index.py      # Tworzy indeks AOSS (MUSI być przed terraform apply KB)
│   ├── ingest_docs.py          # Upload 15 docs (eng/legal/sec × cl0-4) + ingestion job
│   ├── create_test_users.py    # Tworzy alice/bob/eve w Cognito
│   ├── stop_costs.ps1          # Niszczy AOSS + KB (jedyny zasób ~$11/dzień)
│   └── build_authorizer_layer.py  # Buduje layer.zip przez Docker (Linux wheels)
│
├── tests/
│   ├── infra/                  # terraform validate + boto3 post-apply (marker: aws)
│   ├── auth/                   # JWT unit + E2E (marker: aws)
│   ├── sts/                    # STS cache, session policy, ABAC isolation
│   ├── sanitizer/              # Regex patterns, lambda sanitize, sandwich, policy
│   ├── kb/                     # Terraform KB, ingest, metadataFilter, ABAC (marker: aoss)
│   ├── conversation/           # SessionId, historia, context injection
│   ├── cli/                    # Auth, storage, scan, gateway, CLI loop, integration
│   └── e2e/                    # Full-flow (3 users × 7 tests) + security (marker: aoss)
│
├── .github/workflows/ci.yml    # CI: lint-test + terraform + gitleaks (offline tylko)
└── STEPS/
    ├── PhasePlan.md            # Plan wszystkich faz + decyzje architektoniczne
    ├── Phase0X_WriteUp.md      # Dokumentacja ukończonych faz (Phase 01–08)
    ├── KnownLimitations.md     # Świadome ograniczenia PoC (KL-01 … KL-08)
    └── HANDOFF.md              # Stan projektu na koniec sesji
```

---

## Pierwsze uruchomienie

### Wymagania

- Python 3.12+, [uv](https://github.com/astral-sh/uv)
- Terraform ≥ 1.7
- Docker Desktop (do budowania Lambda Layer – jednorazowo)
- AWS credentials (IAM, Lambda, Cognito, APIGW, DynamoDB, S3, Bedrock, AOSS, CloudTrail)

### 1. Backend Terraform (jednorazowo)

```powershell
$tf = "...\terraform.exe"
Set-Location terraform\bootstrap
& $tf init; & $tf apply
```

Skopiuj `terraform/backend.tf.example` → `terraform/backend.tf` i uzupełnij outputami.

### 2. Deploy infrastruktury (AOSS — sekwencja 3-krokowa)

AOSS wymaga ręcznego stworzenia indeksu przed KB — nie można tego zrobić w jednym `apply`:

```powershell
$uv = "...\uv.exe"
Set-Location terraform

# Krok 1: tylko kolekcja AOSS
& $tf apply "-target=aws_opensearchserverless_collection.kb"

# Krok 2: indeks (kolekcja musi być ACTIVE)
Set-Location ..; & $uv run python scripts\create_kb_index.py; Set-Location terraform

# Krok 3: reszta (KB + Lambda + APIGW)
& $tf apply
```

### 3. Zaindeksuj dokumenty

```powershell
& $uv run python scripts\ingest_docs.py
```

### 4. Utwórz użytkowników testowych

```powershell
& $uv run python scripts\create_test_users.py
```

Tworzy: `alice@test.local` (engineering/cl=2), `bob@test.local` (legal/cl=1), `eve@test.local` (security/cl=4).

### 5. Uruchom CLI

```powershell
& $uv run python -m cli.main
```

### Zatrzymanie kosztów po sesji

AOSS kosztuje ~$11.52/dzień gdy jest aktywne. Zniszcz po każdej sesji:

```powershell
.\scripts\stop_costs.ps1
```

---

## Uruchamianie testów

Testy podzielone na trzy poziomy kosztowe:

```powershell
# Tier 1: offline — bezpłatne, ciągłe (599 testów)
uv run pytest -m "not aws and not aoss" -q

# Tier 2: aws — ~$0, weryfikuje live auth/IAM/infra (56 testów)
$env:COGNITO_USER_POOL_ID = "..."
$env:CHAT_API_URL         = "..."
uv run pytest -m "aws and not aoss" -q

# Tier 3: aoss — płatne okno AOSS (48 testów, ~$1/2h)
# Wymaga odtworzenia infra + ingest_docs.py
uv run pytest -m "aoss" -q
# Po testach: .\scripts\stop_costs.ps1
```

Wymagane env vars dla tierów aws/aoss: `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `CHAT_API_URL`, `AWS_REGION`, `INT_TEST_USER_ENG`, `INT_TEST_PASS_ENG`, `INT_TEST_USER_LEGAL`, `INT_TEST_PASS_LEGAL`, `INT_TEST_USER_SEC`, `INT_TEST_PASS_SEC`.

---

## Plan faz

| Faza | Zakres | Status |
|------|--------|--------|
| **Phase 01** | Infrastruktura bazowa: Terraform, IAM ABAC, DynamoDB, S3, Cognito, CloudTrail | ✅ |
| **Phase 02** | Autentykacja i JWT: Lambda Authorizer, API Gateway REST, revocation endpoint | ✅ |
| **Phase 03** | IAM Session Generation: STS AssumeRole z ABAC session tags, cache credentials | ✅ |
| **Phase 04** | Input Security: sanitizacja PII + prompt injection, sandwich method, limity per clearance | ✅ |
| **Phase 05** | Bedrock Knowledge Base z ABAC: AOSS, metadataFilter (department + clearance_level) | ✅ |
| **Phase 06** | Conversation Management: DynamoDB, historia 3–5 ostatnich wymian per sesja | ✅ |
| **Phase 07** | Python CLI Client: Cognito login, keyring, client-side sanitize, HTTPS do APIGW | ✅ |
| **Phase 08** | CI/CD + E2E Tests: GitHub Actions, security audit, trójwarstwowe markery testów | ✅ |

**Future scope (poza PoC):**
- Phase 09 – KMP Android Client (Cognito Amplify, Android Keystore, Jetpack Compose)
- Phase 10 – Output sanitization (Bedrock Guardrails lub LLM-judge jako post-processor)
- Phase 11 – Semantic Cache z injection detection dla historii konwersacji

---

## Bezpieczeństwo

- **Żadnych sekretów w kodzie** – klucze przez AWS Secrets Manager i `.env` (gitignored)
- **Stan Terraform** – S3 z SSE-KMS + DynamoDB lock
- **Lambda Authorizer TTL=0** – revocation działa natychmiastowo (brak cache'owania w APIGW)
- **Least privilege IAM** – każda Lambda ma osobną rolę; `bedrock_scoped` wymaga session tags na każdym wywołaniu (`aws:PrincipalTag` Null=false) jako defense-in-depth
- **Deny non-TLS** – S3 bucket policy odrzuca żądania bez `aws:SecureTransport`
- **CloudTrail** – wszystkie regiony, log file validation, metric filters dla `UnauthorizedAccess` i `AssumeRole`
- **gitleaks** – skan secrets w CI na pełnej historii gita

Szczegóły: `STEPS/SecAnalysis.md`, `STEPS/KnownLimitations.md`.

---

## Licencja

Projekt edukacyjny / PoC. Nie przeznaczony do użycia produkcyjnego bez audytu bezpieczeństwa.
