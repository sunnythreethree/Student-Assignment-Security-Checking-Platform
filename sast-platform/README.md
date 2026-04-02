# Student Assignment Security Checking Platform

A serverless SAST pipeline: students submit code through a frontend, Lambda A validates and queues it, Lambda B runs Bandit/Semgrep and writes results to S3 + DynamoDB.

```
Browser → Lambda A (validate + queue) → SQS → Lambda B (scan) → S3 + DynamoDB
```

---

## Local Development

**No AWS account needed for unit and integration tests.** All AWS calls are intercepted by [moto](https://github.com/getmoto/moto), which emulates DynamoDB, SQS, and S3 in-process.

### Prerequisites

| Tool | Version | Required for |
|------|---------|-------------|
| Python | 3.12 | everything |
| pip | any | installing deps |
| bandit | `pip install bandit` | Lambda B scanner tests only |
| semgrep | `pip install semgrep` | Lambda B scanner tests only |

bandit and semgrep are **optional** — all other tests run without them. Skip scanner tests with `pytest -k "not scanner"`.

---

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

---

### Running Tests by Suite

#### Unit tests — Lambda A

Files: `tests/unit/test_validator.py`, `test_dispatcher.py`, `test_lambda_a.py`

```bash
pip install -r lambda_a/requirements.txt
pytest tests/unit/test_validator.py tests/unit/test_dispatcher.py tests/unit/test_lambda_a.py -v
```

Covers: input validation, scan job creation, DynamoDB record shape, SQS message payload, error propagation.

#### Unit tests — Lambda B

Files: `tests/unit/test_result_parser.py`, `test_scanner.py`

```bash
pip install -r lambda_b/requirements.txt   # includes bandit + semgrep

# result_parser tests need no external tools:
pytest tests/unit/test_result_parser.py -v

# scanner tests invoke bandit/semgrep as subprocesses:
pytest tests/unit/test_scanner.py -v
```

Covers: Bandit/Semgrep output normalisation, severity mapping, finding counts.

#### Integration tests

Files: `tests/integration/`

```bash
pip install -r lambda_a/requirements.txt
pip install -r lambda_b/requirements.txt
pytest tests/integration -v
```

Wires Lambda A + Lambda B together through moto-backed SQS and DynamoDB. No real AWS needed.

#### Load tests

Files: `tests/load/locustfile.py`

Requires a **live deployed stack**. Install locust and point it at your Lambda URL:

```bash
pip install locust
locust -f tests/load/locustfile.py --host https://<your-lambda-function-url>
```

#### End-to-end tests

Files: `tests/e2e/`

Requires a fully deployed AWS environment:

```bash
export LAMBDA_URL="https://<your-function-url>"
export STUDENT_ID="your-student-id"
pytest tests/e2e -v
```

---

### Environment Variables

Unit and integration tests set all required env vars automatically inside moto fixtures. You do **not** need a `.env` file for local testing.

For manual invocation or e2e tests, the Lambdas expect:

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

---

### How moto works

moto intercepts all `boto3` calls and routes them to an in-memory AWS emulator. No real credentials are needed — the fixtures set dummy values that satisfy boto3's validation:

```python
monkeypatch.setenv("AWS_ACCESS_KEY_ID",     "testing")
monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
monkeypatch.setenv("AWS_DEFAULT_REGION",    "us-east-1")
```

AWS state is isolated per test — each test function gets a fresh DynamoDB table, SQS queue, and S3 bucket, then it is torn down automatically.

---

## Project Structure

```
sast-platform/
├── lambda_a/               # API handler (POST /scan, GET /status)
│   ├── handler.py
│   ├── validator.py
│   ├── dispatcher.py
│   ├── status.py
│   └── requirements.txt    # runtime + test deps (boto3, pytest, moto)
├── lambda_b/               # Scan engine (Bandit, Semgrep, ECS fallback)
│   ├── handler.py
│   ├── scanner.py
│   ├── result_parser.py
│   ├── s3_writer.py
│   ├── ecs_handler.py
│   └── requirements.txt    # runtime + test deps (bandit, semgrep, pytest, moto)
├── frontend/               # Static HTML/CSS/JS frontend
├── infrastructure/         # CloudFormation templates
│   ├── dynamodb.yaml
│   ├── sqs.yaml
│   ├── cloudwatch.yaml
│   └── ...
├── scripts/                # Sequential deploy scripts
├── tests/
│   ├── unit/               # Fast, moto-backed, no AWS required
│   ├── integration/        # Service-level, moto-backed, no AWS required
│   ├── load/               # Locust load tests — needs live stack
│   └── e2e/                # Full flow — needs live stack
├── pytest.ini              # Test discovery config
└── README.md
```

---

## Deployment

Deployment uses the shell scripts in `scripts/`. Run them in order from the `sast-platform/` directory:

```bash
bash scripts/01_setup_infra.sh       # CloudFormation stacks (DynamoDB, SQS, CloudWatch)
bash scripts/02_deploy_lambda_a.sh   # Package and deploy Lambda A
bash scripts/03_deploy_lambda_b.sh   # Package and deploy Lambda B
bash scripts/04_upload_frontend.sh   # Upload static frontend to S3
bash scripts/05_test_api.sh          # Smoke-test the live API
```

Each script prints the required AWS environment variables at the top if they are not set.
