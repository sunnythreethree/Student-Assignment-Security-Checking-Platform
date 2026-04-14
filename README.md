# SAST Platform — Student Assignment Security Checker

**CS6620 Group 9** | Jingsi (Jess) Zhang · Mengshan (Sunny) Li · Jiahua (Liz) Wu

A serverless static analysis platform on AWS. Students submit source code via a web UI; the system scans it asynchronously using Bandit (Python), JS/TS (JavaScript / TypeScript), and Semgrep (Java / Go / Ruby), stores the report in S3, and returns a presigned download URL to the browser.

---

## Quick Start

**The platform is live.** No setup required — just open the frontend and start scanning.

**Frontend URL:**
```
http://sast-platform-frontend-dev-891377348481.s3-website-us-east-1.amazonaws.com
```

1. Open the URL above in your browser
2. Enter your **Student ID** to log in
3. Paste or upload source code, select the language, and click **Scan**
4. The page polls automatically — when status is `DONE`, click **Download Report** for the JSON vulnerability report
5. The **History** tab shows your last 50 scans


---

## Architecture

```
Browser
  │
  ├─ POST /scan ──────────────────────────────────────────────►  Lambda A
  │                                                               (Function URL)
  │                                                     validate → dispatcher
  │                                                                    │
  │                                                             ┌──────┴──────┐
  │                                                          S3 upload     SQS Queue
  │                                                         (code blob)        │
  │                                                                             ▼
  │                                                                        Lambda B
  │                                                                        scanner.py / scanner.js
  │                                                                             │
  │                                                              ┌──────────────┴──────────────┐
  │                                                         (small files)              (large files)
  │                                                              │                              │
  │                                                        Bandit / Semgrep          ECS Fargate task
  │                                                        (in Lambda)               (Docker container)
  │                                                              │                              │
  │                                                              └──────────────┬──────────────┘
  │                                                                             │
  │                                                                    ┌────────┴────────┐
  │                                                                    S3                DynamoDB
  │                                                              reports/              ScanResults
  │                                                          {scan_id}.json
  │
  ├─ GET /status?scan_id=xxx ──────────────────────────────────►  Lambda A
  │                                                               → presigned S3 URL
  │
  └─ GET /history ─────────────────────────────────────────────►  Lambda A
                                                                  → last 50 scans
```

**Scan status lifecycle:**
```
PENDING → IN_PROGRESS → DONE | FAILED
       └→ ECS_QUEUED  → IN_PROGRESS → DONE | FAILED
```
Large submissions are routed to ECS Fargate (`ECS_QUEUED`) instead of running inside Lambda.

---

## Supported Languages

`python` · `java` · `javascript` · `typescript` · `go` · `ruby` 

---

## CI/CD Pipeline

All deployments go through GitHub Actions. Pushing to `main` triggers the CD workflow automatically; each job only runs when its relevant files change (path filter).

```
push to main
     │
     ├─ test          (always)      unit tests with moto
     ├─ changes       (always)      detect which components changed
     │
     ├─ deploy-infra  (infra/**)    CloudFormation stacks
     ├─ deploy-lambda-a (lambda_a/**) Lambda A code update
     ├─ deploy-lambda-b (lambda_b/**) Lambda B code update
     ├─ build-ecs     (lambda_b/**) ECS Docker image rebuild
     └─ deploy-frontend (frontend/**) S3 frontend upload
```

**Manual full re-deploy** (e.g. after AWS Learner Lab credential rotation):

1. Go to **Actions → CD** in GitHub
2. Click **Run workflow**
3. Leave `force_all` as `true` (default) — bypasses path filter, runs all jobs
4. Click **Run workflow**

For full pipeline documentation see [`deployment-pipeline.md`](deployment-pipeline.md).

---

## Monitoring Dashboard

The CloudWatch dashboard shows live metrics across the entire scan pipeline.

**Dashboard URL:**
```
https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=sast-platform-dev-overview
```

Or navigate manually: AWS Console → CloudWatch → Dashboards → `sast-platform-dev-overview`

**What's monitored:**

| Section | Metrics | Alarms |
|---------|---------|--------|
| Lambda A (API layer) | Invocations, Errors, Duration (avg+p99), Throttles | ≥3 errors/5min · avg ≥25s · any throttle |
| Lambda B (scan engine) | Invocations, Errors, Duration, Concurrent executions | ≥5 errors · ≥13min duration |
| SQS (scan queue) | Queue depth, Throughput (sent vs processed), DLQ | ≥50 messages · any DLQ message |
| DynamoDB (ScanResults) | Latency (PutItem/UpdateItem/Query), Throttles, System errors | ≥5 throttles · any system error |

Alarms publish to an SNS topic (`sast-platform-dev-alerts`). Configure `AlertEmail` in `cloudwatch.yaml` to receive email notifications.

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

## Unit Tests

