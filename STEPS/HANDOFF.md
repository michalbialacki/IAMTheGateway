# HANDOFF – 2026-06-13 (aktualizacja: koniec sesji, Phase 07 zakończona)

---
## ⚠ KOSZTY – WYŁĄCZ PO SESJI (sekcja stała, nie usuwać)

**Jedyny zasób generujący stały koszt: Amazon OpenSearch Serverless (AOSS)**
- Stawka: 2 OCU × $0.24/OCU/h = **~$11.52/dzień (~$345/miesiąc)**
- Billing aktywny gdy AOSS collection istnieje w stanie ACTIVE
- Nie zatrzymuje się samo — musisz ręcznie zniszczyć kolekcję

**Jak zatrzymać koszty (po każdej sesji roboczej):**
```powershell
# Z katalogu projektu:
.\scripts\stop_costs.ps1
```
Skrypt niszczy AOSS + Bedrock KB, zostawia całą resztę infry nietkniętą.

**Jak przywrócić (przed następną sesją z Phase 05+):**
```powershell
$tf = "C:\Users\Michal\AppData\Local\Microsoft\WinGet\Packages\Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe\terraform.exe"
Set-Location terraform
& $tf apply
# Po apply — odtwórz indeks AOSS:
$uv = "C:\Users\Michal\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe"
& $uv run python scripts/create_kb_index.py
# Następnie jeszcze raz apply (tworzy KB):
& $tf apply
```
Stan Terraform jest w S3 remote backend — `apply` odtwarza AOSS + KB bez utraty konfiguracji.
**WAŻNE:** `create_kb_index.py` musi być uruchomiony między pierwszym a drugim `apply` — Bedrock KB wymaga istniejącego indeksu AOSS.

**Pozostałe zasoby (Lambda, API GW, DynamoDB, S3, Cognito, CloudTrail):** koszt = $0 przy braku ruchu.

---

## Stan projektu
- Aktualna faza: Phase 08 – CI/CD + End-to-End Tests
- Ostatni ukończony etap: Phase 07 / Step 06 – Integration tests (full-flow) — **Phase 07 ZAKOŃCZONA**
- Status testów: zielone (99/99 unit + 10 skipped integration; łącznie z poprzednimi: 508+ unit, 2 xfailed, 10 integration pending)
- Infrastruktura: AOSS WYŁĄCZONE (stop_costs.ps1), CLI moduł gotowy (`cli/`), DynamoDB conversation_history aktywna

## Następny krok
Phase 08 / Step 01 – Pytest e2e: testy integracyjne full-flow (każda rola × każdy clearance level)
- Wymaga: `terraform apply` (przywrócenie AOSS) → `create_kb_index.py` → `terraform apply` (KB)
- Uruchomienie Step 06 integration testów jako smoke test przed pełnym e2e
- Env vars: `INT_TEST_USER_ENG`, `INT_TEST_PASS_ENG`, `INT_TEST_USER_FIN`, `INT_TEST_PASS_FIN`, `CHAT_API_URL`

## Otwarte kwestie
- **Bedrock model access** – wymagane ręczne włączenie w Bedrock console → Model Access → "Amazon Titan Embeddings V2" + "Amazon Titan Text Express v1". Bez tego KB sync nie zadziała.
- **AOSS index odtwarzanie** – po `stop_costs.ps1` + ponownym `terraform apply` indeks AOSS `bedrock-kb-index` nie istnieje. Sekwencja przywracania: `apply` (AOSS) → `create_kb_index.py` → `apply` (KB). Udokumentowane powyżej.
- **Strategia testowania Phase 08** – AOSS wyłączone obecnie. Włączyć raz w Phase 08 na czas e2e (~2h, koszt ~$1): `apply` → `create_kb_index.py` → `apply`. Po testach natychmiast `stop_costs.ps1`.
- **Znane ograniczenia regex-only detection** (udokumentowane jako xfail):
  - Polskie wzorce injection – nie wykrywane
  - Base64/ROT13-encoded injection – nie wykrywane
  - Obie klasy zaadresowane semantyczną detekcją w Phase 10

## Zmodyfikowane pliki (sesja 2026-06-13)
lambda/sts/handler.py tests/sts/test_step01_iam_abac.py tests/sts/test_step02_session_policy.py tests/sts/test_step03_cache.py tests/sts/test_step04_bedrock_client.py tests/sts/test_step05_scope_isolation.py tests/sanitizer/test_step02_lambda_sanitize.py tests/sanitizer/test_step03_sandwich.py tests/sanitizer/test_step04_policy.py tests/sanitizer/test_step05_security.py terraform/bedrock_kb.tf terraform/lambda_sts.tf terraform/outputs.tf scripts/create_kb_index.py scripts/stop_costs.ps1 scripts/ingest_docs.py tests/kb/__init__.py tests/kb/test_step01_terraform_kb.py tests/kb/test_step02_ingest.py tests/kb/test_step03_metadata_filter.py tests/kb/test_step04_retrieve_generate.py tests/kb/test_step05_abac_isolation.py tests/conversation/__init__.py tests/conversation/test_step01_session_id.py tests/conversation/test_step02_history.py tests/conversation/test_step03_context_injection.py cli/__init__.py cli/auth.py cli/storage.py cli/scan.py cli/gateway.py cli/main.py tests/cli/__init__.py tests/cli/test_step01_auth.py tests/cli/test_step02_storage.py tests/cli/test_step03_scan.py tests/cli/test_step04_gateway.py tests/cli/test_step05_cli_loop.py tests/cli/test_step06_integration.py pyproject.toml uv.lock STEPS/Phase03_WriteUp.md STEPS/Phase05_WriteUp.md STEPS/Phase06_WriteUp.md STEPS/Phase07_WriteUp.md STEPS/PhasePlan.md STEPS/HANDOFF.md
