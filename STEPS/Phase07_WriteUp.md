# Phase 07 WriteUp – Python CLI Client

## Step 01 – Cognito login flow

**Co zrobiono:** Stworzono moduł `cli/auth.py` z trzema publicznymi elementami: `CognitoConfig` (frozen dataclass z `from_env()` odczytującym `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `AWS_REGION`), `AuthTokens` (frozen dataclass z `id_token`, `access_token`, `refresh_token`, `expires_in`), `AuthError` (user-facing wyjątek), `login()` (`USER_PASSWORD_AUTH` → `AuthTokens`) i `refresh()` (`REFRESH_TOKEN_AUTH` → `AuthTokens` z oryginalnym `refresh_token`). Stworzono `tests/cli/test_step01_auth.py` z 19 testami jednostkowymi.
**Dlaczego:** `IdToken` z `AuthenticationResult` to JWT z claims Cognito groups (`department`, `clearance_level`) — jedyny token akceptowany przez Lambda Authorizer w API Gateway. `refresh()` pozwala odnowić sesję bez ponownego logowania (refresh token żyje 30 dni).
**Kompromisy:** `USER_PASSWORD_AUTH` zamiast SRP — SRP jest bezpieczniejszy (hasło nigdy nie opuszcza klienta), ale wymaga implementacji protokołu Secure Remote Password. Dla CLI PoC `USER_PASSWORD_AUTH` jest wystarczający pod warunkiem HTTPS. Cognito nie rotuje refresh tokenów przy odświeżaniu — `refresh()` preservuje oryginalny token (udokumentowane w kodzie).
**Testy:** 19 unit testów (no AWS): `CognitoConfig.from_env()` (odczyt zmiennych, default region, brakujące zmienne), `login()` (success, poprawne parametry wywołania, region, 5 błędów Cognito), `refresh()` (success, reuse refresh token, poprawny flow, 2 błędy), `AuthTokens` immutability. Wynik: 19/19 zielone.

## Step 02 – JWT storage (keyring)

**Co zrobiono:** Dodano `keyring>=25.0.0` do `[project] dependencies` w `pyproject.toml`. Stworzono `cli/storage.py` z czterema funkcjami: `save_tokens(username, tokens)` (JSON blob → Windows Credential Manager), `load_tokens(username)` → `AuthTokens | None` (odczytuje i przelicza `expires_in` na podstawie zapisanego `expires_at` ISO UTC), `clear_tokens(username)` (cicha obsługa `PasswordDeleteError`), `needs_refresh(username, buffer_seconds=300)` → bool. Stworzono `tests/cli/test_step02_storage.py` z 18 testami.
**Dlaczego:** Przechowywanie tokenów w OS credential store zamiast pliku plaintext eliminuje ryzyko wycieku `IdToken`/`RefreshToken` w środowiskach wieloużytkownikowych. `expires_at` (timestamp absolutny) zamiast `expires_in` (czas względny) pozwala poprawnie obliczyć pozostały czas po restarcie CLI.
**Kompromisy:** `keyring` na Windowsie używa Windows Credential Manager — brak możliwości exportu wpisów przez CLI bez uprawnień administratora (to zaleta, nie wada). Na headless Linux fallback to `keyring.backends.fail.Keyring` który rzuca wyjątek — do obsługi w Step 05 (CLI loop).
**Testy:** 18 unit testów (keyring mock, no OS): `save_tokens` (wywołanie, service/username, pola JSON, expires_at timezone), `load_tokens` (None przy brak wpisu, happy path, expires_in jako remaining seconds, expired=0, corrupt JSON, brakujące pole, service/username), `clear_tokens` (wywołanie, PasswordDeleteError ignorowany), `needs_refresh` (brak tokenów, przed buforem, dokładnie na buforze, po buforze, custom buffer). Wynik: 18/18 zielone.

## Step 03 – Client-side regex scan (defense-in-depth)

**Co zrobiono:** Stworzono `cli/scan.py` — wrapper nad `sanitizer.scan_input` z wbudowanym `sys.path` setup (ten sam mechanizm co `tests/conftest.py`, działa zarówno w testach jak i przy uruchomieniu CLI standalone). Eksponuje `client_scan(message)` → `ScanResult` oraz `format_scan_warning(result)` → str (czytelny komunikat o przyczynie blokady). Stworzono `tests/cli/test_step03_scan.py` z 24 testami.
**Dlaczego:** Ten sam zestaw wzorców regex co serwer — naruszenia wykryte po stronie klienta dają natychmiastowe odrzucenie bez round-tripu do API Gateway. Defense-in-depth: serwer zawsze uruchomi swój własny scan niezależnie.
**Kompromisy:** Reużycie `lambda/sanitizer/` przez `sys.path` zamiast duplikacji kodu. Trade-off: coupling do struktury katalogowej repo (akceptowalne dla CLI w tym samym projekcie). Alternatywa — wydzielenie sanitizera do osobnego pakietu — wymaga przebudowy struktury projektu, nieopłacalna na tym etapie PoC.
**Testy:** 24 testy bez mocków (regex są pure Python): czyste wiadomości (clean, puste, unicode), PII (redakcja email/IP/PESEL, is_clean=True przy samym PII), injection (7 wzorców), jailbreak (4 wzorce), `format_scan_warning` (injection, jailbreak, multiple findings, clean message). Wynik: 24/24 zielone.

## Step 04 – HTTPS request do API Gateway

**Co zrobiono:** Przeniesiono `requests>=2.32.0` z `[dependency-groups] dev` do `[project] dependencies` (jest potrzebny w runtime CLI). Stworzono `cli/gateway.py` z `ChatResponse` (frozen dataclass), `GatewayError` (wyjątek z `status_code`), `send_message(message, id_token, api_url, session_id=None, timeout=30)`. Funkcja wysyła POST z `Authorization: Bearer {id_token}`, opcjonalnym `session_id` w body, obsługuje kody 400/401/403/502 i sieciowe `Timeout`/`ConnectionError`. Stworzono `tests/cli/test_step04_gateway.py` z 17 testami.
**Dlaczego:** Thin HTTP client bez logiki biznesowej — jedynym zadaniem jest serializacja requestu i deserializacja `ChatResponse`. Cała logika auth/sanitize/session jest w dedykowanych modułach. `GatewayError.status_code` pozwala CLI loop (Step 05) reagować inaczej na 401 (re-login) vs 403 (block message) vs 503 (retry).
**Kompromisy:** `session_id` pomijany w body gdy `None` (nie wysyłany jako `null`) — serwer generuje nowe UUID wtedy. `timeout=30` jako default — wystarczający dla Bedrock RetrieveAndGenerate, konfigurowalne per-call dla testów integracyjnych.
**Testy:** 17 unit testów (requests.post mock): success (ChatResponse pola, Bearer header, URL, body, session_id present/absent, timeout default/custom), HTTP errors (400 z details, 401 re-login message, 403 denied, 502 AI service, 500 unexpected), network (Timeout→504, ConnectionError→503), malformed 200 (brakujące pole), `ChatResponse` immutability. Wynik: 17/17 zielone.

## Step 05 – CLI loop

**Co zrobiono:** Stworzono `cli/main.py` z injectable I/O (`input_fn`, `print_fn`, `password_getter`) dla testowalności. Kluczowe funkcje: `get_api_url()` (odczyt `CHAT_API_URL`), `prompt_login()` (`getpass` + `save_tokens`), `ensure_tokens()` (load → needs_refresh → refresh → fallback login), `run_chat_loop()` (główna pętla). Pętla obsługuje: `/exit` `/quit` `/logout` `/session`, puste wejście, scan blocking, PII warning, auto-refresh przed wysłaniem, propagację `session_id` między turnami, 401 re-auth mid-session. Entry point `main()` z argparse (`--username`/env `COGNITO_USERNAME`). Stworzono `tests/cli/test_step05_cli_loop.py` z 18 testami.
**Dlaczego:** Injectable I/O (`input_fn`, `print_fn`) pozwala testować całą pętlę bez terminala. `getpass` zastępowane `password_getter` w testach. Reużywa wszystkich modułów z poprzednich kroków — `main.py` to tylko orkiestrator.
**Kompromisy:** `ensure_tokens` łapie `AuthError` (nie `Exception`) — intencjonalne: inne wyjątki (np. `ConnectionError` boto3) powinny propagować do wywołującego a nie być cicho wchłaniane jako "login required". Odkryto podczas testów że `"Ignore all previous instructions"` nie matchuje wzorca (słowo `previous` po `all` nie jest obsługiwane) — istniejące ograniczenie patterns, wchodzi w zakres xfail z Phase 04.
**Testy:** 18 unit testów (mocked I/O, auth, storage, gateway): `get_api_url` (env read, exit when missing), `ensure_tokens` (fresh cache, silent refresh, fallback to login, no tokens), loop (exit/quit/EOF, logout+clear, /session, injection blocked, puste wejście, PII warning, happy path response, session_id propagated across turns, 403 continue, 401 re-auth). Wynik: 18/18 zielone. Łącznie tests/cli/: 96/96.

## Step 06 – Integration tests (full-flow)

**Co zrobiono:** Stworzono `tests/cli/test_step06_integration.py` z `pytestmark = pytest.mark.integration`. Plik zawiera 13 testów w 4 grupach: auth (login sukces, struktura JWT, błędne hasło/username), full-flow eng user (ChatResponse pola, clearance_level range), session continuity (reuse session_id, nowa sesja = nowy ID), finance user (department field), nieautoryzowany token (401), client-side scan (3 testy zawsze zielone — czyste regex, brak AWS). Credentials i API URL z env vars (`INT_TEST_USER_ENG`, `INT_TEST_PASS_ENG`, `INT_TEST_USER_FIN`, `INT_TEST_PASS_FIN`, `CHAT_API_URL`).
**Dlaczego:** AOSS wyłączone w tej sesji (stop_costs.ps1) — pełny flow wymaga KB. Testy napisane teraz, uruchamiane w Phase 08 gdy AOSS wróci (~$1, ~2h). Fixture scope=module minimalizuje liczbę Cognito `InitiateAuth` call (jeden login per moduł, nie per test).
**Kompromisy:** 10 testów pomijanych domyślnie (`-m "not integration"`) — nie blokuje CI. 3 testy client-side scan zawsze zielone bo nie wymagają AWS. Fixture `_require_env` wywołuje `pytest.skip` zamiast `pytest.fail` — poprawne zachowanie gdy infra celowo wyłączona.
**Testy:** 99/99 passed, 10 skipped (integration, oczekiwane). 3 client-side scan testy przeszły bez AWS.

---
## Podsumowanie fazy 07

**Co zostało zbudowane:** Kompletny Python CLI client w module `cli/` — 5 modułów (`auth`, `storage`, `scan`, `gateway`, `main`) plus plik integration testów. CLI obsługuje pełny cykl: login Cognito → zapis JWT do Windows Credential Manager → client-side regex scan → HTTPS POST do API Gateway → wyświetlenie odpowiedzi z historią konwersacji → auto-refresh tokenu.

**Jak działa:** `main.py` orkiestruje wszystkie moduły: `ensure_tokens` ładuje lub odświeża JWT z keyring; `client_scan` filtruje injection/jailbreak i redaktuje PII przed wysłaniem; `send_message` wysyła POST z `Authorization: Bearer {id_token}` i deserializuje `ChatResponse`; `session_id` z każdej odpowiedzi trafia do następnego requestu zapewniając ciągłość konwersacji.

**Co można dalej rozwinąć:**
- Phase 08: uruchomienie integration testów Step 06 z AOSS — potwierdzenie pełnego e2e flow
- Obsługa headless Linux (keyring fallback) — `keyring.backends.fail.Keyring` rzuca wyjątek bez GUI
- `--department`/`--clearance` flagi do wyświetlania ABAC context w CLI bez wysyłania wiadomości
