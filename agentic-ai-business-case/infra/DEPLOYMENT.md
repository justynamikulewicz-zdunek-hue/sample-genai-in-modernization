# MAP Agentic Accelerator — Dokumentacja wdrożenia

> Multi-tenant blueprint OpenTofu dla **AWS Migration Business Case Generator**
> (9-agentowa architektura Claude na ECS Fargate + Bedrock).
> Migracja z monolitycznego CloudFormation → modularny OpenTofu (Terraform).

---

## 1. Co zbudowaliśmy (architektura)

```
Internet
   │ HTTPS 443 (self-signed cert)
   ▼
┌─────────────────────────────────────────────┐
│  ALB (public subnets)  ── HTTP 80 → 301 ──┐  │
│         │ forward :8080                    │  │
│         ▼                                  │  │
│  ECS Fargate (private subnets)             │  │
│   └─ kontener business-case-generator      │  │
│        :8080  (9 agentów Claude)           │  │
└────────┬───────────────┬──────────┬────────┘  │
         │ Bedrock       │ S3       │ DynamoDB   │
         ▼               ▼          ▼            │
   eu.claude-sonnet  input/output  cases table  │
                                                 │
  NAT Gateway (1x, koszt-opt) ── private → internet
  Cognito User Pool (admin-create only)
  ECR (obraz Dockera) ← CodeBuild ← GitHub
```

**Każdy zasób ma prefix `${client_name}-`** (np. `acme-ecs-cluster`) → pełna izolacja między klientami.

### Mapowanie CloudFormation → OpenTofu
| CF Lambda custom resource | Zastąpione przez |
|---|---|
| `ECRCleanupLambda` | `force_delete = true` na `aws_ecr_repository` |
| `SelfSignedCertLambda` | provider `tls_self_signed_cert` + `aws_acm_certificate` |
| `GetClientSecretLambda` | atrybut `.client_secret` na `aws_cognito_user_pool_client` |
| `BuildTriggerLambda` | `aws codebuild start-build` (ręcznie / CI/CD) |

---

## 2. Wymagania lokalne (zainstalowane)

| Narzędzie | Komenda instalacji | Weryfikacja |
|---|---|---|
| AWS CLI v2 | `brew install awscli` | `aws --version` |
| OpenTofu | `brew install opentofu` | `tofu --version` |
| Git | `brew install git` | `git --version` |
| jq | `brew install jq` | `jq --version` |
| Colima + Podman | `brew install colima podman` | (lokalny test obrazu, opcjonalnie) |

---

## 3. Struktura projektu

```
agentic-ai-business-case/infra/
├── bootstrap/              # JEDNORAZOWO: state bucket, lock table, IAM deployer, OIDC
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
├── backend.tf             # S3 remote state (partial config)
├── main.tf                # wywołania 9 modułów
├── variables.tf           # client_name, region, admin_email, cpu, memory
├── outputs.tf             # ALB URL, Cognito domain, ECR URL, next_steps
├── backends/
│   └── acme.hcl           # per-klient: klucz state w S3
├── clients/
│   └── acme.tfvars        # per-klient: wartości zmiennych
└── modules/
    ├── vpc/               # VPC, subnets, IGW, 1x NAT GW, route tables, SG
    ├── ecr/               # repo Dockera (force_delete, lifecycle 5 obrazów)
    ├── iam/               # ECS Task Execution Role + ECS Task Role + CodeBuild Role
    ├── s3/                # input + output bucket (versioning, SSE)
    ├── dynamodb/          # cases table + GSI UserIdIndex (PAY_PER_REQUEST, PITR)
    ├── cognito/           # User Pool, domain, app client, admin user
    ├── alb/               # ALB, target group, HTTPS+HTTP listener, self-signed cert
    ├── ecs/               # cluster, task definition, Fargate service
    └── codebuild/         # projekt budujący obraz z GitHub → ECR
```

---

## 4. Co zrobiliśmy — krok po kroku

### Krok 1 — Logowanie do AWS przez SSO
Konto **STX Next DevOps (680696743786)** używa IAM Identity Center (region `eu-central-1`).

