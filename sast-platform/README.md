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

**Scan status lifecycle:** `PENDING` → `DONE` | `FAILED`

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

### POST / — Submit a scan

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

### GET /?scan_id=\<id\> — Poll scan status

```
Headers:
  X-Student-Key: <api_key>
```

**Response 200 (PENDING):**
```json
{ "scan_id": "scan-a1b2c3d4", "status": "PENDING", "language": "python", "created_at": "..." }
```

**Response 200 (DONE):**
```json
{
  "scan_id": "scan-a1b2c3d4",
  "status": "DONE",
  "language": "python",
  "vuln_count": 3,
  "completed_at": "...",
  "report_url": "https://s3.amazonaws.com/...?X-Amz-Expires=3600&..."
}
```

**Errors:** `400` missing scan_id · `401` auth · `403` not your scan · `404` not found

---

## Local Development & Testing

### Unit tests (no AWS needed)

```bash
pip install boto3 pytest "moto[sqs,dynamodb,s3]>=5.0.0"
pytest tests/unit/ -v
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
