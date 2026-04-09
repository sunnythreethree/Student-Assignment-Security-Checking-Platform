# SAST Platform — Student Assignment Security Checker

**CS6620 Group 9** | Jingsi (Jess) Zhang · Mengshan (Sunny) Li · Jiahua (Liz) Wu

A serverless static analysis platform on AWS. Students submit source code via a web UI; the system scans it asynchronously using Bandit (Python) and Semgrep (Java / JavaScript / TypeScript / Go / Ruby / C / C++), stores the report in S3, and returns a presigned download URL to the browser.

---

## Quick Start

**Deploy the full platform in one command:**

```bash
# 1. Clone the repo
git clone https://github.com/sunnythreethree/Student-Assignment-Security-Checking-Platform.git
cd Student-Assignment-Security-Checking-Platform/sast-platform

# 2. Create an S3 bucket for Lambda deployment packages (one-time)
aws s3 mb s3://my-sast-deploy-bucket --region us-east-1

# 3. Deploy everything
./scripts/deploy.sh --code-bucket my-sast-deploy-bucket

# 4. Seed an API key for a student
python scripts/00_seed_auth.py --table StudentAuth --add-student zhang.jings

# 5. Open the frontend URL printed at the end of step 3
```

That's it. The script provisions all AWS infrastructure, packages and deploys both Lambda functions, uploads the frontend, and optionally runs a smoke test.

> **With ECS Fargate fallback + smoke test:**
> ```bash
> ./scripts/deploy.sh \
>   --code-bucket my-sast-deploy-bucket \
>   --vpc-id vpc-xxxxxxxx \
>   --subnets subnet-aaa,subnet-bbb \
>   --student-key <api-key-from-step-4>
> ```

> **Via Make:**
> ```bash
> make deploy CODE_BUCKET=my-sast-deploy-bucket
> make deploy CODE_BUCKET=my-sast-deploy-bucket VPC_ID=vpc-xxx SUBNETS=subnet-aaa STUDENT_KEY=abc123
> ```

---

## Architecture

```
Browser
  │
  ├─ POST /scan ──────────────────────────────────────────────►  Lambda A
  │                                                               (Function URL)
  │                                                               validate → dispatcher
  │                                                                    │
  │                                                             ┌──────┴──────┐
  │                                                          S3 upload     SQS Queue
  │                                                         (code blob)        │
  │                                                                             ▼
  │                                                                        Lambda B
  │                                                                        scanner.py
  │                                                                        result_parser.py
  │                                                                        s3_writer.py
  │                                                                             │
  │                                                                        ┌────┴────┐
  │                                                                        S3        DynamoDB
  │                                                                  reports/       ScanResults
  │                                                              {scan_id}.json
  │
  ├─ GET /status?scan_id=xxx ──────────────────────────────────►  Lambda A
  │                                                               → presigned S3 URL
  │
  └─ GET /history ─────────────────────────────────────────────►  Lambda A
                                                                  → last 50 scans
```

**Scan status lifecycle:** `PENDING` → `IN_PROGRESS` → `DONE` | `FAILED`

---

## Prerequisites

| Tool | Version | Required for |
|------|---------|-------------|
| AWS CLI | any | All deployment |
| AWS credentials | configured | All deployment (`aws configure` or IAM role) |
| Python | 3.12+ | Lambda code + tests |
| `jq` | any | Smoke test (`05_test_api.sh`) |
| Docker | any | ECS image build only (optional) |

Install on macOS:
```bash
brew install awscli python jq
```

Install on Ubuntu/Debian:
```bash
sudo apt install awscli python3 jq
```

Configure AWS credentials:
```bash
aws configure          # enter Access Key ID, Secret, region, output format
aws sts get-caller-identity   # verify — should print your account ID
```

---

## Deployment

### Option A — Single command (recommended)

```bash
cd sast-platform
./scripts/deploy.sh --code-bucket <your-s3-bucket> [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--code-bucket` | *(required)* | S3 bucket for Lambda zip uploads |
| `--env` | `dev` | Environment tag applied to all stacks |
| `--region` | `us-east-1` | AWS region |
| `--vpc-id` | — | Enable ECS Fargate fallback (requires `--subnets`) |
| `--subnets` | — | Comma-separated subnet IDs for ECS tasks |
| `--scanner-image` | auto | ECR image URI override for ECS scanner |
| `--student-key` | — | Run end-to-end smoke test with this API key |
| `--skip-ecs` | — | Skip ECS image build even when `--vpc-id` is set |
| `--skip-test` | — | Skip smoke test unconditionally |

**What it runs internally:**

