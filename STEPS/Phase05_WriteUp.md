# Phase 05 WriteUp – Bedrock Knowledge Base z ABAC (metadataFilter)

## Step 01 – Terraform: Bedrock KnowledgeBase + AOSS + IAM

**Co zrobiono:** Stworzono `terraform/bedrock_kb.tf` z pełnym stosem infrastruktury KB: (1) IAM role `bedrock_kb` (principal `bedrock.amazonaws.com`, source condition na account_id + KB ARN pattern) z trzema politykami – S3 read, `bedrock:InvokeModel` na Titan Embeddings V2, `aoss:APIAccessAll` na kolekcję; (2) AOSS security policy `encryption` (AWSOwnedKey) + `network` (AllowFromPublic); (3) AOSS data access policy grantująca `bedrock_kb` i `bedrock_scoped` pełne uprawnienia collection + index; (4) AOSS collection `iam-gateway-dev-kb` (typ VECTORSEARCH); (5) `aws_bedrockagent_knowledge_base` z Titan Embeddings V2 (`amazon.titan-embed-text-v2:0`), AOSS storage, field mapping (`bedrock-knowledge-base-default-vector`, `AMAZON_BEDROCK_TEXT_CHUNK`, `AMAZON_BEDROCK_METADATA`); (6) `aws_bedrockagent_data_source` (S3, chunking FIXED_SIZE 300 tokenów / 20% overlap). Zaktualizowano `outputs.tf` (4 nowe outputy) i `lambda_sts.tf` (`KNOWLEDGE_BASE_ID` w env vars).

**Dlaczego:** AOSS jest jedynym wektorem wspieranym natywnie przez Bedrock KB bez zewnętrznych zależności. `depends_on` łańcuch (security policies → collection → access policy + IAM → KB → data source) gwarantuje właściwą kolejność provision – bez niego Bedrock KB może nie znaleźć indeksu AOSS przy pierwszym create. Field mapping z `AMAZON_BEDROCK_METADATA` jest warunkiem koniecznym do działania metadataFilter w Phase 05 Step 03.

**Kompromisy:** AOSS kosztuje ~$11.52/dzień (2 OCU minimum) – przygotowano `scripts/stop_costs.ps1` do targeted destroy po sesji. Chunking FIXED_SIZE 300/20% wybrany jako dobry default dla dokumentów tekstowych średniej długości; HIERARCHICAL byłby lepszy dla długich dokumentów, ale niepotrzebny dla PoC.

**Testy:** 19 testów lokalnych (wszystkie zielone): `terraform validate` (1), struktura pliku bedrock_kb.tf (13), lambda env var (1), outputs (4). Pełny suite: 424/424 + 2 xfailed.

**Uwagi operacyjne:** Bedrock KB wymaga istniejącego indeksu AOSS przed provisioning — provider go nie tworzy automatycznie. Sekwencja: `terraform apply` (AOSS) → `scripts/create_kb_index.py` → `terraform apply` (KB). Dodano `aws_opensearchserverless_access_policy.kb_admin` grantującą deploying IAM identity prawa do zarządzania indeksem. Po `stop_costs.ps1` i ponownym `terraform apply` sekwencja musi być powtórzona.

---

## Step 02 – Ingest pipeline: dokumenty testowe + sidecar metadata + KB sync

**Co zrobiono:** Napisano `scripts/ingest_docs.py` z modularną strukturą: (1) pure helper functions (`s3_key_for_doc`, `s3_key_for_metadata`, `build_metadata_payload`, `doc_content`, `all_document_pairs`) – wszystkie testowalne bez AWS; (2) `upload_documents` uploaduje 8 dokumentów tekstowych (2 departments: `alpha`, `bravo` × 4 clearance levels: 0–3) + 8 sidecarów `.metadata.json` do S3 z prefixem `docs/{department}/cl_{level}/`; (3) `start_ingestion` + `wait_for_ingestion` triggerują Bedrock KB sync job z pollingiem do statusu `COMPLETE` (timeout 300s). Format sidecarów: `{ "metadataAttributes": { "department": str, "clearance_level": int } }` — konwencja wymagana przez Bedrock KB data source z sidecar metadata.

**Dlaczego:** Dokumenty muszą być w S3 z poprawnymi sidecarami PRZED sync jobem — Bedrock KB przetwarza metadane podczas ingestii i osadza je w wektorach AOSS. Bez `clearance_level` jako `int` (nie `str`) metadataFilter `lessThanOrEquals` w Phase 05 Step 03 nie zadziała poprawnie. Separacja pure functions od AWS operations pozwala testować logikę lokalnie.

