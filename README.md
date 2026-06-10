# IAM The Gateway

**Multitenant GenAI Gateway z izolacją IAM i ABAC**

Proof-of-concept bramki dostępu do Amazon Bedrock z wielowarstwową izolacją między dzierżawcami. Każde żądanie do modelu przechodzi przez weryfikację JWT, generowanie tymczasowych poświadczeń STS z tagami sesji ABAC oraz filtrowanie wiedzy przez metadane dokumentów w oparciu o poziom uprawnienia użytkownika.

---

## Architektura

```
Klient (CLI)
    │  Authorization: Bearer {JWT}
    ▼
API Gateway (REST) ──► Lambda Authorizer
    │                       │ weryfikacja RS256, JWKS Cognito
    │                       │ sprawdzenie jti w DynamoDB (revocation)
    │                       └─ parsowanie grup ABAC → department / clearance_level
    ▼
Lambda Chat Handler
    │  sts.assume_role() z session tags (department, clearance_level)
    ▼
Bedrock RetrieveAndGenerate
    │  metadataFilter: department == X AND clearance_level <= N
    ▼
S3 Knowledge Base (dokumenty + sidecar .metadata.json)
```

Kluczowe właściwości:
- **Zero hardkodowanych uprawnień** – dostęp wynika wyłącznie z JWT claims i ABAC conditions na roli IAM
- **Revocation w czasie rzeczywistym** – `jti` sprawdzany przy każdym wywołaniu (TTL=0), unieważniany przez endpoint `/revoke`
- **Izolacja na poziomie danych** – Bedrock filtruje dokumenty przez `clearance_level` i `department` zanim zwróci wynik

---

## Stack

| Warstwa | Technologia |
|---------|-------------|
| Infrastruktura | Terraform ≥ 1.7, AWS Provider ~5.50 |
| Autentykacja | Amazon Cognito User Pools + Groups |
| API | API Gateway REST (REGIONAL), Lambda Authorizer TOKEN |
| Autoryzacja | IAM ABAC – session tags przez STS AssumeRole |
| Logika biznesowa | Python 3.12 Lambda |
| Baza danych | DynamoDB (sessions, conversation_history, revoked_tokens) |
| Wiedza | Amazon Bedrock Knowledge Base + S3 (SSE-KMS) |
| Obserwability | CloudTrail (multi-region) + CloudWatch Logs + metric filters |
| Testy | Pytest, PyJWT, cryptography, requests |

---

## Model uprawnień ABAC

Grupy Cognito mają format `dept_{department}_cl_{clearance_level}`:

| Clearance | Wartość | Przykładowa grupa |
|-----------|---------|-------------------|
| unclassified | 0 | `dept_hr_cl_0` |
| classified | 1 | `dept_legal_cl_1` |
| restricted | 2 | `dept_engineering_cl_2` |
| secret | 3 | `dept_finance_cl_3` |
| top_secret | 4 | `dept_security_cl_4` |

Lambda Authorizer parsuje grupę i przekazuje do API Gateway context:

```json
{
  "user_id": "cognito-sub",
  "department": "engineering",
  "clearance_level": "2",
  "jti": "uuid-tokenu"
}
```

---

## Struktura projektu

```
IAMTheGateway/
├── terraform/
│   ├── bootstrap/              # Zdalny backend S3 + DynamoDB lock (jednorazowo)
│   ├── main.tf                 # Provider, locals, tagi domyślne
│   ├── variables.tf            # Region, project_name, environment, clearance_levels
│   ├── iam.tf                  # Role: lambda_exec + bedrock_scoped (ABAC conditions)
│   ├── dynamodb.tf             # sessions, conversation_history, revoked_tokens (TTL)
│   ├── s3.tf                   # Knowledge Base bucket (SSE-KMS, deny-non-TLS)
│   ├── cognito.tf              # User Pool + ABAC Groups + App Client
│   ├── cloudtrail.tf           # CloudTrail + CloudWatch metric filters
│   ├── lambda_authorizer.tf    # Lambda Authorizer + IAM role + Layer
│   ├── lambda_revoke.tf        # Lambda Revoke + endpoint /revoke
│   ├── api_gateway.tf          # REST API, /chat POST, throttling, stage prod
│   └── outputs.tf
│
├── lambda/
│   ├── authorizer/handler.py   # JWT verify (RS256 + JWKS), revocation, ABAC context
│   └── revoke/handler.py       # Admin: zapisuje jti do revoked_tokens (guard cl≥3)
│
├── scripts/
│   ├── build_authorizer_layer.py  # Buduje layer.zip przez Docker (Linux wheels)
│   ├── create_test_users.py       # Tworzy użytkowników testowych w Cognito
│   └── get_jwt.py                 # Login Cognito → Tokens dataclass
│
├── tests/
│   ├── infra/                  # terraform validate + boto3 post-apply
│   └── auth/                   # Unit (pełny mock) + E2E (live AWS)
│
└── STEPS/
    ├── PhasePlan.md            # Plan wszystkich faz
    ├── Phase0X_WriteUp.md      # Dokumentacja ukończonych faz
    └── HANDOFF.md              # Stan projektu na koniec sesji
```

