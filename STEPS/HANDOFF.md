# HANDOFF – 2026-06-13 (sesja wieczorna: Phase 08 Step 02b/02c/03/04/05 + naprawy)

---
## ⚠ KOSZTY – WYŁĄCZ PO SESJI (sekcja stała, nie usuwać)

**Jedyny zasób generujący stały koszt: Amazon OpenSearch Serverless (AOSS)**
- Stawka: 2 OCU × $0.24/OCU/h = **~$11.52/dzień (~$345/miesiąc)**
- Billing aktywny gdy AOSS collection istnieje w stanie ACTIVE
- Nie zatrzymuje się samo — musisz ręcznie zniszczyć kolekcję

**Stan na koniec tej sesji: AOSS ZNISZCZONE (`stop_costs.ps1` wykonany, billing $0).**

**Jak zatrzymać koszty (po każdej sesji roboczej):**
```powershell
.\scripts\stop_costs.ps1
```
Niszczy AOSS + KB; kaskadowo nosi też STS Lambdę + API GW (Lambda referuje `KNOWLEDGE_BASE_ID`).
Zostają: DynamoDB, S3, Cognito, CloudTrail, IAM. Wszystkie $0 idle.

**Jak przywrócić (sekwencja sprawdzona w tej sesji — UWAGA na pułapki):**
```powershell
$tf = "C:\Users\Michal\AppData\Local\Microsoft\WinGet\Packages\Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe\terraform.exe"
$uv = "C:\Users\Michal\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe"
cd terraform
# 1) Najpierw plan — zweryfikuj rozjazd stanu:
& $tf plan
# 2) Jeśli "ConflictException: collection already exists" przy apply → kolekcja żyje w AWS,
#    ale nie w stanie. Odczytaj aktualne id i zaimportuj:
#    aws opensearchserverless list-collections --query "collectionSummaries[?name=='iam-gateway-dev-kb'].id" --output text
#    & $tf import aws_opensearchserverless_collection.kb <ID>
# 3) Utwórz indeks (kolekcja musi być ACTIVE), POTEM apply (KB wymaga istniejącego indeksu!):
cd ..; & $uv run python scripts\create_kb_index.py; cd terraform
& $tf apply
# 4) Dane:
cd ..; & $uv run python scripts\ingest_docs.py
```
**WAŻNE:** `create_kb_index.py` MUSI być uruchomiony przed `apply` tworzącym KB — Bedrock przy AOSS NIE tworzy indeksu automatycznie (komentarz w `bedrock_kb.tf` jest błędny, potwierdzone empirycznie).

---

## Stan projektu
- Aktualna faza: **Phase 08 – CI/CD + End-to-End Tests**
- Ukończone etapy: Step 01 (markery) · 02 (e2e suite) · **02b** (remediacja offline, 123→0 fail) · **02c** (spójność tokenów/userów) · **03** (CI GitHub Actions) · **04** (security audit) · **05** (pełne uruchomienie live — z findingami)
- Status testów: **offline `-m "not aws and not aoss"` = 599 passed / 0 failed / 2 xfailed** (zielone). Tier `aws` (live) = 62 passed / 1 finding (patrz niżej). Tier `aoss` = **niezwalidowany z nowym modelem** (blocker naprawiony offline, czeka na okno).
- Infra: **WYŁĄCZONA** (AOSS zniszczone). Reszta (Lambda/APIGW/DynamoDB/S3/Cognito) też zniszczona kaskadowo przez stop_costs — odtwarzalna przez `apply`.
- Ruff: czysto (35→0). Terraform: validate Success, fmt czysto.