**Kompromisy:** Dokumenty testowe mają prostą, schematyczną treść — wystarczającą do walidacji ABAC, nie reprezentatywną dla produkcji. Matrix 2×4=8 dokumentów daje minimalny zasięg potrzebny do testów izolacji cross-tenant w Step 05.

**Testy:** 26 testów łącznie: 21 lokalnych (unit) + 5 integracyjnych (live AWS). Lokalne: `TestBuildMetadataPayload` (7), `TestS3Keys` (7), `TestAllDocumentPairs` (4), `TestDocContent` (3). Integracyjne (`@pytest.mark.integration`): S3 TXT exists (1), S3 metadata sidecars exist (1), metadata department correct (1), metadata clearance_level correct + int type (1), ingestion job COMPLETE (1). Wszystkie 26/26 zielone. Pełny suite: 445/445 + 2 xfailed.

---

## Step 03 – Lambda: metadataFilter builder

**Co zrobiono:** Dodano `build_metadata_filter(department, clearance_level)` do `lambda/sts/handler.py`. Funkcja zwraca dict z `andAll` zawierającym dwa warunki: `equals` na polu `department` (str) oraz `lessThanOrEquals` na polu `clearance_level` (int). Struktura jest bezpośrednio kompatybilna z `vectorSearchConfiguration.filter` w Bedrock RetrieveAndGenerate API. Napisano 26 unit testów w `tests/kb/test_step03_metadata_filter.py` pokrywających: strukturę `andAll`, operator `equals` na department, operator `lessThanOrEquals` na clearance_level, typy wartości (int nie str), izolację cross-tenant, parametryzowane testy dla 5 poziomów clearance i 4 departmentów.

**Dlaczego:** `clearance_level` musi być przekazany jako `int` — Bedrock ewaluuje `lessThanOrEquals` numerycznie; przekazanie stringa dałoby błąd lub niezdefiniowane zachowanie. `department` jako `equals` (nie `in` ani prefix) wymusza ścisłą izolację tenant — użytkownik `alpha` nie może nigdy widzieć dokumentów `bravo` nawet przez manipulację zapytaniem. Separacja filter buildera jako pure function (bez AWS) pozwala testować logikę ABAC niezależnie od infrastruktury.

**Kompromisy:** Brak — funkcja jest deterministyczna i nie ma trade-offów na tym poziomie.

**Testy:** 26/26 zielone (wszystkie lokalne, bez AWS). Pełny suite: 471/471 + 2 xfailed.

---

## Step 04 – Bedrock Retrieve & Generate pipeline z metadataFilter

**Co zrobiono:** Zastąpiono `_invoke_bedrock` (bezpośrednie `InvokeModel` na `bedrock-runtime`) funkcją `_retrieve_and_generate(user_message, credentials, policy, metadata_filter)` korzystającą z `bedrock-agent-runtime` i `retrieve_and_generate` API. Klient inicjalizowany per-request z STS credentials z cache'a. Konfiguracja: `retrievalConfiguration.vectorSearchConfiguration.filter` przyjmuje wynik `build_metadata_filter`, `generationConfiguration.inferenceConfig.textInferenceConfig` wczytuje parametry z `ClearancePolicy`. Dodano `BEDROCK_KB_MODEL_ARN` do env vars Lambdy w `lambda_sts.tf` (ARN konstruowany z region + model_id, bez nowej zmiennej Terraform). Zaktualizowano 9 istniejących plików testowych które mockowały `_invoke_bedrock` — podmienione na `_retrieve_and_generate`. Napisano 20 nowych testów w `tests/kb/test_step04_retrieve_generate.py`.

**Dlaczego:** `retrieve_and_generate` (R&G) to single API call obsługujący zarówno retrieval (AOSS vector search z metadataFilter) jak i generację — efektywniejszy i mniej podatny na błędy niż dwa oddzielne wywołania. `bedrock-agent-runtime` wymaga pełnego ARN modelu (nie samego ID) — dlatego `BEDROCK_KB_MODEL_ARN` jako osobny env var. Credentials STS trafiają bezpośrednio do klienta Bedrock Agent Runtime — nigdy nie są ujawniane callerowi.

