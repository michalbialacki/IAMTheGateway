# Phase 02 WriteUp – Autentykacja i JWT

## Step 01 – Testowi użytkownicy Cognito

**Co zrobiono:** Utworzono skrypt `scripts/create_test_users.py` (idempotent, boto3, bez zależności od terraform CLI) tworzący 3 testowych użytkowników: alice (engineering/restricted cl=2), bob (legal/classified cl=1), eve (security/top_secret cl=4). Każdy przypisany do odpowiedniej grupy ABAC. Dodano `scripts/get_jwt.py` do logowania i zwracania tokenów – reużywany jako biblioteka przez testy. Fixture'y sesyjne w `tests/auth/conftest.py`.

**Dlaczego:** Testowi użytkownicy z różnymi clearance levels są niezbędni do weryfikacji izolacji ABAC w kolejnych krokach. Fixture'y sesyjne minimalizują liczbę wywołań Cognito Auth API w trakcie testów (tokeny pobierane raz na sesję pytest).

**Kompromisy:** Hasło `DevTest1234` hardkodowane w skrypcie – akceptowalne dla PoC, produkcja wymaga zarządzania hasłami przez Secrets Manager lub one-time setup. `admin_get_user` z `username_attributes=["email"]` zwraca wewnętrzny UUID jako `Username` – test poprawiony na weryfikację atrybutu `email` w `UserAttributes`.

**Testy:** 12 testów (3 × user exists via email attribute, 3 × correct group, 3 × can authenticate, 3 × access token contains cognito:groups). Wszystkie zielone.

## Step 02 – Lambda Authorizer

**Co zrobiono:** Napisano `lambda/authorizer/handler.py` – Lambda Authorizer weryfikujący JWT RS256 przez JWKS endpoint Cognito (cache module-level), sprawdzający revocation w DynamoDB `revoked_tokens`, parsujący grupę `dept_{dept}_cl_{level}` i zwracający IAM policy Allow z context ABAC (`user_id`, `department`, `clearance_level`, `jti`). Deploy przez `terraform/lambda_authorizer.tf` z osobną rolą IAM (least privilege: tylko `dynamodb:GetItem` na `revoked_tokens`). Skrypt `scripts/build_authorizer_layer.py` buduje Lambda Layer (PyJWT + cryptography) przez Docker dla Linux x86_64.

**Dlaczego:** Autorizer stanowi bramę bezpieczeństwa dla całego API Gateway – każde żądanie musi przejść przez weryfikację JWT przed dotarciem do logiki biznesowej. Osobna rola IAM (nie `lambda_exec`) minimalizuje zakres uprawnień zgodnie z zasadą least privilege.

**Kompromisy:** Lambda Layer (PyJWT + cryptography) wymaga Docker do zbudowania – `cryptography` ma C extensions wymagające Linux ABI, niedostępnych na Windows. Alternatywą byłby `python-jose[rsa]` (pure Python), ale PyJWT jest aktywniej maintainowany. Warstwa tworzona jest z `count = fileexists(...)` żeby `terraform validate` przechodziło bez layer.zip – `terraform plan` / `apply` wymagają wcześniejszego uruchomienia `scripts/build_authorizer_layer.py`.

**Testy:** 9 unit testów bez AWS (pełny mock JWKS + DynamoDB, RSA key pair generowane w fixture). Przypadki: valid token → Allow + context, revoked → Deny, missing Bearer, empty header, expired, wrong token_use, grupy z prefiksem, brak grupy dept. Łącznie: 70 testów zielonych (poprzednio 61).

## Step 03 – API Gateway REST

**Co zrobiono:** Utworzono `terraform/api_gateway.tf` z REST API (REGIONAL), Lambda Authorizer TOKEN type z `authorizer_result_ttl_in_seconds=0` (revocation natychmiastowe), zasobem `/chat` + metodą POST (CUSTOM auth), integracją MOCK jako placeholder (Phase 03 zastąpi ją Lambdą), deployment + stage `prod`, throttling (`*/*`: 100 RPS / 50 burst). Osobna rola IAM dla API Gateway → Lambda Authorizer invocation. Testy infra w `tests/infra/test_step07_api_gateway.py`.

**Dlaczego:** TTL=0 w authorizerze jest kluczowe dla revocation: przy TTL>0 unieważniony token działałby jeszcze przez czas cache'owania. MOCK integration pozwala zadeplojować i przetestować cały auth flow bez gotowego backendu.

**Kompromisy:** TTL=0 = wywołanie authorizera przy każdym requeście → wyższy koszt i latencja (~10-50ms). Dla PoC akceptowalne; produkcja mogłaby używać TTL=30s z akceptowalnym oknem opóźnienia revocation.

