# SAST Platform — Student Assignment Security Checker

**CS6620 Group 9** | Jingsi Zhang · Mengshan Li · Jiahua (Beibei) Wu

A serverless static analysis platform on AWS. Students submit source code via a web UI; the system scans it asynchronously using Bandit (Python) and Semgrep (Java / JavaScript), stores the report in S3, and returns a presigned URL to the browser.

---

## Architecture

```
Browser
  │
  ├─ POST /scan ──────────────────────────────────────────────►  Lambda A
  │                                                               (Function URL)
  │                                                               validate → dispatcher
  │                                                                    │
  │                                                                    ▼
  │                                                               SQS Queue
  │                                                                    │
  │                                                                    ▼
  │                                                               Lambda B
  │                                                               scanner.py
  │                                                               result_parser.py
  │                                                               s3_writer.py
  │                                                                    │
  │                                                               ┌────┴────┐
  │                                                               S3        DynamoDB
  │                                                         reports/       ScanResults
  │                                                     {student}/{id}.json
  │
  └─ GET /status?scan_id=xxx ─────────────────────────────────►  Lambda A
                                                                  → presigned URL
```

**Scan status lifecycle:** `PENDING` → `IN_PROGRESS` → `DONE` | `FAILED`

---

## Prerequisites

- AWS CLI configured (`aws configure` or Academy lab credentials)
- Python 3.12+
- `jq` (for `05_test_api.sh`)
- Docker (for ECS image build only)

---

## Deployment

Run scripts in order from the `scripts/` directory:

```bash
# 1. Deploy all CloudFormation stacks (S3, DynamoDB, SQS, Lambda A/B, CloudWatch)
./01_setup_infra.sh --code-bucket <your-deploy-bucket> --env dev

# 2. Package and deploy Lambda A
./02_deploy_lambda_a.sh

# 3. Package and deploy Lambda B
./03_deploy_lambda_b.sh

# 4a. (Optional) Build and push ECS scanner image
./04_build_ecs_image.sh

# 4b. Upload frontend to S3
./04_upload_frontend.sh

# 5. Smoke test the live API
LAMBDA_URL=<from step 1 output> STUDENT_KEY=<from seed script> ./05_test_api.sh
```

### Seed student auth keys

```bash
# Add all students before the demo
python scripts/00_seed_auth.py --table StudentAuth --students students.txt

# Add one student
python scripts/00_seed_auth.py --table StudentAuth --add-student zhang.jings
```

---

## API Contract

**Base URL:** Lambda A Function URL (printed by `01_setup_infra.sh`)

### POST /scan — Submit a scan

```
Headers:
  X-Student-Key: <api_key>
  Content-Type: application/json

Body:
  { "code": "<source code>", "language": "python" }

Supported languages: python, java, javascript, typescript, go, ruby, c, cpp
```

**Response 202:**
```json
{ "scan_id": "scan-a1b2c3d4", "status": "PENDING", "message": "..." }
```

**Errors:** `400` invalid input · `401` missing/invalid key · `500` internal error

---

### GET /status?scan_id=\<id\> — Poll scan status

```
Headers:
  X-Student-Key: <api_key>
```

**Response 200 (PENDING / IN_PROGRESS):**
```json
{
  "scan_id": "scan-a1b2c3d4",
  "status": "PENDING",
  "language": "python",
  "created_at": "...",
  "retry_after_seconds": 5,
  "scan_expires_at": "2025-01-01T13:00:00+00:00"
}
```

**Response 200 (DONE):**
```json
{
  "scan_id": "scan-a1b2c3d4",
  "status": "DONE",
  "language": "python",
  "vuln_count": 3,
  "completed_at": "...",
  "report_url": "https://s3.amazonaws.com/...?X-Amz-Expires=3600&...",
  "report_url_expires_at": "2025-01-01T13:00:00+00:00"
}
```

**Errors:** `400` missing scan_id · `401` auth · `403` not your scan · `404` not found

---

### GET /history — List past scans

```
Headers:
  X-Student-Key: <api_key>
```

**Response 200:**
```json
{
  "student_id": "neu123456",
  "scans": [
    { "scan_id": "scan-a1b2c3d4", "status": "DONE", "language": "python", "vuln_count": 3, "created_at": "...", "completed_at": "..." }
  ]
}
```

**Errors:** `401` auth

---

## Demo Fixtures

`tests/fixtures/` contains sample files with known vulnerabilities for demo purposes.

### `vulnerable_python.py`

Submit with **language: python**. Triggers Bandit findings:

| Rule | Severity | Description |
|------|----------|-------------|
| B602 | HIGH | `subprocess.call` with `shell=True` — command injection |
| B307 | HIGH | `eval()` — arbitrary code execution |
| B301 | MEDIUM | `pickle.loads` — arbitrary object deserialization |
| B105 | LOW | Hardcoded password string |

### `vulnerable_javascript.js`

Submit with **language: javascript**. Triggers Semgrep findings:

| Rule | Description |
|------|-------------|
| `javascript.lang.security.audit.eval-detected` | `eval()` with user-controlled input |
| `javascript.sequelize.security.audit.sequelize-injection` | SQL injection via template literal |
| `javascript.lang.security.audit.hardcoded-credentials` | Hardcoded DB password |