Profil w `~/.aws/config`:
```ini
[sso-session stxnext]
sso_start_url = https://stxnext.awsapps.com/start
sso_region = eu-central-1
sso_registration_scopes = sso:account:access

[profile stxnext-devops]
sso_session = stxnext
sso_account_id = 680696743786
sso_role_name = AdministratorAccess
region = eu-north-1
output = json
```

Logowanie:
```bash
aws sso login --profile stxnext-devops
aws sts get-caller-identity --profile stxnext-devops   # weryfikacja
```

### Krok 2 — Bootstrap (jednorazowo)
Tworzy fundament: S3 na state, DynamoDB lock, IAM role `terraform-deployer`, GitHub OIDC.
State bootstrapu jest **lokalny** (nie ma jeszcze gdzie trzymać zdalnego).

```bash
cd agentic-ai-business-case/infra/bootstrap
tofu init
AWS_PROFILE=stxnext-devops tofu apply -auto-approve
```

Utworzone:
- S3 bucket: `map-accelerator-tfstate-680696743786`
- DynamoDB lock: `terraform-state-lock`
- IAM role: `arn:aws:iam::680696743786:role/terraform-deployer`
- OIDC provider: `token.actions.githubusercontent.com`

### Krok 3 — Profil terraform-deployer (best practice)
Zamiast deployować bezpośrednio rolą Admina, używamy dedykowanej roli z granularnymi uprawnieniami.
Dodane do `~/.aws/config`:
```ini
[profile terraform-deployer]
role_arn = arn:aws:iam::680696743786:role/terraform-deployer
source_profile = stxnext-devops
region = eu-north-1
output = json
```

Weryfikacja AssumeRole:
```bash
aws sts get-caller-identity --profile terraform-deployer
# Arn: .../assumed-role/terraform-deployer/...
```

### Krok 4 — Konfiguracja klienta
Dwa pliki na klienta (tu: `acme`):

`backends/acme.hcl` (gdzie trzymać state):
```hcl
bucket         = "map-accelerator-tfstate-680696743786"
key            = "clients/acme/terraform.tfstate"
region         = "eu-north-1"
dynamodb_table = "terraform-state-lock"
encrypt        = true
profile        = "terraform-deployer"
```

`clients/acme.tfvars` (parametry):
```hcl
client_name      = "acme"
admin_email      = "justyna.mikulewicz-zdunek@stxnext.pl"
aws_region       = "eu-north-1"
aws_profile      = "terraform-deployer"
container_cpu    = 2048
container_memory = 4096
```

### Krok 5 — Wdrożenie infrastruktury
```bash
cd agentic-ai-business-case/infra
tofu init -backend-config=backends/acme.hcl
tofu plan  -var-file=clients/acme.tfvars     # 55 zasobów do utworzenia
tofu apply -var-file=clients/acme.tfvars     # wpisz: yes  (~8 min, NAT GW najdłużej)
```

### Krok 6 — Budowa i push obrazu Dockera
CodeBuild klonuje repo z GitHub (branch `main`), buduje `infrastructure/Dockerfile`, pushuje do ECR.
```bash
aws codebuild start-build \
  --project-name acme-business-case-build \
  --source-version main \
  --region eu-north-1 --profile terraform-deployer
```
Po wypchnięciu obrazu ECS automatycznie pobiera `:latest` i uruchamia kontener.

---

## 5. Codzienna obsługa

### Sprawdź adres aplikacji
```bash
cd agentic-ai-business-case/infra
tofu output alb_url
```

### Oszczędzanie — zatrzymaj compute (ECS) gdy nie używasz
```bash
# STOP (Fargate $0)
aws ecs update-service --cluster acme-ecs-cluster \
  --service acme-business-case-service --desired-count 0 \
  --region eu-north-1 --profile terraform-deployer

# START (przed demo)
aws ecs update-service --cluster acme-ecs-cluster \
  --service acme-business-case-service --desired-count 1 \
  --region eu-north-1 --profile terraform-deployer
```

### Pełne wyłączenie (koniec dnia / weekend)
```bash
cd agentic-ai-business-case/infra
tofu destroy -var-file=clients/acme.tfvars     # wpisz: yes
```
Bootstrap (`infra/bootstrap/`) **zostaje** — kosztuje ~$0 i pozwala wrócić jednym `apply`.

