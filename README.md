# SAST Platform — Student Assignment Security Checker

**CS6620 Group 9** | Jingsi Zhang · Mengshan Li · Jiahua (Beibei) Wu

A serverless static analysis platform on AWS. Students submit source code via a web UI; the system scans it asynchronously using Bandit (Python) and Semgrep (Java / JavaScript / TypeScript / Go / Ruby / C / C++), stores the report in S3, and returns a presigned download URL to the browser.

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
├── scripts/                # Sequential deploy scripts (00–05)
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

## Prerequisites

| Tool | Required for |
|------|-------------|
| AWS CLI (configured) | All deployment scripts |
| Python 3.12+ | Lambda code + tests |
| `jq` | `05_test_api.sh` smoke test |
| Docker | ECS image build only |

---

## Deployment

Run all scripts from `sast-platform/scripts/`:

```bash
cd sast-platform/scripts

# 1. Deploy CloudFormation stacks (S3, DynamoDB, SQS, Lambda A/B, CloudWatch)
./01_setup_infra.sh --code-bucket <your-deploy-bucket> --env dev

# 2. Deploy Lambda A
./02_deploy_lambda_a.sh

# 3. Deploy Lambda B
./03_deploy_lambda_b.sh

# 4a. (Optional) Build + push ECS scanner image
./04_build_ecs_image.sh

# 4b. Upload frontend to S3
./04_upload_frontend.sh

# 5. Smoke test — verifies the live API end-to-end
LAMBDA_URL=<from step 1 output> STUDENT_KEY=<from seed script> ./05_test_api.sh
```

### Seed API keys

```bash
# Add a single student
python scripts/00_seed_auth.py --table StudentAuth --add-student zhang.jings

# Bulk-add from file
python scripts/00_seed_auth.py --table StudentAuth --students students.txt
```

---

## Running Tests Locally

No AWS account needed — all AWS calls are mocked by [moto](https://github.com/getmoto/moto).

```bash
cd sast-platform

# Install deps
pip install -r lambda_a/requirements.txt -r lambda_b/requirements.txt

# Run all unit tests
pytest tests/unit -v

# Skip scanner tests if bandit/semgrep are not installed
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

## API Reference

**Authentication:** all endpoints require `X-Student-Key: <api_key>` header.

**Base URL:** Lambda A Function URL (printed by `01_setup_infra.sh`).

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

Submit either file through the UI or via the smoke test script.

---

## Team

| Member | GitHub | Responsibilities |
|--------|--------|-----------------|
| Jingsi Zhang | @tyrahappy | Lambda A, auth, dispatcher, infra scripts, tests |
| Mengshan Li | @sunnythreethree | Frontend, S3 infra, upload script |
| Jiahua Wu | @beibei-ui | Lambda B, scanner, ECS, CloudWatch |