~105 tests across 7 modules, all mocked with [moto](https://github.com/getmoto/moto) — no AWS account needed.

| File | Tests | What's covered |
|------|-------|----------------|
| `test_validator.py` | 23 | Input validation: language field, code field, size limit (1 MB), normalization |
| `test_scanner.py` | 23 | Scanner routing (Python→Bandit, JS→teacher scanner, Java/Go/Ruby→Semgrep), exit codes, timeout, temp file cleanup |
| `test_dispatcher.py` | 17 | Scan ID generation, DynamoDB record creation, SQS message content |
| `test_status.py` | 17 | Status response shape for all states: PENDING, IN_PROGRESS, ECS_QUEUED, DONE, FAILED |
| `test_lambda_b_idempotency.py` | 10 | Lambda B atomic claim — prevents duplicate processing of the same SQS message |
| `test_history.py` | 10 | Scan history: ordering (newest first), field shapes, student isolation, 50-item limit |
| `test_result_parser.py` | 5 | Bandit / Semgrep output parsing and field normalization |

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
│   ├── scanner.py          # Bandit runner (Python)
│   ├── scanner.js          # Semgrep runner (JS/TS/Java/Go/Ruby)
│   ├── result_parser.py    # Normalise raw tool output
│   ├── s3_writer.py        # Write report + generate presigned URL
│   ├── ecs_handler.py      # ECS Fargate fallback for large submissions
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/               # Static HTML + CSS + JS
├── infrastructure/         # CloudFormation templates
│   ├── s3.yaml             # Report + frontend buckets
│   ├── dynamodb.yaml       # ScanResults + StudentAuth tables
│   ├── sqs.yaml            # Scan queue + DLQ
│   ├── lambda_a.yaml       # API Lambda
│   ├── lambda_b.yaml       # Scanner Lambda
│   ├── ecs.yaml            # Fargate fallback cluster
│   └── cloudwatch.yaml     # Alarms + dashboard
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
├── test-samples/           # E2E scanner validation (needs live stack)
│   ├── code/               # Vulnerable + clean code in 5 languages
│   └── e2e_test.py         # Automated test runner (11 scenarios)
├── Makefile
└── pytest.ini
```

Root-level docs:
- [`deployment-pipeline.md`](deployment-pipeline.md) — full CD workflow documentation
- [`debugging-js-scan-failure.md`](debugging-js-scan-failure.md) — JS scan debugging log (13 fixes)

---

## API Reference

**Authentication:** all endpoints require `X-Student-Key: <api_key>` header.

**Base URL:** Lambda A Function URL (visible in AWS Console → Lambda → `sast-lambda-a` → Function URL).

### `POST /scan`

```json
// Request body
{ "code": "<source code>", "language": "java" }

// Supported languages: java, javascript, typescript, go, ruby, python
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
  "language": "Java",
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
  "language": "Java",
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

Rate limit: **10 scans per hour per student**.

---

## Infrastructure Stacks

| Stack | Template | What it creates |
|-------|----------|-----------------|
| `sast-platform-s3` | `infrastructure/s3.yaml` | Report bucket + frontend bucket |
| `sast-platform-dynamodb` | `infrastructure/dynamodb.yaml` | ScanResults + StudentAuth tables |
| `sast-platform-sqs` | `infrastructure/sqs.yaml` | Scan queue + DLQ |
| `sast-platform-lambda-a` | `infrastructure/lambda_a.yaml` | API + dispatch (rate limit: 10 scans/hour/student) |
| `sast-platform-lambda-b` | `infrastructure/lambda_b.yaml` | Scanner worker |
| `sast-platform-ecs` | `infrastructure/ecs.yaml` | Fargate fallback for large submissions |
| `sast-platform-cloudwatch` | `infrastructure/cloudwatch.yaml` | Alarms + dashboard |

---

## Deployment

Deployments are fully automated via GitHub Actions — see [`deployment-pipeline.md`](deployment-pipeline.md) for details.

For manual deployment (e.g. local testing or emergency re-deploy):

```bash
cd sast-platform

# Deploy everything in one command
./scripts/deploy.sh --code-bucket <your-s3-bucket>

# With ECS Fargate fallback + smoke test
./scripts/deploy.sh \
  --code-bucket <your-s3-bucket> \
  --vpc-id vpc-xxxxxxxx \
  --subnets subnet-aaa,subnet-bbb \
  --student-key <api-key>
```

| Flag | Default | Description |
|------|---------|-------------|
| `--code-bucket` | *(required)* | S3 bucket for Lambda zip uploads |
| `--env` | `dev` | Environment tag applied to all stacks |
| `--region` | `us-east-1` | AWS region |
| `--vpc-id` | — | Enable ECS Fargate fallback |
| `--subnets` | — | Comma-separated subnet IDs for ECS tasks |
| `--student-key` | — | Run end-to-end smoke test with this API key |

**AWS credentials for Learner Lab:** update `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` in GitHub → Settings → Secrets after each Lab session.

---

## Demo

`sast-platform/tests/fixtures/` contains sample files that produce known findings:

| File | Language | Expected findings |
|------|----------|-------------------|
| `vulnerable_javascript.js` | javascript | HARDCODED_SECRET HIGH (Stripe secret key, hardcoded password, AWS Access Key ID), NOSQL_INJECTION HIGH (MongoDB find/findOne/deleteOne), INSECURE_FUNCTION HIGH (eval) |

Submit the file through the UI or run the smoke test:

```bash
LAMBDA_URL=<url> STUDENT_KEY=<key> ./scripts/05_test_api.sh
```

---

## Team

| Member | GitHub | Responsibilities |
|--------|--------|-----------------|
| Jingsi Zhang | @tyrahappy | Lambda A, auth, dispatcher, infra scripts, CI/CD pipeline|
| Mengshan Li | @sunnythreethrees | Frontend, S3 infra, upload script, tests |
| Jiahua Wu | @beibei-ui | Lambda B, scanner, ECS Fargate, CloudWatch |
