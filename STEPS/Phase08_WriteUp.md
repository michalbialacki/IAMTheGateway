# Phase 08 – CI/CD + End-to-End Tests

## Step 01 – Marker split (offline)

**Co zrobiono:** Rozbito pojedynczy marker `integration` na trzy poziomy testowe: brak markera = offline (bez AWS), `aws` = żywe AWS bez AOSS (~$0), `aoss` = AOSS ACTIVE + Bedrock KB (~$1/2h). Przemapowano 3 istniejące testy integracyjne (`kb/test_step02_ingest`, `kb/test_step05_abac_isolation`, `cli/test_step06_integration`) z `integration` na `aoss` oraz zaktualizowano docstringi.
**Dlaczego:** Stary marker zlewał „wymaga AWS" z „wymaga AOSS", uniemożliwiając uruchamianie testów warstwami od najtańszej. Rozdzielenie pozwala odpalić cały tier offline i `aws` za $0, a płatne AOSS dopiero w jednym oknie na końcu.
**Kompromisy:** Marker `integration` usunięty całkowicie (czysta migracja zamiast aliasu) – odniesienia w HANDOFF/docstringach zaktualizowane. `--strict-markers` nie jest włączone, więc literówka w markerze nie wywali kolekcji – do rozważenia w Step 03.
**Testy:** `pytest --collect-only` per tier: offline 656/682 (26 deselected), `aoss` dokładnie 26 (3 otagowane pliki), `aws` 0 (zostanie wypełnione w Step 02). Suma 682, brak błędów kolekcji.

## Step 02 – E2E suite (offline) + alignment działów

**Co zrobiono:**
- Zsynchronizowano działy: `scripts/ingest_docs.py` z `alpha/bravo × cl0-3` na `engineering/legal/security × cl0-4` (zgodnie z userami z `create_test_users.py`), by `metadataFilter` zalogowanego usera trafiał w realne dokumenty. Live-testy `kb/test_step05` przepięte na engineering/legal (unit zostają na alpha/bravo jako arbitralne stringi).
- Nowy pakiet `tests/e2e/`: `_helpers.py` (E2EUser z create_test_users jako single source of truth, raw_post), `conftest.py` (fixtures: cognito_config, chat_url, revoke_url, parametryzowany e2e_user × user_tokens), `test_step01_fullflow.py` (aoss, matrix 3 userów × 7 testów), `test_step02_security.py` (9 testów: aws = 401/403/400 przed Bedrock, aoss = bypass metadanych).
- Bearer = **access_token** (authorizer wymaga `token_use=="access"`), nie id_token.

**Dlaczego:** Pełne e2e napisane offline, otagowane aws/aoss, gotowe do jednego płatnego okna w Step 05.

**Kompromisy:** Test expired-JWT jest opt-in (`E2E_EXPIRED_TOKEN`) — Cognito nie pozwala wygenerować wygasłego tokena na żądanie bez czekania 1h.

