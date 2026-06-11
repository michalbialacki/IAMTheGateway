# Phase 04 WriteUp – Input Security (Sanitization + Sandwich Method)

## Step 01 – Biblioteka regex patterns (PII, injection, jailbreak)

**Co zrobiono:** Stworzono moduł `lambda/sanitizer/` z dwoma plikami: `patterns.py` (skompilowane wzorce regex) i `sanitizer.py` (funkcje `scan_input`, `redact_pii`, dataclass `ScanResult`). Wzorce podzielone na trzy kategorie: PII (PESEL, IBAN PL, karta kredytowa, email, telefon, IP) – redagowane przed wysłaniem do Bedrock; prompt injection (ignore/disregard/forget instructions, act as, pretend to be, tokeny LLM `[INST]`/`<|im_start|>`, bypass filter, override system) – blokują żądanie; jailbreak (DAN, developer mode, jailbreak, uncensored, simulate being, no restrictions, base model) – blokują żądanie. Kluczowa decyzja: IBAN stosowany przed kartą kredytową (obie mają grupy 4-cyfrowe – IBAN musi wygrać, bo jest bardziej specyficzny).

**Dlaczego:** Separacja wzorców od logiki skanowania pozwala testować je niezależnie i rozszerzać w Phase 05 bez modyfikacji `scan_input`. PII jest redagowane (nie blokowane), żeby umożliwić anonimowe pytania do RAG – injection i jailbreak blokują żądanie całkowicie.

**Kompromisy:** Wzorce regex są deterministyczne i szybkie (<1 ms), ale mają ograniczoną skuteczność wobec zaawansowanych ataków obfuskacyjnych (np. Base64, ROT13, unicode look-alikes). Semantyczna detekcja (LLM-judge) odłożona do Phase 10. Fałszywe pozytywy minimalizowane przez testowanie na negatywnych przykładach (62 testy, 0 false positives na zestawie testowym).

**Testy:** 62 unit testy lokalne (no AWS), zielone 62/62. Pokrycie: każdy wzorzec ma test pozytywny (match) i negatywny (no match), case-insensitivity, Unicode, puste wejście, kombinacje PII+injection.

## Step 02 – Lambda: server-side 1st sanitize

**Co zrobiono:** Zintegrowano `scan_input` w `lambda/sts/handler.py` bezpośrednio po parsowaniu wiadomości. Jeśli `not scan.is_clean` → 400 z `{"error": "Request blocked by input security policy", "details": [...]}` (lista nazw wykrytych wzorców). Jeśli czyste → `message = scan.redacted_text` (PII usunięte). Zaktualizowano `terraform/lambda_sts.tf`: `archive_file` zmieniony z `source_file` na cztery bloki `source`, pakując `sanitizer/__init__.py`, `sanitizer/patterns.py`, `sanitizer/sanitizer.py` razem z `handler.py` w jednym ZIP. Zaktualizowano `tests/conftest.py`: `lambda/` dodane do `sys.path` globalnie – wszystkie testy mogą importować z paczek Lambda bez lokalnych `sys.path.insert`.

**Dlaczego:** Klient API Gateway jest untrusted – sanityzacja wyłącznie server-side. Redakcja PII odbywa się przed przekazaniem do Bedrock, więc dane osobowe nie trafiają do modelu. Pakowanie sanitizer w ZIP zapewnia spójność między środowiskiem lokalnym a Lambda.

**Kompromisy:** Globalny `sys.path` w `conftest.py` zamiast per-test – prostsze utrzymanie, ale wymaga unikalnych nazw pakietów w `lambda/`. Brak izolacji środowisk wykonania Lambda (każdy handler współdzieli jeden package namespace) – akceptowalne w PoC, docelowo rozwiązane przez Lambda Layers.

**Testy:** 26 unit testów w `test_step02_lambda_sanitize.py` (no AWS, z wyjątkiem `terraform validate/fmt`), zielone. Regresja Phase 03: 109/109 zielone. Łącznie: 171/171.

## Step 03 – Sandwich method

**Co zrobiono:** Stworzono `lambda/sanitizer/sandwich.py` z funkcją `build_sandwich_prompt(message, department, clearance_level) → str`. Prompt składa się z: `[SYSTEM]` (opening – ustala department, clearance_level, zakaz override), `[USER] {message}` (sanitized, PII-redacted), `[REMINDER]` (closing – powtarza ograniczenia). Zintegrowano w `handler.py` po sanitize i walidacji clearance, przed `_get_credentials` (bo sandwich wymaga `department` i `cl`). Dodano `sandwich.py` do `archive_file` w Terraform. Zaktualizowano 2 testy Phase 03 i Step 02, które sprawdzały `inputText == raw_message` → zmienione na `raw_message in inputText`.

**Dlaczego:** Sandwich method to obrona wielowarstwowa – nawet jeśli injection wzorzec regex nie pochwyci próby ataku, model widzi ograniczenia zarówno PRZED jak i PO wejściu użytkownika, co utrudnia przesłonięcie kontekstu systemowego. Konieczne dla modeli bez natywnego API systemu (Titan Text).

**Kompromisy:** Opening i closing to plain text (nie dedykowany "system" slot jak w Claude/GPT-4) – skuteczność zależy od modelu. Dla Titan jest to optymalne podejście. Sandwich nie jest redagowany przed wysłaniem – zawiera `department` i `clearance_label` w jawnym tekście, co jest zamierzone (model musi je widzieć).

