# HANDOFF – 2026-06-14 (Phase 08 UKOŃCZONA — tier aoss 48/48)

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
- Aktualna faza: **Phase 08 – CI/CD + End-to-End Tests — UKOŃCZONA ✅**
- Ukończone etapy: Step 01 · 02 · 02b · 02c · 03 · 04 · 05 · **05b** (finalna walidacja live)
- Status testów: **offline = 599 passed / 0 failed / 2 xfailed** ✅ · **aws = 56 passed / 8 skipped / 0 findingów** ✅ · **aoss = 48/48 passed** ✅
- Infra: **WYŁĄCZONA** (`stop_costs.ps1` wykonany). Billing $0.
- Model: **`eu.amazon.nova-lite-v1:0`** (cross-region inference profile) — Claude 3 Haiku wycofany jako LEGACY.

## Następny krok
**Phase 09 – KMP Android Client** (future scope) lub zamknięcie projektu jako PoC.

Projekt IAM Gateway PoC jest funkcjonalnie kompletny. Wszystkie 3 tiery testów przechodzą.

## Otwarte kwestie
- **CI nie biegł realnie na GitHub** — workflow `.github/workflows/ci.yml` zwalidowany lokalnie, repo bez remote.
- **Pre-existing xfail** (regex-only detection): polskie wzorce injection + Base64/ROT13 — Phase 10.
- **gitleaks w CI** — dodany jako job, lokalnie nie weryfikowany.
- **`bedrock:GetInferenceProfile`** wymagane przy cross-region inference profiles — dodane do `iam.tf` i session policy.

## Zmodyfikowane pliki (do graphify)
STEPS/HANDOFF.md STEPS/Phase08_WriteUp.md STEPS/KnownLimitations.md lambda/sts/handler.py scripts/ingest_docs.py terraform/variables.tf terraform/lambda_sts.tf terraform/iam.tf