**Testy:** Kolekcja e2e: 30 testów (21 fullflow + 9 security), ruff na nowych plikach czysty. Walidacja pełnego tieru offline **ujawniła pre-existing breakage z commita 3c1c121** (nie z tych zmian):
- **123 faile w offline tier**: 109 × `KeyError: CONVERSATION_TABLE` (handler woła `_save_exchange`/`_load_history` z Phase 06; starsze testy Phase 03/04/05 invokujące pełny `lambda_handler` nie mockują tego) + 16 × `ResourceNotFoundException` (live-AWS testy gate'owane tylko na `.terraform`, nieoznaczone markerem).
- HANDOFF twierdził „zielone 508+" — nieścisłe; pełny offline suite nie był uruchamiany po integracji Phase 06.

**STATUS: zakończony po remediacji w Step 02b.** (Pierwotnie niezakończony — offline tier był czerwony z powodu pre-existing breakage.)

## Step 02b – Remediacja offline (odblokowanie tieru)

**Co zrobiono:** Naprawiono 123 pre-existing faile offline (z commita `3c1c121`), które blokowały walidację Phase 08. Dwie klasy:
- **Class A (109 × `KeyError: CONVERSATION_TABLE`):** w helperze ładującym handler (`_import_handler` / `_load_handler`) każdego z 11 plików invokujących pełny `lambda_handler` (sts/step01-05, sanitizer/step02-05, kb/step04-05) dodano neutralizację trwałości konwersacji z Phase 06: `mod._save_exchange = lambda *a, **k: None` oraz `mod._load_history = lambda *a, **k: []`. Jedna edycja na plik (DRY), zamiast modyfikacji każdego bloku `with patch.object`.
- **Class B (14 live-AWS):** root cause = gate `@skip_no_aws`/`skip_no_infra` oparty na *obecności creds*, nie na markerze — przepuszczał testy gdy creds są, ale infra zniszczona → `ResourceNotFoundException`. Naprawa: dodano `@pytest.mark.aws` nad **każdym** `@skip_no_aws` w 9 plikach (infra/step02-07, sts/step01,02,05) — łącznie ~50 live-testów, nie tylko 14 padających — oraz module-level `pytestmark = pytest.mark.aws` w `auth/test_step05_e2e_jwt.py`. Dzięki temu offline tier jest hermetyczny niezależnie od tego, która infra akurat żyje, a tier `aws` zbiera komplet tanich live-testów na Step 05.

**Dlaczego:** Tier offline musi być zielony i deterministyczny zanim zbudujemy CI (Step 03), który go uruchamia. Oznaczenie *wszystkich* live-testów (nie tylko padających) zamyka latentną kruchość: gate na creds był złym kryterium dla warstwy offline.

**Kompromisy:** Liczba testów „passed" w offline spadła (640→598), bo ~42 live-testów które wcześniej przypadkiem przechodziły (infra częściowo żywa) wyszło z tieru offline do `aws` — to korekta, nie regresja. Kod produkcyjny handlera **nietknięty** (asymetria `_save_exchange` strict vs `_load_history` defensywny zostawiona świadomie; ewentualna zmiana to osobna decyzja, nie test-fix). Decyzja `auth/step05_e2e` → `aws` (nie `aoss`): testy success-path akceptują 502 (Bedrock/KB niewłączony), więc nie wymagają AOSS.

**Testy:**
- Offline (`-m "not aws and not aoss"`): **598 passed, 0 failed, 112 deselected, 2 xfailed** (było 123 failed).
- Kolekcja tierów: `aws and not aoss` = 64, `aoss` = 48, `tests/e2e` = 30 — bez błędów kolekcji.
- Ruff: 17 pre-existing problemów w dotkniętych plikach (nieużywane importy, sortowanie `import boto3` w funkcjach) — **nie wprowadzone tą zmianą** (edycje czysto addytywne); należą do puli 35 błędów ruff adresowanej w Step 04.

## Step 04 – Security hardening audit (offline)

> Kolejność: Step 04 wykonano **przed** Step 03 (CI) — CI ma odpalać `ruff check`, więc lint musi być zielony zanim powstanie pipeline.

**Co zrobiono:** Audyt bezpieczeństwa bez dotykania płatnej infry: (1) lint cleanup, (2) IAM least-privilege review, (3) secrets scan + przegląd pinów zależności.

**1. Lint cleanup (ruff 35 → 0):**
- 19 × F401 (nieużywane importy) + 9 × I001 (sortowanie) → `ruff check --fix` (auto, tylko pliki testowe; `lambda/sts/handler.py` nie miał F401, jedynie reorder importów I001).
- 7 × N802 (nazwy funkcji nie-lowercase) w `tests/kb/test_step03_metadata_filter.py` i `tests/kb/test_step05_abac_isolation.py` → **nie zmieniono nazw**, tylko `per-file-ignores` w `pyproject.toml`. Powód: nazwy (`test_uses_lessThanOrEquals_operator`, `test_top_level_key_is_andAll`) odzwierciedlają **dosłowne klucze API Bedrock metadataFilter** (`andAll`/`orAll`/`lessThanOrEquals`); snake_case zerwałby korespondencję z API.
- E402 = 0 (wcześniejszy zbiorczy licznik dał spurious match). Po naprawie: offline tier nadal 598 passed / 0 failed; `ruff check .` = „All checks passed!".

**2. IAM least-privilege review (`terraform/iam.tf`) — PASS:**
- `lambda_exec`: logi zawężone do `/aws/lambda/${name_prefix}-*`, `sts:AssumeRole` wyłącznie na `bedrock_scoped`, DynamoDB tylko do 3 tabel projektu (+ ich indeksów). Bez `*` na zasobach.
- `bedrock_scoped`: tagi sesji `department`+`clearance_level` wymuszone podwójnie — w trust policy (`aws:RequestTag` Null=false) i w permission policy (`aws:PrincipalTag` Null=false, defense-in-depth). `bedrock:InvokeModel` zawężony do konkretnego foundation-model ARN. Retrieve zablokowany regionem (`aws:RequestedRegion`).
- **Accepted finding:** `bedrock:Retrieve`/`RetrieveAndGenerate` mają `Resource = "*"`. Świadomy trade-off — KB Bedrock jest efemeryczne (niszczone przez `stop_costs.ps1`); zawężenie do ARN KB sprzęgłoby zawsze-włączoną rolę z efemerycznym zasobem i wywalałoby `terraform apply` gdy KB nie istnieje. Kontrole kompensujące: Null na session-tagach + region lock. Do rewizji jeśli KB stanie się stały (poza PoC).

**3. Secrets scan + zależności:**
- Skan regex po plikach tracked (AKIA/secret/private-key/password) → **brak hardcoded secrets**. Brak gitleaks/trufflehog w środowisku → rekomendacja: dodać gitleaks jako krok CI w Step 03.
- `.gitignore` pokrywa `*.tfstate*`, `terraform/backend.tf`, `**/backend.tf`, `*.tfvars`, `.env*` (z wyjątkami `*.example`). `git ls-files` → żaden `.env`/`.tfstate`/`backend.tf`/`.tfvars`/`.pem` nie jest zacommitowany.
- Piny: AWS provider `~> 5.50`, Terraform `>= 1.7.0` (OK). Python: `pyproject` używa floorów `>=`, ale `uv.lock` pinuje dokładne wersje (reprodukowalność zachowana). Akceptowalne.

**Dlaczego:** Tier CI (Step 03) wymaga zielonego lintu i braku sekretów w repo. Audyt potwierdza, że granica systemu (IAM, .gitignore) jest szczelna przed wystawieniem pipeline.

**Kompromisy:** N802 rozwiązane przez ignore zamiast renamingu (świadomy wybór czytelności API > konwencja). IAM `Resource="*"` na Retrieve zostawione (uzasadnione wyżej). gitleaks nie uruchomiony lokalnie (brak narzędzia) — skan regex jako substytut + plan dodania do CI.

**Testy:** `ruff check .` = czysto; `terraform validate` = Success; `terraform fmt -check -recursive` = czysto; offline tier `-m "not aws and not aoss"` = 598 passed / 0 failed / 2 xfailed.

## Step 02c – Spójność tokenów i userów (domknięcie Step 02 przed live)

> Wykonane po Step 04. Naprawia dwa latentne bugi, które inaczej wywróciłyby live e2e/CLI w Step 05.

**Co zrobiono:**
- **id_token → access_token:** Authorizer (`lambda/authorizer/handler.py:60`) wymaga `token_use=='access'`, ale CLI wysyłał `id_token` jako Bearer → gwarantowane 401. Zmieniono: parametr `send_message(access_token=...)` w `cli/gateway.py` (+ nagłówek `Authorization`), wywołanie w `cli/main.py`, oraz wszystkie `send_message(...)` w `tests/cli/test_step06_integration.py`. Zaktualizowano docstringi/komentarze pól w `cli/auth.py` (access token niesie `cognito:groups`; id token nie jest już wysyłany do API GW). Pakiet `tests/e2e/` już wysyłał access_token — bez zmian.
- **User `finance` → `legal`:** `test_step06` odwoływał się do nieistniejącego usera `INT_TEST_USER_FIN`/finance. `create_test_users.py` definiuje tylko alice (engineering/2), bob (legal/1), eve (security/4). Przepięto fixturę `fin_tokens`→`legal_tokens` (env `INT_TEST_USER_LEGAL`/`INT_TEST_PASS_LEGAL`), test `test_finance_user_gets_chat_response`→`test_legal_user_gets_chat_response` (assert `department == "legal"`).

**Dlaczego:** Live e2e w Step 05 musi używać prawidłowego typu tokena i istniejących userów, inaczej cały tier `aoss`/`aws` dla CLI padnie na 401/skip.

**Kompromisy:** Rename parametru `id_token`→`access_token` w `send_message` jest bezpieczny — unit-testy (`test_step04_gateway`) wołają pozycyjnie, więc niezłamane. Pole `AuthTokens.id_token` zostaje (Cognito je zwraca; po prostu nie jest wysyłane do API GW).

**Testy:** `tests/cli/test_step04_gateway` = 17 passed (pozycyjni wołający OK); offline tier = 598 passed / 0 failed; `test_step06` (aoss) zbiera 13 testów; ruff `cli/` + step06 = czysto. Live walidacja (faktyczne 200/401) dopiero w Step 05.

## Step 03 – GitHub Actions CI pipeline

**Co zrobiono:** Dodano `.github/workflows/ci.yml` — pipeline w pełni offline (zero AWS creds, zero AOSS), uruchamiany na `push` (master/main) i każdym `pull_request`. Trzy równoległe joby:
1. **lint-test:** `astral-sh/setup-uv@v5` (z cache) → `uv sync --frozen --dev` (uv provisionuje CPython wg `requires-python>=3.12` i instaluje dokładne wersje z `uv.lock`) → `uv run ruff check .` → `uv run pytest -m "not aws and not aoss" -q`.
2. **terraform:** `hashicorp/setup-terraform@v3` (1.9.8) → `terraform fmt -check -recursive` → `init -backend=false` + `validate`. `-backend=false` pomija S3 backend (gitignored `backend.tf`) i creds — validate pobiera tylko providera AWS do type-checku.
3. **secrets-scan:** gitleaks (pinned binary v8.21.2, `detect` license-free) na pełnej historii (`fetch-depth: 0`) — realizuje rekomendację z Step 04.

Dodatkowo: `permissions: contents: read` (least-privilege dla `GITHUB_TOKEN`) + `concurrency` z `cancel-in-progress` (anuluje nieaktualne biegi na tym samym ref).

**Dlaczego:** CI ma być zieloną bramką jakości bez kosztów i bez sekretów — łapie regresje lintu, offline-testów i konfiguracji Terraform na każdym PR. Płatne tiery (`aws`/`aoss`) celowo poza CI — uruchamiane ręcznie w Step 05.

**Kompromisy:** **`terraform plan` świadomie pominięty** w domyślnym pipeline — plan wymaga realnego stanu S3 + AWS creds, co kłóci się z „CI bez sekretów". Aby go włączyć opcjonalnie: dodać job gated na `if: ${{ secrets.AWS_ROLE_ARN != '' }}` z `aws-actions/configure-aws-credentials` (OIDC, read-only) + `terraform init` (z backendem) + `terraform plan -lock=false`. `validate` + `fmt` pokrywają statyczną poprawność konfiguracji bez tego. gitleaks uruchamiany jako pinned binary (nie `gitleaks-action`), by uniknąć wymogu licencji na repo prywatnym.

**Testy:** YAML zwalidowany (`yaml.safe_load` → 3 joby sparsowane). Komendy bazowe udowodnione lokalnie: `ruff check .` = czysto, offline tier = 598 passed / 0 failed, `terraform validate` = Success, `terraform fmt -check -recursive` = czysto, `uv lock --check` = aktualny (`--frozen` nie wywali CI). gitleaks zweryfikowany pośrednio skanem regex w Step 04 (brak sekretów w repo).

## Step 05 – Pełne uruchomienie (płatne okno AOSS)

**Co zrobiono:** Odtworzono infrastrukturę i przejechano testy live warstwami. Napotkano rozjazd stanu i jeden blocker Bedrock; oba zdiagnozowane, fixy zrobione offline.

**Przebieg deploymentu:**
1. **Rozjazd stanu:** `terraform plan` pokazał 13 to add / 0 destroy. Pierwszy targetowany `apply` padł: `ConflictException: collection already exists` — kolekcja AOSS żyła w AWS, ale nie było jej w stanie TF. Naprawione przez `terraform import aws_opensearchserverless_collection.kb <id>` (stary id z outputu był nieaktualny; aktualny odczytany przez `list-collections`).
2. **Indeks → KB:** `create_kb_index.py` (kolekcja ACTIVE, indeks `bedrock-kb-index` utworzony) → pełny `terraform apply` = 8 added (KB, data source, STS Lambda, API GW). Potwierdzono empirycznie: komentarz w `bedrock_kb.tf` o auto-tworzeniu indeksu jest błędny — indeks MUSI powstać przed KB.
3. **Dane:** `ingest_docs.py` = 15 dokumentów + ingestion job COMPLETE (embeddingi Titan V2 działają).

**Wyniki testów:**
- **Tier `aws` (~$0): 62 passed, 1 skipped, 1 finding.** Zwalidowane live: cała warstwa auth/JWT (401/403/odrzucenia), ABAC session tags, izolacja, infra (DynamoDB/S3/Cognito/CloudTrail/API GW). Pozytywny dowód że rdzeń bramki działa.
- **Tier `aoss` (płatny): 24 passed, 24 failed — wszystkie 24 faile to jeden root cause.**

**Root cause (blocker generacji):** `RetrieveAndGenerate` zwracał `ValidationException: The model arn provided is not supported` na KAŻDYM zapytaniu chat. Diagnoza (read-only `list-foundation-models`): **`amazon.titan-text-express-v1` nie istnieje na liście modeli TEXT w eu-central-1** (i nie jest wspierany przez Bedrock KB RAG). Embeddingi działały, bo to osobny model (`amazon.titan-embed-text-v2:0`, modalność embedding).

**Fixy (wszystkie offline / $0, walidowane lokalnie):**
1. **Model → `anthropic.claude-3-haiku-20240307-v1:0`** (`terraform/variables.tf` + `tests/sts/test_step05` FAKE_MODEL_ID). To **jedyny** model RAG-supported z `ON_DEMAND` w eu-central-1 (reszta — Nova, nowsze Claude — tylko `INFERENCE_PROFILE`). Zostaje format `foundation-model` ARN → zero zmian w IAM/handlerze. Handler-check potwierdził: `generationConfiguration.inferenceConfig.textInferenceConfig` jest model-agnostyczny, Claude przyjmie te same parametry. **Wymaga ręcznego włączenia model access „Claude 3 Haiku" w konsoli przed następnym oknem.**
2. **Luka regex sanitizera** (`lambda/sanitizer/patterns.py`): payload „ignore **all previous** instructions" przechodził, bo `ignore_instructions` łapał tylko jeden kwalifikator. Rozszerzono na 1–4 kwalifikatory z kontrolowanej listy + dodano test regresyjny. To znalezisko z tieru `aws` (test injection dostał 502 zamiast 400 — przy okazji ujawnione przez blocker modelu, bo każde zapytanie 502-owało).
3. **`stop_costs.ps1`** — czysty ASCII (był UTF-8 bez BOM ze znakami `─`/`–`; PowerShell 5.1 czytał jako Windows-1252, korumpując parsowanie → `(cost drivers)` wykonywane jako komenda `cost`). Sprostowano też opis: destroy KB kaskaduje na Lambdę + API GW (env `KNOWLEDGE_BASE_ID`).

**Dlaczego Claude 3 Haiku, nie Nova:** Nova w eu-central-1 jest dostępna **wyłącznie** przez cross-region inference profile (`eu.*`), który routuje ruch po regionach EU (Frankfurt/Irlandia/Paryż/…). Claude 3 Haiku `ON_DEMAND` liczy **in-region** (Frankfurt), bez routingu, z prostszym IAM. Dla projektu z poziomami clearance (data residency) in-region jest przewagą bezpieczeństwa.

**Kompromisy / dług:** Pełna walidacja tieru `aoss` z poprawionym modelem wymaga **kolejnego okna AOSS** (włączyć Claude 3 Haiku access → apply → create_kb_index → apply → ingest → `-m aoss` → stop_costs). Pozostałe FAKE_MODEL_ID w offline-fixturach (test_step04, sanitizer, kb) zostawiono jako Titan — to arbitralne wartości bez asercji przeciw live, zmiana byłaby czystym szumem.

**Uwaga na przyszłość (cross-region — nie wzięte pod uwagę w tym projekcie):** Wybór modelu zawęził się do in-region dopiero po napotkaniu blockera; cross-region inference profiles (Nova, nowsze Claude) świadomie odrzucono ze względu na routing danych poza Frankfurt. To jednak otwiera **pomysł na osobny projekt**: bramka GenAI świadomie wykorzystująca cross-region inference profiles — np. routing/load-balancing modeli po regionach EU z politykami data-residency per tenant, albo failover między regionami. Wątek do osobnego PoC, poza zakresem IAM Gateway.

**Testy:** Po fixach — offline tier `-m "not aws and not aoss"` = **599 passed / 0 failed / 2 xfailed** (nowy test injection), `ruff check .` = czysto, `terraform fmt -check` = czysto, `terraform validate` = Success. Live tier `aoss` z nowym modelem: **do walidacji w następnym oknie** (po włączeniu model access).