**Testy:** 32 testy unit (`build_sandwich_prompt` + integracja Lambda) + 2 terraform validate/fmt. 202/202 zielone, regresja zerowa.

## Step 04 – Dynamiczne ograniczenia per clearance_level

**Co zrobiono:** Stworzono `lambda/sanitizer/policy.py` z dataclassem `ClearancePolicy(max_tokens, temperature, top_p, allowed_topics)` i funkcją `get_policy(clearance_level)`. Parametry generacji skalują się z clearance: cl=0 → 256 tokenów / temp 0.3; cl=4 → 4096 tokenów / temp 0.9. Tylko cl=0 (Unclassified) ma listę allowed_topic_keywords (HR, onboarding, policy, procedures itd.) – cl=1–4 bez ograniczeń tematycznych. Zaktualizowano `_invoke_bedrock` w `handler.py` – przyjmuje `ClearancePolicy` zamiast hardkodowanych wartości. Dodano topic gate w `lambda_handler` między walidacją clearance a sandwich (sprawdzany na PII-redacted message; cl=0 off-topic → 403). Zaktualizowano 4 testy Phase 03 (`_invoke_bedrock` bezpośrednie wywołania → teraz z `_POLICY_CL2`). Dodano `policy.py` do archive_file w Terraform.

**Dlaczego:** Niższe clearance powinno oznaczać mniejszy surface attack – mniej tokenów to mniejsze ryzyko ekstrakcji długich sekretów, niższy temperature redukuje kreatywność w obchodzeniu guardrails. Topic gate na cl=0 ogranicza zakres pytań do publicznie dostępnych informacji firmowych.

**Kompromisy:** Keyword-based topic filtering to PoC-grade rozwiązanie – produkcyjne wymagałoby LLM-judge (odłożone do Phase 10). `fake_invoke(msg, creds)` w istniejących testach wymagało dodania `policy=None` – backward-compatible. `test_clearance_boundary_zero` i `_event()` w test_step05 zaktualizowane pod topic gate (message z keyword "policy").

**Testy:** 52 testy unit+integracyjne w `test_step04_policy.py` + poprawki w 5 plikach test. 242/242 zielone, regresja zerowa.

## Step 05 – Testy bezpieczeństwa (end-to-end security validation)

**Co zrobiono:** Stworzono `tests/sanitizer/test_step05_security.py` z 64 testami pokrywającymi pięć kategorii: (1) wektory prompt injection – 17 testów (w tym 2 `xfail` dokumentujące znane ograniczenia: polskie ataki językowe i Base64 omijają regex-only detection); (2) wektory jailbreak – 11 testów; (3) PII leakage prevention – 12 testów (weryfikacja, że PII nie dociera do Bedroc i nie wycieka w odpowiedzi); (4) bezpieczeństwo topic gate – 5 testów; (5) edge cases – 12 testów (puste wejście, unicode: polski/emoji/arabski, bardzo długi input, null byte, SQL/HTML w body, tylko PII); (6) invarianty pipeline – 7 testów (kolejność warstw, struktura odpowiedzi 200/400/403).

**Dlaczego:** Testy jednostkowe walidują komponenty w izolacji; testy bezpieczeństwa Step 05 weryfikują właściwości całego pipeline'u end-to-end: że injection jest sprawdzana PRZED topic gate, PII jest redagowane PRZED sandwich, STS credentials nie wyciekają w odpowiedzi, Bedrock nie jest wywoływany dla zablokowanych żądań. Dokumentacja znanych ograniczeń (xfail) zapobiega fałszywemu poczuciu bezpieczeństwa.

**Kompromisy:** Regex-only detection ma znane bypassy: polskie wzorce injection, Base64/ROT13 encoding, leetspeak. Oznaczone jako `xfail` z komentarzem „semantic/entropy detection deferred to Phase 10". Topic keyword stuffing (wstawianie słowa „policy" w off-topic query) to znane ograniczenie PoC – akceptowalne, bo wymaga świadomego działania atakującego z dostępem do clearance 0.

**Testy:** 62 testy + 2 xfail = 64 łącznie w `test_step05_security.py`. Pełna regresja: 398/398 zielone (+ 2 xfail), regresja zerowa.

---
## Podsumowanie fazy 04

**Co zostało zbudowane:** Kompletna warstwa Input Security dla Lambda `sts-session`: moduł `lambda/sanitizer/` z biblioteką regex patterns (PII, injection, jailbreak), funkcją `scan_input/redact_pii`, builderem sandwich prompt, dataclassem `ClearancePolicy` z 5 poziomami clearance i topic gate dla cl=0. Wszystko spakowane w handler ZIP przez Terraform `archive_file` z wieloma blokami `source`.

**Jak działa:** Każde żądanie przechodzi przez 5 warstw: (1) sanitize – injection/jailbreak → 400, PII → redact; (2) topic gate – cl=0 off-topic → 403; (3) sandwich – wrap z [SYSTEM]/[USER]/[REMINDER] z department/clearance context; (4) STS AssumeRole z ABAC session tags; (5) Bedrock z per-clearance `max_tokens`/`temperature`/`top_p`. Każda warstwa testowana niezależnie i end-to-end.

**Co można dalej rozwinąć:**
- Semantyczna detekcja injection/jailbreak przez LLM-judge (Phase 10) – pokryje polskie ataki, encoding, parafrazowanie
- Output sanitization – skanowanie odpowiedzi Bedrock przed zwróceniem klientowi
- Rate limiting per user_id/jti – ochrona przed brute-force topic gate bypass