**Kompromisy:** `numberOfResults=5` — rozsądny default dla PoC; w produkcji powinno być konfigurowalne per clearance level. Stara funkcja `_invoke_bedrock` usunięta (dead code po zmianie API); testy z niej przepisane na nowy interfejs.

**Testy:** 20 nowych w `test_step04_retrieve_generate.py` (lokalne): `TestClientCreation` (5), `TestApiCallParameters` (6), `test_generation_params_match_policy` (5 parametryzowane), `test_returns_output_text` (1), `TestHandlerFilterWiring` (3). Zaktualizowane testy z faz 01–04 (9 plików, 21 zmian mock). Pełny suite: 492/492 + 2 xfailed.

---

## Step 05 – Testy izolacji cross-tenant (ABAC enforcement)

**Co zrobiono:** Napisano `tests/kb/test_step05_abac_isolation.py` z 24 testami weryfikującymi izolację ABAC na dwóch poziomach. (1) Unit testy (16, lokalne): `TestFilterBuiltFromJwt` — filter pochodzi wyłącznie z JWT authorizer, body request nie może nadpisać `department` ani `clearance_level`; `TestCrossDepartmentIsolation` — filtry alpha/bravo są różne i wzajemnie wykluczające; `TestClearanceCeiling` — cl=1 user ma ceiling=1 (nie 3), cl=3 ma ceiling=3, cl=0 ma ceiling=0; `TestFilterOperators` — department używa `equals` (nie `startsWith`), clearance używa `lessThanOrEquals` (nie `equals` ani `lessThan`). (2) Integration testy (8, live AWS): `TestLiveAbacIsolation` — wywołania `bedrock-agent-runtime.retrieve()` z realnym metadataFilter: `alpha/cl=1` zwraca wyłącznie alpha docs z cl≤1; `alpha/cl=3` nie zwraca bravo docs; `bravo/cl=2` nie zwraca alpha docs; cl=3 user widzi dokumenty na wielu poziomach (hierarchia dostępu).

**Dlaczego:** Testy integracyjne używają `retrieve()` (nie `retrieve_and_generate`) — oddziela weryfikację ABAC od jakości generacji, co daje deterministyczny wynik niezależny od modelu generatywnego. `lessThanOrEquals` (nie `lessThan`) jest kluczowe — `lessThan` wykluczyłoby dokumenty na dokładnie poziomie użytkownika (cl=2 user nie widziałby docs cl=2), co jest błędną semantyką dla systemu hierarchicznego dostępu.

**Kompromisy:** Testy integracyjne zależą od AOSS ACTIVE i ingestion job COMPLETE — muszą być poprzedzone Step 02. Zapytanie `"department clearance test"` dobrane empirycznie by trafić we wszystkie dokumenty testowe; dla produkcji testy e2e powinny używać domain-specific queries.

**Testy:** 16/16 unit (lokalne) + 8/8 integration (live AWS) = 24/24 zielone. Pełny suite: 508/508 + 2 xfailed (local), 8/8 integration pass.

---
## Podsumowanie fazy 05

**Co zostało zbudowane:** Pełny stos Bedrock Knowledge Base z ABAC: infrastruktura AOSS + KB (Terraform), pipeline ingestii 8 dokumentów testowych z sidecar metadanymi (department + clearance_level), funkcja `build_metadata_filter` tworząca filter `{andAll: [equals(dept), lessThanOrEquals(cl)]}`, integracja R&G w Lambda przez `_retrieve_and_generate` z STS credentials i ClearancePolicy, testy izolacji cross-tenant na poziomie unit i live AWS.

**Jak działa:** Każde zapytanie do `/chat` generuje metadataFilter z JWT context (department + clearance_level) i przekazuje go do `bedrock-agent-runtime.retrieve_and_generate`. AOSS vector search zwraca wyłącznie chunki pasujące do filtra — cross-tenant isolation i hierarchia clearance są egzekwowane przez Bedrock na poziomie retrieval, nie przez kod aplikacji. STS credentials scoped do departmentu użytkownika uniemożliwiają manipulację filtrem na poziomie IAM.

**Co można dalej rozwinąć:** (1) Phase 06 — zarządzanie historią konwersacji (DynamoDB, ostatnie 3–5 wymian wstrzykiwane do kontekstu R&G); (2) Skalowanie dokumentów KB (obecne 8 testowych → rzeczywiste dokumenty per department); (3) `numberOfResults` konfigurowalne per clearance_level (wyższy clearance = więcej kontekstu).
