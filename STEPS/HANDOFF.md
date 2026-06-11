# HANDOFF – 2026-06-11

## Stan projektu
- Aktualna faza: Phase 04 – Input Security (Sanitization + Sandwich Method) — **UKOŃCZONA**
- Ostatni ukończony etap: Step 05 – Testy bezpieczeństwa (prompt injection, PII leakage, jailbreak, edge cases, pipeline invariants)
- Status testów: zielone (398/398 + 2 xfailed)
- Infrastruktura: `terraform apply` zastosowany – Lambda ZIP z pełnym modułem `sanitizer/` wdrożona

## Następny krok
Phase 05 – (do zaplanowania w następnej sesji na podstawie PhasePlan.md)

## Otwarte kwestie
- **Bedrock model access** – wymagane ręczne włączenie w Bedrock console → Model Access → "Amazon Titan Text Express v1". Bez tego e2e testy akceptują 502 jako "auth passed".
- **Cache key nie zawiera `department`** – jeśli user zmieni dział między warm invocations, dostanie credentials scoped do starego działu przez max 840s. Odłożone do Phase 05.
- **Znane ograniczenia regex-only detection** (udokumentowane jako xfail w test_step05_security.py):
  - Polskie wzorce injection (np. "Zignoruj poprzednie instrukcje") – nie wykrywane
  - Base64/ROT13-encoded injection – nie wykrywane
  - Obie klasy zaadresowane semantyczną detekcją w Phase 10

## Zmodyfikowane pliki (Phase 04)
lambda/sts/handler.py lambda/sanitizer/__init__.py lambda/sanitizer/patterns.py lambda/sanitizer/sanitizer.py lambda/sanitizer/sandwich.py lambda/sanitizer/policy.py terraform/lambda_sts.tf tests/conftest.py tests/sanitizer/test_step01_patterns.py tests/sanitizer/test_step02_lambda_sanitize.py tests/sanitizer/test_step03_sandwich.py tests/sanitizer/test_step04_policy.py tests/sanitizer/test_step05_security.py tests/sts/test_step01_iam_abac.py tests/sts/test_step04_bedrock_client.py tests/sts/test_step05_scope_isolation.py STEPS/Phase04_WriteUp.md STEPS/HANDOFF.md