| Step | Script | What it does |
|------|--------|-------------|
| 1 | `01_setup_infra.sh` | Deploy all CloudFormation stacks |
| 2 | `02_deploy_lambda_a.sh` | Package and deploy Lambda A |
| 3 | `03_deploy_lambda_b.sh` | Package and deploy Lambda B |
| 4 | `04_build_ecs_image.sh` | Build + push ECS scanner image *(only with `--vpc-id`)* |
| 5 | `04_upload_frontend.sh` | Inject Lambda URL, sync frontend to S3 |
| 6 | `05_test_api.sh` | End-to-end smoke test *(only with `--student-key`)* |

### Option B — Step by step

Run from `sast-platform/scripts/` if you need to deploy or redeploy individual components:

```bash
cd sast-platform/scripts

# 1. CloudFormation stacks (S3, DynamoDB, SQS, Lambda A/B, CloudWatch)
./01_setup_infra.sh --code-bucket <bucket> --env dev

# 2. Lambda A code
./02_deploy_lambda_a.sh --code-bucket <bucket>

# 3. Lambda B code
./03_deploy_lambda_b.sh   # reads CODE_BUCKET env var

# 4a. (Optional) ECS scanner image
./04_build_ecs_image.sh

# 4b. Frontend
./04_upload_frontend.sh

# 5. Smoke test
LAMBDA_URL=<url> STUDENT_KEY=<key> ./05_test_api.sh
```

### Seed API keys

Students need an API key to authenticate. Generate keys after deploying infrastructure:

```bash
# Add a single student
python scripts/00_seed_auth.py --table StudentAuth --add-student zhang.jings

# Bulk-add from a file (one student ID per line)
python scripts/00_seed_auth.py --table StudentAuth --students students.txt
```

The generated key is printed to stdout — share it with the student securely.

---

## Using the Platform

### As a student

1. **Open the frontend** — use the URL printed at the end of deployment (or find it in the CloudFormation output for `sast-platform-s3` → `FrontendWebsiteURL`).

2. **Enter your Student ID and API key** in the login form.

3. **Paste or upload your source code**, select the language, and click **Scan**.

4. **Wait for results** — the page polls automatically. When the scan is `DONE`, a download button appears for the JSON vulnerability report.

5. **View scan history** — the History tab shows your last 50 scans.

### Supported languages

`python` · `java` · `javascript` · `typescript` · `go` · `ruby` · `c` · `cpp`

### Via the API directly

```bash
LAMBDA_URL=https://<your-function-url>
STUDENT_KEY=<your-api-key>

# Submit a scan
curl -X POST "$LAMBDA_URL/scan" \
  -H "Content-Type: application/json" \
  -H "X-Student-Key: $STUDENT_KEY" \
  -d '{"code": "import os\nos.system(input())", "language": "python"}'
# → {"scan_id": "scan-a1b2c3d4", "status": "PENDING", ...}

# Poll for results
curl "$LAMBDA_URL/status?scan_id=scan-a1b2c3d4" \
  -H "X-Student-Key: $STUDENT_KEY"
# → {"status": "DONE", "vuln_count": 1, "report_url": "https://..."}

# View history
curl "$LAMBDA_URL/history" \
  -H "X-Student-Key: $STUDENT_KEY"
```

Rate limit: **10 scans per hour per student**.

---

## Running Tests Locally

