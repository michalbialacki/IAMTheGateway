# Phase 03 WriteUp – IAM Session Generation (STS / AssumeRole)

## Step 01 – IAM Role docelowa z ABAC + Lambda STS

**Co zrobiono:** Wzmocniono trust policy roli `bedrock_scoped` w `terraform/iam.tf` dodając warunek `Null { aws:RequestTag/department = "false", aws:RequestTag/clearance_level = "false" }` – rola może być objęta tylko gdy oba tagi są dostarczone w samym wywołaniu `sts:AssumeRole`. Stworzono `lambda/sts/handler.py`: Lambda czyta `requestContext.authorizer` (user_id, department, clearance_level, jti) ustawiony przez JWT Authorizer, sanityzuje `RoleSessionName` (regex `[^a-zA-Z0-9=,+@._-]` → `_`, max 64 znaki), wywołuje `sts.assume_role()` z tagami i zwraca tymczasowe credentials STS lub 403. Stworzono `terraform/lambda_sts.tf` z deploymentem Lambda (runtime python3.12, timeout 15s, rola `lambda_exec` która już ma uprawnienia `sts:AssumeRole` na `bedrock_scoped`).

**Dlaczego:** ABAC w dwóch warstwach: trust policy (kto może objąć rolę i z jakimi tagami) + permissions policy (jakie akcje dozwolone gdy tagi obecne). Bez `aws:RequestTag` w trust policy rola mogłaby być objęta bez tagów, a ABAC byłby egzekwowany tylko w permissions – to pojedynczy punkt kontroli. Dwie warstwy dają defense-in-depth.

**Kompromisy:** `DurationSeconds=900` (minimum STS). Cache w Step 03 zredukuje liczbę wywołań assume_role. Aktualnie Lambda zastępuje MOCK integration w API GW dopiero w Step 02 – celowo, żeby każdy etap był atomową jednostką.

**Testy:** 19 unit testów lokalnych (wszystkie zielone): sanitize_session_name (4), handler 403-paths (6), handler success-path (7), terraform validate (2). 3 post-apply testy (oczekują `terraform apply`): Lambda exists, Lambda env var, trust policy conditions. Wcześniejsze 94 testy nadal zielone.

## Step 02 – Lambda STS z session policy + wiring do API Gateway

**Co zrobiono:** Dodano funkcję `_build_session_policy(department)` w `lambda/sts/handler.py` – generuje inline session policy ograniczającą sesję do jednego działu (`StringEquals aws:PrincipalTag/department`). Policy przekazywana jako `Policy=` w wywołaniu `sts.assume_role()`. W `terraform/api_gateway.tf` zastąpiono MOCK integration integracją `AWS_PROXY` → `aws_lambda_function.sts_session`; usunięto `aws_api_gateway_integration_response` (zbędna dla PROXY); zaktualizowano deployment trigger i `depends_on`.

**Dlaczego:** Session policy tworzy per-user scope: efektywne uprawnienia sesji = intersection(rola + policy). Nawet jeśli bugi w tagu-setting błędnie połączyłyby działy, session policy scoped do konkretnego działu blokuje cross-department dostęp niezależnie. AWS_PROXY eliminuje MOCK i daje realny przepływ JWT context → STS → credentials.

**Kompromisy:** Session policy nie ogranicza clearance_level na poziomie IAM (Bedrock nie wspiera numeric conditions na Retrieve). Hierarchia clearance egzekwowana przez metadataFilter w Phase 05.

**Testy:** 12 lokalnych zielone: _build_session_policy (6), handler z Policy kwarg (4), terraform validate/fmt (2). 2 post-apply (zielone po apply): typ integracji == AWS_PROXY, URI zawiera nazwę Lambda.

## Step 03 – STS credentials cache

**Co zrobiono:** Dodano module-level `_sts_cache: dict[str, dict]` w `lambda/sts/handler.py`, keyed `{user_id}:{clearance_level}`. Funkcje pomocnicze `_cache_key`, `_get_cached` (zwraca None jeśli pusty, wygasły lub w buforze 60s), `_put_cached`. W `lambda_handler`: przed `assume_role()` sprawdzenie cache – hit zwraca cached credentials bez wywołania STS; miss wywołuje assume_role, formatuje credentials i zapisuje do cache z `raw["Expiration"]` jako expiry. **Hotfix (2026-06-13):** klucz rozszerzony do `{user_id}:{department}:{clearance_level}`; dodano `_user_last_dept` i `_evict_user(user_id)` – przy wykryciu zmiany działu wszystkie wpisy danego usera są natychmiast usuwane z cache przed lookup.

**Dlaczego:** `sts:AssumeRole` kosztuje (API call, latencja ~200ms). Przy 900s DurationSeconds i 60s buforze, ten sam user może być obsłużony ~14 razy z cache przed odświeżeniem. Cache żyje przez czas życia kontenera Lambda – bezpieczne, bo Lambda jest single-threaded (brak race conditions na dict). `department` w kluczu eliminuje ryzyko zwrotu stale credentials po zmianie działu między warm invocations; proaktywna ewakuacja usuwa stare wpisy natychmiast, nie po ich naturalnym wygaśnięciu (max 840s).

**Kompromisy:** `_evict_user` iteruje po całym `_sts_cache` przy każdej zmianie działu – pomijalne przy typowym rozmiarze cache (kilka-kilkanaście wpisów per container). Eviction wykrywa zmianę działu dopiero przy pierwszym żądaniu po zmianie (nie real-time); TTL 900s wyznacza górną granicę dla nie-warm containers.