---

## Pierwsze uruchomienie

### Wymagania

- Python 3.12+, [uv](https://github.com/astral-sh/uv)
- Terraform ≥ 1.7
- Docker Desktop (do budowania Lambda Layer – wymagany raz)
- AWS CLI z skonfigurowanymi credentials
- Uprawnienia AWS: IAM, Lambda, Cognito, API Gateway, DynamoDB, S3, Bedrock, CloudTrail

### 1. Backend Terraform (jednorazowo)

```powershell
Set-Location terraform\bootstrap
terraform init && terraform apply
```

Skopiuj `terraform/backend.tf.example` → `terraform/backend.tf` i uzupełnij outputami z powyższego apply.

### 2. Zmienne projektu

```powershell
Copy-Item terraform\terraform.tfvars.example terraform\terraform.tfvars
# Edytuj: aws_region, project_name, environment
```

### 3. Deploy infrastruktury

```powershell
Set-Location terraform
terraform init
terraform plan
terraform apply
```

### 4. Zbuduj Lambda Layer

```powershell
# Wymaga uruchomionego Docker Desktop
python scripts/build_authorizer_layer.py
terraform apply   # wgrywa layer do AWS
```

### 5. Utwórz użytkowników testowych

```powershell
uv run python scripts/create_test_users.py
```

Tworzy: `alice@test.local` (engineering / cl=2), `bob@test.local` (legal / cl=1), `eve@test.local` (security / cl=4).

---

## Uruchamianie testów

```powershell
# Testy lokalne (bez AWS): terraform validate + unit testy z mockami
uv run pytest tests/infra/ tests/auth/test_step02_authorizer.py tests/auth/test_step04_revoke.py -v

# Pełna suita (wymaga AWS credentials + zadeplojowanej infrastruktury)
uv run pytest tests/ -v
```

Aktualna liczba testów: **94** (49 infra + 45 auth, w tym 6 E2E na live AWS).

---

## Plan faz

| Faza | Zakres | Status |
|------|--------|--------|
| **Phase 01** | Infrastruktura bazowa: Terraform, IAM ABAC, DynamoDB, S3, Cognito, CloudTrail | ✅ Ukończona |
| **Phase 02** | Autentykacja i JWT: Lambda Authorizer, API Gateway REST, revocation endpoint | ✅ Ukończona |
| **Phase 03** | IAM Session Generation: STS AssumeRole z ABAC session tags, cache credentials | 🔄 Następna |
| **Phase 04** | Input Security: sanitizacja PII/prompt injection, sandwich method, limity per clearance | ⏳ Planowana |
| **Phase 05** | Bedrock Knowledge Base z ABAC: metadataFilter (department + clearance_level) | ⏳ Planowana |
| **Phase 06** | Conversation Management: DynamoDB, historia 3–5 ostatnich wymian per sesja | ⏳ Planowana |
| **Phase 07** | Python CLI Client: Cognito login, keyring, client-side sanitize, HTTPS do APIGW | ⏳ Planowana |
| **Phase 08** | CI/CD + E2E Tests: GitHub Actions, security hardening audit, dependency scan | ⏳ Planowana |

**Future scope (poza PoC):**
- Phase 09 – KMP Android Client (Cognito Amplify, Android Keystore, Jetpack Compose)
- Phase 10 – Output sanitization (Bedrock Guardrails lub LLM-judge jako post-processor)
- Phase 11 – Semantic Cache z injection detection dla historii konwersacji

---

## Bezpieczeństwo

- **Żadnych sekretów w kodzie** – klucze przez AWS Secrets Manager i `.env` (gitignored)
- **Stan Terraform** – S3 z SSE-KMS + DynamoDB lock, dostęp przez IAM
- **Lambda Authorizer TTL=0** – revocation działa natychmiastowo (brak cache'owania w APIGW)
- **Least privilege** – każda Lambda ma osobną rolę IAM z minimalnym zakresem (GetItem / PutItem, nie pełne CRUD)
- **Deny non-TLS** – S3 bucket policy odrzuca żądania bez `aws:SecureTransport`
- **CloudTrail** – wszystkie regiony, log file validation enabled, metric filters dla `UnauthorizedAccess` i `AssumeRole`

Szczegóły: `STEPS/SecAnalysis.md`, `STEPS/KnownLimitations.md`.

---

## Licencja

Projekt edukacyjny / PoC. Nie przeznaczony do użycia produkcyjnego bez audytu bezpieczeństwa.
