# Phase 06 WriteUp – Conversation Management (DynamoDB)

## Step 01 – SessionId generation

**Co zrobiono:** Dodano `import uuid` do `lambda/sts/handler.py` i generację `session_id = str(uuid.uuid4())` w `lambda_handler` po walidacji ABAC context, przed wywołaniem STS. `session_id` zwracany jest w każdej odpowiedzi 200 obok `user_id`, `department`, `clearance_level` i `response`. Stworzono `tests/conversation/test_step01_session_id.py` z 10 testami jednostkowymi.
**Dlaczego:** UUID v4 jako unikalny identyfikator sesji per request — fundament pod zapis historii konwersacji w DynamoDB (Step 02). Klient otrzymuje `session_id` i może go przesyłać w kolejnych requestach do kontynuacji sesji.
**Kompromisy:** Aktualnie `session_id` generowany jest per request (nowa sesja przy każdym wywołaniu). W Step 02 logika zostanie rozszerzona: jeśli klient prześle `session_id` w body requestu, handler będzie go reużywać zamiast generować nowy.
**Testy:** 10 unit testów (no AWS): obecność pola, format UUID v4, unikalność (5x ten sam user → 5 różnych ID), brak `session_id` w odpowiedziach błędów (400/403), korelacja z `user_id`. Wynik: 10/10 zielone.

## Step 02 – DynamoDB: zapis i odczyt historii konwersacji

**Co zrobiono:** Dodano lazy DynamoDB client `_get_dynamodb()`, funkcję `_save_exchange(session_id, user_id, user_msg, assistant_msg)` (PutItem z `turn_index` = epoch millis, TTL 24h) oraz `_load_history(session_id, limit=5)` (Query DESC, odwrócone do porządku chronologicznego). W `lambda_handler`: opcjonalne przyjęcie `session_id` z body requestu (reuse istniejącej sesji), przechwycenie `user_msg_clean` PRZED sandwich wrappingiem, zapis wymiany po odpowiedzi Bedrock. Dodano `CONVERSATION_TABLE` do env vars Lambdy w `lambda_sts.tf`.
**Dlaczego:** Fundament historii konwersacji — każda wymiana jest trwale zapisana w DynamoDB z TTL 24h. Klient może kontynuować sesję przesyłając `session_id` z poprzedniej odpowiedzi. `_load_history` jest gotowe do użycia w Step 03 (wstrzyknięcie kontekstu do promptu).
**Kompromisy:** `turn_index` = epoch millis zamiast sekwencyjnego licznika — eliminuje read-before-write, ryzyko kolizji w ciągu tej samej milisekundy jest akceptowalne dla projektu PoC. Pre-sandwich `user_msg_clean` zapisywany w historii — czytelny dla użytkownika, sandwich to detal techniczny niewidoczny w historii.
**Testy:** 23 unit testy (no AWS): `_save_exchange` (PutItem payload, TTL > teraz+1h), `_load_history` (kolejność chronologiczna, Query DESC, graceful przy brakującym env), handler (save na 200, brak save na 400/403/502, reuse session_id, spójność ID między response a DynamoDB). Wynik: 33/33 zielone (Step 01 + 02).

## Step 03 – Historia wstrzykiwana do prompt context

**Co zrobiono:** Rozszerzono `build_sandwich_prompt` o opcjonalny parametr `history: list[dict] | None = None` i wewnętrzną funkcję `_format_history` — formatuje historię jako sekcję `[CONVERSATION HISTORY]` z numerowanymi turnami (Turn N: / User: / Assistant:) wstawianą między blok [SYSTEM] a [USER]. W `lambda_handler`: przeniesiono resolwowanie `session_id` przed sandwich, dodano `history = _load_history(session_id)` i przekazano do `build_sandwich_prompt(message, department, cl, history=history)`. Backward compat zachowany — wywołania bez `history` dają identyczny wynik.
**Dlaczego:** Model widzi kontekst poprzednich wymian bezpośrednio w prompcie — umożliwia odpowiedzi uwzględniające wcześniejsze pytania w tej samej sesji. Wstrzyknięcie między [SYSTEM] a [USER] zachowuje strukturę sandwicza i priorytet instrukcji systemowych.
**Kompromisy:** Historia ładowana przy każdym requeście (nie cache'owana w kontenerze Lambda). Akceptowalne — DynamoDB latency < 5ms, a brak cache eliminuje ryzyko serwowania przeterminowanej historii przy nowym `session_id`.
**Testy:** 17 unit testów (no AWS): sandwich z historią (sekcja, kolejność, Turn N, User/Assistant labels, pozycja przed [USER], po [SYSTEM], backward compat), handler (load_history z poprawnym session_id, historia w prompcie, brak sekcji gdy pusta, position check). Wynik: 50/50 zielone (Step 01+02+03).

---
## Podsumowanie fazy 06

**Co zostało zbudowane:** Pełny system zarządzania konwersacją oparty na DynamoDB — każda wymiana user/assistant jest persistowana z TTL 24h i `turn_index` (epoch millis). Klient może kontynuować sesję przez przesłanie `session_id` z poprzedniej odpowiedzi; historia ładowana jest automatycznie i wstrzykiwana do promptu Bedrock.

**Jak działa:** Request trafia do Lambda → `session_id` resolve (reuse lub UUID v4) → `_load_history` pobiera ostatnie 5 wymian z DynamoDB → `build_sandwich_prompt` buduje prompt z historią między blokiem systemowym a pytaniem użytkownika → Bedrock odpowiada z pełnym kontekstem → `_save_exchange` zapisuje nową wymianę do DynamoDB. Cały flow jest synchroniczny, bez dodatkowych round-tripów poza jednym Query i jednym PutItem.

**Co można dalej rozwinąć:**
- Konfigurowalna głębokość historii per clearance level (wyższy clearance → więcej kontekstu)
- Kompresja historii przy przekroczeniu limitu tokenów (summarize oldest turns)
- Endpoint `DELETE /session/{id}` do jawnego kończenia sesji przez użytkownika