**Testy:** `terraform validate` + `terraform fmt` (lokalne, zawsze zielone). 6 post-apply testów (REST API, authorizer config TTL=0, POST /chat z CUSTOM auth, stage throttling) – skipped do czasu `terraform apply`. Po apply łączna liczba testów: 70 + 6 = 76.

## Step 04 – JWT Revocation Endpoint

**Co zrobiono:** Napisano `lambda/revoke/handler.py` – endpoint admin do wpisywania `jti` do DynamoDB `revoked_tokens`. ABAC guard: `clearance_level >= 3` (secret/top_secret) z JWT context dostarczonego przez authorizer. Endpoint `/revoke` POST w API Gateway z `AWS_PROXY` integration. Osobna rola IAM (tylko `dynamodb:PutItem`). Naprawiono izolację testów: zastąpiono `sys.path.insert + import handler` przez `importlib.util.spec_from_file_location` z unikalnymi nazwami modułów (`authorizer_handler` / `revoke_handler`) – eliminuje kolizję nazw w pełnej suicie.

**Dlaczego:** Revocation endpoint domyka pętlę bezpieczeństwa: Lambda Authorizer sprawdza DynamoDB przy każdym requeście, revoke endpoint pozwala wpisać `jti` do tablicy. Bez endpointu revocation byłoby niemożliwe bez bezpośredniego dostępu do DynamoDB.

**Kompromisy:** Granular clearance check (`>= 3`) zamiast `department == security` – clearance jest numeryczny i łatwiej testowalny. Produkcja mogłaby wymagać obu warunków (AND). `expires_at` jest opcjonalny – brak TTL oznacza że wpis nigdy nie wygasa, co jest bezpieczniejszym defaultem (token zawsze zablokowany).

**Testy:** 10 unit testów (4 happy path + 3 authz + 3 validation). Naprawiona izolacja testów w całej suicie. Łącznie: 88 testów zielonych.

## Step 05 – Testy end-to-end JWT

**Co zrobiono:** Napisano `tests/auth/test_step05_e2e_jwt.py` – 6 E2E testów przez HTTP na live API Gateway. Testy: (1) valid access token → 200, (2) brak nagłówka → 401, (3) tampered JWT (obcy klucz/kid) → 401, (4) ID token zamiast access token (token_use='id') → 401, (5) revocation flow (fresh token → 200 → POST /revoke → 403), (6) cross-tenant forged JWT → 401. Skip gdy brak AWS credentials lub API nie zadeplojowane. Dodano `requests>=2.32.0` do dev deps.

**Dlaczego:** Testy jednostkowe weryfikują logikę handlera w izolacji; E2E testy weryfikują pełną integrację: API Gateway routing → Lambda Authorizer → DynamoDB → response codes.

**Kompromisy:** Revocation test loguje się do Cognito ponownie (fresh token) żeby nie niszczyć session-scoped fixtures używanych przez inne testy. `test_cross_tenant_forged_jwt` pokrywa ten sam scenariusz co `test_tampered_jwt` z innym intent description – decyzja: zostawić oba dla jasności dokumentacji security requirementów.

**Testy:** 6 E2E testów wszystkie zielone na live AWS. Łącznie: 94 testy zielone.

---
## Podsumowanie fazy 02

**Co zostało zbudowane:** Kompletna warstwa autentykacji i autoryzacji opartа о Cognito JWT + API Gateway Lambda Authorizer. Użytkownicy logują się przez Cognito, otrzymują access token RS256, który jest weryfikowany przy każdym requeście przez Lambda Authorizer. Dodano endpoint administracyjny `/revoke` do unieważniania tokenów z ochroną ABAC (clearance ≥ 3).

**Jak działa:** Klient wysyła `Authorization: Bearer {jwt}` do API Gateway. API Gateway wywołuje Lambda Authorizer (TOKEN type, TTL=0), który: (1) pobiera JWKS z Cognito, weryfikuje podpis RS256, (2) sprawdza `jti` w DynamoDB `revoked_tokens`, (3) parsuje grupy ABAC (`dept_{dept}_cl_{level}`), (4) zwraca IAM policy Allow z context: user_id, department, clearance_level, jti. Nieważne tokeny generują 401; unieważnione 403.

**Co można dalej rozwinąć:** (1) Lambda Authorizer cache warming – prefetch JWKS przy cold start. (2) Bulk revocation endpoint (revoke all tokens for a user_id). (3) Audit log revocations do CloudTrail / osobnej tabeli z metadanymi (kto unieważnił, kiedy, powód).
