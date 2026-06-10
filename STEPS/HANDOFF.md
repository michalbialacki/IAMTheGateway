# HANDOFF – 2026-06-10

## Stan projektu
- Aktualna faza: Phase 02 – Autentykacja i JWT ✅ UKOŃCZONA
- Ostatni ukończony etap: Step 05 – Testy end-to-end JWT
- Status testów: zielone (94 testów: 49 infra + 45 auth; 6 post-apply API GW skipped bez AWS)

## Następny krok
Phase 03 / Step 01 – IAM Role docelowa z ABAC

Zbudować/zweryfikować Terraform dla docelowej roli IAM (już istnieje jako `aws_iam_role.bedrock_scoped` w `terraform/iam.tf`) i napisać Lambda która:
1. Przyjmuje JWT context z `requestContext.authorizer` (user_id, department, clearance_level)
2. Wywołuje `sts.assume_role()` z session tags: `department`, `clearance_level`
3. Zwraca tymczasowe credentials STS
4. Weryfikuje że ABAC conditions na roli blokują dostęp bez tagów

## Otwarte kwestie
- Phase 03 Step 02: Lambda STS assume_role – wymagana jest Lambda "chat handler" która zastąpi MOCK integration w API Gateway
- Phase 03 Step 03: STS credentials cache – keyed `user_id:clearance_level`, TTL-aware (expiry STS minus 60s)
- Phase 03 Step 04: Bedrock boto3 client inicjalizowany z STS credentials per request
- `aws_iam_role.bedrock_scoped` już istnieje w terraform/iam.tf – sprawdzić czy ABAC conditions są kompletne przed Phase 03

## Zmodyfikowane pliki
lambda/authorizer/handler.py lambda/authorizer/requirements.txt lambda/revoke/handler.py scripts/build_authorizer_layer.py scripts/create_test_users.py scripts/get_jwt.py tests/auth/conftest.py tests/auth/test_step01_test_users.py tests/auth/test_step02_authorizer.py tests/auth/test_step04_revoke.py tests/auth/test_step05_e2e_jwt.py tests/infra/test_step07_api_gateway.py tests/conftest.py terraform/api_gateway.tf terraform/lambda_authorizer.tf terraform/lambda_revoke.tf terraform/iam.tf terraform/dynamodb.tf terraform/s3.tf terraform/cognito.tf terraform/cloudtrail.tf terraform/versions.tf terraform/main.tf terraform/variables.tf terraform/outputs.tf STEPS/PhasePlan.md STEPS/Phase01_WriteUp.md STEPS/Phase02_WriteUp.md STEPS/HANDOFF.md CLAUDE.md .gitignore pyproject.toml