## Następny krok
**Walidacja tieru `aoss` z nowym modelem (Claude 3 Haiku) — wymaga PŁATNEGO okna AOSS.**
1. **Konsola → Bedrock → Model access → włącz „Claude 3 Haiku" (`anthropic.claude-3-haiku-20240307-v1:0`).** Bez tego RAG padnie.
2. Przywróć infra wg sekwencji z sekcji kosztów (plan → ew. import → create_kb_index → apply → ingest).
3. Env-vary (z `terraform output`): `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `CHAT_API_URL`, `AWS_REGION`, `INT_TEST_USER_ENG=alice@test.local`, `INT_TEST_PASS_ENG=DevTest1234`, `INT_TEST_USER_LEGAL=bob@test.local`, `INT_TEST_PASS_LEGAL=DevTest1234`. Userzy: `create_test_users.py`.
4. `uv run pytest -m "aws and not aoss"` (powinien być 0 findingów — regex injection naprawiony) → `uv run pytest -m "aoss"` (cel: zielone z Claude) → **`stop_costs.ps1`**.

## Otwarte kwestie
- **Blocker modelu (NAPRAWIONY offline, niewalidowany live):** `titan-text-express` nie istnieje w eu-central-1 jako model TEXT i nie wspiera RAG → 502 na każdym chacie. Zmieniono na `anthropic.claude-3-haiku-20240307-v1:0` (jedyny RAG+ON_DEMAND, in-region). Wymaga model access w konsoli + walidacji live.
- **Finding `aws` tier (NAPRAWIONY offline):** test injection `ignore all previous instructions` dostawał 502 zamiast 400 — regex sanitizera łapał tylko 1 kwalifikator. Rozszerzony + test regresyjny. Do potwierdzenia live (400) po redeployu.
- **CI nie odpalony realnie** — workflow `.github/workflows/ci.yml` dodany i zwalidowany lokalnie (YAML + komendy bazowe), ale nigdy nie biegł na GitHub (repo bez remote? do sprawdzenia). `terraform plan` świadomie poza CI (wymaga creds/stanu).
- **Pomysł na osobny projekt (cross-region):** bramka GenAI świadomie używająca cross-region inference profiles (Nova / nowsze Claude) — data-residency/failover per tenant. Poza zakresem IAM Gateway. Szczegóły w `Phase08_WriteUp.md` Step 05.
- **gitleaks rekomendowany w CI** (dodany jako job) — lokalnie niedostępny, skan regex w Step 04 nie wykrył sekretów.
- **Pre-existing xfail** (regex-only detection): polskie wzorce injection + Base64/ROT13 — zaadresowane w Phase 10 (semantyczna detekcja, poza PoC).

## Zmodyfikowane pliki (do graphify)
STEPS/HANDOFF.md STEPS/Phase08_WriteUp.md cli/auth.py cli/gateway.py cli/main.py lambda/sanitizer/patterns.py lambda/sts/handler.py pyproject.toml scripts/stop_costs.ps1 terraform/variables.tf .github/workflows/ci.yml tests/auth/test_step05_e2e_jwt.py tests/cli/test_step02_storage.py tests/cli/test_step03_scan.py tests/cli/test_step05_cli_loop.py tests/cli/test_step06_integration.py tests/conversation/test_step01_session_id.py tests/conversation/test_step02_history.py tests/conversation/test_step03_context_injection.py tests/infra/test_step02_iam.py tests/infra/test_step03_dynamodb.py tests/infra/test_step04_s3.py tests/infra/test_step05_cognito.py tests/infra/test_step06_cloudtrail.py tests/infra/test_step07_api_gateway.py tests/kb/test_step01_terraform_kb.py tests/kb/test_step02_ingest.py tests/kb/test_step04_retrieve_generate.py tests/kb/test_step05_abac_isolation.py tests/sanitizer/test_step01_patterns.py tests/sanitizer/test_step02_lambda_sanitize.py tests/sanitizer/test_step03_sandwich.py tests/sanitizer/test_step04_policy.py tests/sanitizer/test_step05_security.py tests/sts/test_step01_iam_abac.py tests/sts/test_step02_session_policy.py tests/sts/test_step03_cache.py tests/sts/test_step04_bedrock_client.py tests/sts/test_step05_scope_isolation.py tests/e2e/__init__.py tests/e2e/_helpers.py tests/e2e/conftest.py tests/e2e/test_step01_fullflow.py tests/e2e/test_step02_security.py