No AWS account needed — all AWS calls are mocked by [moto](https://github.com/getmoto/moto).

```bash
cd sast-platform

# Install deps
pip install -r lambda_a/requirements.txt -r lambda_b/requirements.txt

# Run all unit tests
pytest tests/unit -v

# Skip scanner tests if bandit/semgrep are not installed locally
pytest tests/unit -v -k "not scanner"
```

Or via Make:

```bash
make install        # install deps for both lambdas
make test-unit      # unit tests only
make test-no-scan   # skip scanner (no bandit/semgrep needed)
make test           # unit + integration
```

---

## Repository Layout

```
sast-platform/
├── lambda_a/               # API layer (POST /scan, GET /status, GET /history)
│   ├── handler.py          # Lambda entry point + routing
│   ├── validator.py        # Input validation
│   ├── dispatcher.py       # DynamoDB + SQS dispatch, rate limiting
│   ├── status.py           # Status + presigned URL generation
│   ├── auth.py             # X-Student-Key authentication
│   ├── history.py          # Scan history endpoint
│   └── requirements.txt
├── lambda_b/               # Scan engine
│   ├── handler.py          # SQS consumer, atomic IN_PROGRESS claim
│   ├── scanner.py          # Bandit + Semgrep runner
│   ├── result_parser.py    # Normalise raw tool output
│   ├── s3_writer.py        # Write report + generate presigned URL
│   ├── ecs_handler.py      # ECS Fargate fallback for large submissions
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/               # Static HTML + CSS + JS
├── infrastructure/         # CloudFormation templates (S3, DynamoDB, SQS, Lambda, ECS, CloudWatch)
├── scripts/
│   ├── deploy.sh           # Single-command full-stack deploy (wraps 01–05)
│   ├── 00_seed_auth.py     # Seed student API keys into DynamoDB
│   ├── 01_setup_infra.sh   # Deploy CloudFormation stacks
│   ├── 02_deploy_lambda_a.sh
│   ├── 03_deploy_lambda_b.sh
│   ├── 04_build_ecs_image.sh
│   ├── 04_upload_frontend.sh
│   └── 05_test_api.sh      # End-to-end smoke test
├── tests/
│   ├── unit/               # Fast, moto-backed — no AWS required
│   ├── integration/        # Service-level, moto-backed
│   ├── fixtures/           # Sample vulnerable code for demos
│   ├── load/               # Locust load tests (needs live stack)
│   └── e2e/                # Full flow (needs live stack)
├── Makefile
└── pytest.ini
```

---

## API Reference

**Authentication:** all endpoints require `X-Student-Key: <api_key>` header.

**Base URL:** Lambda A Function URL (printed by `deploy.sh` or `01_setup_infra.sh`).

### `POST /scan`

```json
// Request body
{ "code": "<source code>", "language": "python" }

// Supported languages: python, java, javascript, typescript, go, ruby, c, cpp
```

```json
// 202 Accepted
{ "scan_id": "scan-a1b2c3d4", "status": "PENDING", "message": "Scan queued." }
```

Errors: `400` invalid input · `401` bad key · `429` rate limit exceeded

---

### `GET /status?scan_id=<id>`

```json
// 200 — PENDING or IN_PROGRESS
{
  "scan_id": "scan-a1b2c3d4",
  "status": "PENDING",
  "language": "python",
  "created_at": "2025-01-01T12:00:00+00:00",
  "retry_after_seconds": 5,
  "scan_expires_at": "2025-01-01T13:00:00+00:00"
}
```

```json
// 200 — DONE
{
  "scan_id": "scan-a1b2c3d4",
  "status": "DONE",
  "language": "python",
  "vuln_count": 3,
  "completed_at": "2025-01-01T12:01:30+00:00",
  "report_url": "https://s3.amazonaws.com/...presigned...",
  "report_url_expires_at": "2025-01-01T13:01:30+00:00"
}
```

Errors: `400` missing scan_id · `401` bad key · `404` not found / wrong owner

---

### `GET /history`

Returns the 50 most recent scans for the authenticated student.

```json
{
  "student_id": "neu123456",
  "scans": [
    { "scan_id": "scan-a1b2c3d4", "status": "DONE", "language": "python",
      "vuln_count": 3, "created_at": "...", "completed_at": "..." }
  ]
}
```

---

## Infrastructure Stacks

| Stack | Template | What it creates |
|-------|----------|-----------------|
| `sast-platform-s3` | `infrastructure/s3.yaml` | Report bucket + frontend bucket |
| `sast-platform-dynamodb` | `infrastructure/dynamodb.yaml` | ScanResults + StudentAuth tables |
| `sast-platform-sqs` | `infrastructure/sqs.yaml` | Scan queue + DLQ + DLQ alarm |
| `sast-platform-lambda-a` | `infrastructure/lambda_a.yaml` | API + dispatch (rate limit: 10 scans/hour/student) |
| `sast-platform-lambda-b` | `infrastructure/lambda_b.yaml` | Scanner worker |
| `sast-platform-ecs` | `infrastructure/ecs.yaml` | Fargate fallback for large submissions (optional) |
| `sast-platform-cloudwatch` | `infrastructure/cloudwatch.yaml` | Alarms + dashboard |

---

## Demo

`sast-platform/tests/fixtures/` contains two sample files that produce known findings:

| File | Language | Expected findings |
|------|----------|-------------------|
| `vulnerable_python.py` | python | B602 HIGH (shell injection), B307 HIGH (eval), B301 MEDIUM (pickle), B105 LOW (hardcoded password) |
| `vulnerable_javascript.js` | javascript | eval injection, SQL injection (template literal), hardcoded credential |

Submit either file through the UI or run the smoke test:

```bash
LAMBDA_URL=<url> STUDENT_KEY=<key> ./scripts/05_test_api.sh
```

---

## Team

| Member | GitHub | Responsibilities |
|--------|--------|-----------------|
| Jingsi Zhang | @tyrahappy | Lambda A, auth, dispatcher, infra scripts, tests |
| Mengshan Li | @sunnythreethree | Frontend, S3 infra, upload script |
| Jiahua Wu | @beibei-ui | Lambda B, scanner, ECS, CloudWatch |