### Odtworzenie po weekendzie
```bash
cd agentic-ai-business-case/infra
tofu apply -var-file=clients/acme.tfvars
aws codebuild start-build --project-name acme-business-case-build \
  --source-version main --region eu-north-1 --profile terraform-deployer
```

### Nowy klient (np. `globex`)
```bash
cp backends/acme.hcl  backends/globex.hcl     # zmień key → clients/globex/...
cp clients/acme.tfvars clients/globex.tfvars  # zmień client_name = "globex"
tofu init  -backend-config=backends/globex.hcl -reconfigure
tofu apply -var-file=clients/globex.tfvars
```

---

## 6. Koszty (eu-north-1, orientacyjnie)

| Zasób | Koszt/dzień | Uwaga |
|---|---|---|
| NAT Gateway | ~$1.20 | zawsze działa |
| ALB | ~$0.60 | zawsze działa |
| ECS Fargate (2vCPU/4GB) | ~$2.40 | tylko gdy desired_count=1 |
| S3/DynamoDB/ECR/Cognito/Logs | ~$0.10 | groszowe |
| **Razem (ECS on)** | **~$4.30/dzień** | |
| **Razem (ECS off)** | **~$1.90/dzień** | |
| **Po `tofu destroy`** | **$0** | bootstrap ~$0 |

Bedrock = pay-per-use (~$3 / 1M tokenów input dla Claude Sonnet).

---

## 7. Napotkane problemy i rozwiązania

| Problem | Przyczyna | Rozwiązanie |
|---|---|---|
| `InvalidRequestException` przy `aws sso login` | zły `sso_region` | Identity Center STX Next jest w `eu-central-1` |
| `AccessDenied` na `sts:AssumeRole` | zbyt restrykcyjny warunek w trust policy (format ARN SSO `sts::` vs `iam::`) | trust policy na `:root`, uprawnienie kontrolowane po stronie SSO |
| `VpcLimitExceeded` w eu-west-1 | 5/5 VPC zajęte | zmiana regionu na `eu-north-1` |
| Zmiana regionu → błędy `PermanentRedirect`/`Invalid Region in ARN` | state trzymał ARN-y ze starego regionu | `tofu destroy` w starym regionie, potem `apply` w nowym |
| `Character sets beyond ASCII` na Security Group | em-dash (—) w polu `description` | tylko ASCII w opisach SG |
| CodeBuild `reference not found ... feature/terraform-migration` | branch tylko lokalnie, nie w forku | build z brancha `main` (tam jest kod aplikacji) |
| Brak Claude 3 Sonnet w eu-north-1 | region ma tylko Claude 4/4.5/5 | kod aplikacji używa `eu.anthropic.claude-sonnet-4-5` (cross-region inference) — kompatybilne |

---

## 8. IAM — dwie kluczowe role ECS

**ECS Task Execution Role** (`acme-ecs-task-execution-role`) — używana przez AWS przed startem aplikacji:
- `AmazonECSTaskExecutionRolePolicy` (pull obrazu z ECR, logi do CloudWatch)
- odczyt SSM SecureString (sekret klienta Cognito)

**ECS Task Role** (`acme-ecs-task-role`) — uprawnienia kodu agentów:
- `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` (foundation-model/* + inference-profile/*)
- `s3:GetObject/PutObject/DeleteObject/ListBucket` (oba buckety)
- `dynamodb:GetItem/PutItem/UpdateItem/DeleteItem/Query/Scan` (tabela + GSI)
- `pricing:*` (kalkulacja kosztów migracji)
- `savingsplans:Describe*` (modelowanie finansowe)

---

## 9. TODO (następne kroki)

- [ ] Commit `infra/` do forka na branch `feature/terraform-migration`
- [ ] GitHub Actions workflow (`.github/workflows/terraform.yml`): `plan` na PR, `apply` na merge (przez OIDC role `terraform-deployer`)
- [ ] Po wdrożeniu zweryfikować health: `curl -k $(tofu output -raw alb_url)/api/health`
- [ ] Pierwszy login: admin dostaje tymczasowe hasło na email (Cognito)