---

## Local Development & Testing

**No AWS account needed for unit and integration tests.** All AWS calls are intercepted by [moto](https://github.com/getmoto/moto), which emulates DynamoDB, SQS, and S3 in-process.

### Prerequisites

| Tool | Version | Required for |
|------|---------|-------------|
| Python | 3.12+ | everything |
| pip | any | installing deps |
| bandit | `pip install bandit` | Lambda B scanner tests only |
| semgrep | `pip install semgrep` | Lambda B scanner tests only |

bandit and semgrep are **optional** — all other tests run without them. Skip scanner tests with `pytest -k "not scanner"`.

### Quick Start

```bash
# 1. Install test dependencies (covers both Lambda A and B)
pip install -r lambda_a/requirements.txt
pip install -r lambda_b/requirements.txt

# 2. Run all unit tests — no AWS, no Docker, completes in seconds
pytest tests/unit -v

# 3. Skip scanner tests if bandit/semgrep are not installed locally
pytest tests/unit -v -k "not scanner"
```

Or use Make:

```bash
make install       # install deps for both lambdas
make test          # all unit + integration tests
make test-unit     # unit tests only
make test-no-scan  # unit tests, skip scanner (no bandit/semgrep needed)
```

### Unit tests — Lambda A

```bash
pip install -r lambda_a/requirements.txt
pytest tests/unit/test_validator.py tests/unit/test_dispatcher.py -v
```

### Unit tests — Lambda B

```bash
pip install -r lambda_b/requirements.txt
pytest tests/unit/test_result_parser.py -v          # no external tools needed
pytest tests/unit/test_scanner.py -v                # requires bandit + semgrep
```

### Integration tests (moto-backed pipeline)

```bash
pytest tests/integration/ -v
```

### E2E tests (live deployment required)

```bash
LAMBDA_URL=https://... STUDENT_KEY=abc123 pytest tests/e2e/ -v
```

### Load test (Locust)

```bash
pip install locust
locust -f tests/load/locustfile.py \
  --host=https://<LAMBDA_URL> \
  --users=30 --spawn-rate=5 --run-time=2m --headless
```

### Environment Variables

Unit and integration tests set all required env vars automatically inside moto fixtures — no `.env` file needed.

**Lambda A**

| Variable | Description |
|----------|-------------|
| `SQS_QUEUE_URL` | SQS queue URL |
| `DYNAMODB_TABLE` | DynamoDB table name |
| `S3_BUCKET` | S3 bucket for scan reports |

**Lambda B**

| Variable | Description |
|----------|-------------|
| `DYNAMODB_TABLE_NAME` | DynamoDB table name |
| `S3_BUCKET_NAME` | S3 bucket for scan reports |

### Project Structure

```
sast-platform/
├── lambda_a/               # API handler (POST /scan, GET /status, GET /history)
│   ├── handler.py
│   ├── validator.py
│   ├── dispatcher.py
│   ├── status.py
│   ├── auth.py             # X-Student-Key API-key authentication
│   └── requirements.txt    # runtime + test deps (boto3, pytest, moto)
├── lambda_b/               # Scan engine (Bandit, Semgrep, ECS fallback)
│   ├── handler.py
│   ├── scanner.py
│   ├── result_parser.py
│   ├── s3_writer.py
│   ├── ecs_handler.py      # ECS Fargate fallback for large submissions
│   ├── Dockerfile          # Container image (bypasses 250MB zip limit)
│   └── requirements.txt    # runtime + test deps (bandit, semgrep, pytest, moto)
├── frontend/               # Static HTML/CSS/JS frontend
├── infrastructure/         # CloudFormation templates
├── scripts/                # Sequential deploy scripts
├── tests/
│   ├── unit/               # Fast, moto-backed, no AWS required
│   ├── integration/        # Service-level, moto-backed, no AWS required
│   ├── load/               # Locust load tests — needs live stack
│   └── e2e/                # Full flow — needs live stack
├── pytest.ini              # Test discovery config
├── Makefile                # Dev convenience targets
└── README.md
```

---

## Infrastructure Stacks

| Stack | Template | Description |
|-------|----------|-------------|
| `sast-platform-s3` | `infrastructure/s3.yaml` | Report bucket + frontend bucket |
| `sast-platform-dynamodb` | `infrastructure/dynamodb.yaml` | ScanResults + StudentAuth tables |
| `sast-platform-sqs` | `infrastructure/sqs.yaml` | Scan queue + DLQ |
| `sast-platform-lambda-a` | `infrastructure/lambda_a.yaml` | API + dispatch layer |
| `sast-platform-lambda-b` | `infrastructure/lambda_b.yaml` | Scanner worker |
| `sast-platform-ecs` | `infrastructure/ecs.yaml` | Fargate fallback (optional) |
| `sast-platform-cloudwatch` | `infrastructure/cloudwatch.yaml` | Alarms + dashboard |

---

## Team

| Member | GitHub | Responsibilities |
|--------|--------|-----------------|
| Jingsi Zhang | @tyrahappy | Lambda A, auth, infra scripts, tests |
| Mengshan Li | @sunnythreethree | Frontend, S3 infra, upload script |
| Jiahua Wu | @beibei-ui | Lambda B, scanner, ECS, CloudWatch |