**Testy:** 17 unit testów (wszystkie zielone): cache_key (4), get/put helpers (6), handler cache behaviour (7). Brak post-apply testów (cache in-memory). Pełny suite: 147/147. Po hotfixie: 24 testy (dodano `TestDepartmentEviction` 4 testy + 3 nowe w istniejących klasach); pełny suite: 405/405.

## Step 04 – Bedrock boto3 client z STS credentials (pełny chat proxy)

**Co zrobiono:** Refaktoryzacja `lambda/sts/handler.py` na pełny chat proxy: (1) parsowanie i walidacja `body.message` (400 jeśli brak/pusty), (2) wyciągnięcie ABAC context z authorizer, (3) `_get_credentials()` – cache lub assume_role, (4) `_invoke_bedrock(message, credentials)` tworzy `bedrock-runtime` client z STS credentials i wywołuje `invoke_model` dla Titan Text, (5) zwraca `{response: text}` bez credentials. Dodano `BEDROCK_MODEL_ID` do env vars w `terraform/lambda_sts.tf`. Zaktualizowano testy Step 01-03 (dodanie body do eventów, wymiana globalna patcha boto3 na `patch.object(mod, "_invoke_bedrock")`).

**Dlaczego:** Credentials STS nigdy nie opuszczają Lambdy – klient widzi tylko odpowiedź Bedrock. Phase 04 (sandwich method) wymaga server-side przetwarzania, które jest teraz możliwe ponieważ Lambda kontroluje pełny flow. Wzorzec server-side proxy (zamiast TVM) spełnia wymogi compliance enterprise (SOC2/ISO27001 – brak credentials w tranzycie).

**Kompromisy:** Model format Titan Text hardcoded w `_invoke_bedrock`. Dodanie wsparcia dla innych modeli (Claude 3 w Phase 10) wymaga warstwy abstrakcji – odłożono na later phase zgodnie z zasadą YAGNI.

**Testy:** 15 unit testów (wszystkie zielone): _invoke_bedrock (4), walidacja body (5), success path (5), błąd Bedrock (1). Pełny suite po refaktoryzacji: 162/162.

## Step 05 – Testy: izolacja scope, cache safety, deny poza zakresem

**Co zrobiono:** Stworzono `tests/sts/test_step05_scope_isolation.py` (16 testów). Unit testy weryfikują: (1) tagi STS zawsze pochodzą z JWT authorizer context, nie z request body (`test_department_from_authorizer_not_body`, `test_clearance_from_authorizer_not_body`), (2) clearance nie jest podnoszone – cl=1 user otrzymuje tag `"1"` a nie `"3"/"4"`, (3) session policy scoped do departmentu użytkownika – inne działy mają inne policies, (4) odrębne cache slots per clearance level – cl=1 i cl=3 wywołują assume_role dwa razy, cl=2+cl=2 raz. Testy post-apply (wymagają AWS): BEDROCK_MODEL_ID w env Lambda, invocation bez body → 400, invocation bez context → 403, trust policy ma Null conditions na oba tagi. Naprawiono dwa e2e testy Phase 02 (`tests/auth/test_step05_e2e_jwt.py`): testy auth teraz wysyłają `json={"message": "ping"}` (wymagane przez chat proxy z Step 04) i akceptują 200 lub 502 jako "auth passed" (502 = Bedrock ResourceNotFoundException gdy model access nie jest włączony w koncie).

**Dlaczego:** Izolacja scope jest krytyczną gwarancją bezpieczeństwa ABAC – user z niskim clearance nie może eskalować uprawnień przez modyfikację request body. Defense-in-depth: kod odczytuje tagi z authorizer (wydany przez JWT Authorizer Lambda, podpisany przez Cognito) a nie z request body (kontrolowanego przez klienta).

**Kompromisy:** Testy e2e auth akceptują 502 ("Bedrock nie dostępny") obok 200 – osłabia pełne end-to-end potwierdzenie, ale testy te testują warstwę auth (Phase 02), nie dostępność Bedrock (Phase 03+). W produkcji należy włączyć model access w Bedrock console.

**Testy:** 16 testów zielone: 12 unit, 4 post-apply (AWS). Pełny suite: 178/178 zielone.

---
## Podsumowanie fazy 03

**Co zostało zbudowane:** System generowania sesji IAM z pełnym ABAC – Lambda STS jako chat proxy przyjmuje zapytania od autoryzowanych użytkowników, przyjmuje rolę `bedrock_scoped` z tagami `department` i `clearance_level` z JWT, stosuje inline session policy ograniczającą scope do działu użytkownika, wywołuje Bedrock z tymi credentials i zwraca odpowiedź. Credentials STS nigdy nie opuszczają Lambdy.

**Jak działa:** (1) API Gateway + JWT Authorizer walidują token Cognito i ekstraktują ABAC context; (2) Lambda STS sprawdza moduł-level cache (`user_id:department:clearance_level`, TTL=expiry-60s; zmiana działu → natychmiastowa ewakuacja starych wpisów); (3) przy cache miss: `sts.assume_role()` z tagami sesji + inline session policy → credentials w pamięci; (4) `bedrock-runtime` client z STS credentials wywołuje Titan Text; (5) odpowiedź trafia do klienta bez credentials. Trust policy wymaga `aws:RequestTag` dla obu tagów (defense-in-depth layer 1); permissions policy wymaga `aws:PrincipalTag` (layer 2).

**Co można dalej rozwinąć:** (1) Włączenie Bedrock model access w koncie AWS (Bedrock console → Model Access) – wymagane do pełnego end-to-end działania; (2) Phase 05: Bedrock Knowledge Base z metadataFilter dla izolacji dokumentów per department/clearance; (3) Phase 06: historia konwersacji w DynamoDB z izolacją per sesja.
