# System Architecture — Student Assignment Security Checking Platform

**Course:** CS6620 Group 9  
**Last updated:** 2026-04-01

---

## Overview

A serverless static application security testing (SAST) platform hosted on AWS. Students submit source code via a web UI; the system scans it asynchronously using Bandit (Python), the instructor-provided Node.js scanner (JavaScript/TypeScript), or Semgrep (Java/Go/Ruby/C/C++), stores the report in S3, and serves results back to the browser via a presigned URL.

---

## High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Browser (Student)                          │
│   index.html + app.js (submit form + status poller) + results.js   │
└──────────────────────┬─────────────────────┬────────────────────────┘
                       │ POST /scan           │ GET /status?scan_id=xxx
                       ▼                      ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  Lambda A  (Function URL, public HTTPS)              │
│  handler.py → validator.py → dispatcher.py       status.py          │
│  • Validate input (code, language, student_id)                       │
│  • Generate scan_id                                                  │
│  • Write PENDING record to DynamoDB                                  │
│  • Enqueue message to SQS                                            │
│  • Query DynamoDB, return status / presigned S3 URL                  │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ SQS message
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  SQS — ScanQueue (Standard)                          │
│  • VisibilityTimeout: 300 s                                          │
│  • Long polling: 20 s                                                │
│  • DLQ after 3 failed attempts (14-day retention)                    │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ SQS trigger
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  Lambda B  (3 GB RAM, 15-min timeout)                │
│  handler.py → scanner.py → result_parser.py → s3_writer.py          │
│  • Consume SQS message                                               │
│  • Run Bandit (Python) or Semgrep (Java / JavaScript)                │
│  • Normalize findings into unified schema                            │
│  • Write report JSON to S3                                           │
│  • Update DynamoDB record to DONE (or FAILED)                        │
└───────────────┬──────────────────────────────────────────────────────┘
                │ Fallback: file too large / timeout risk
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  ECS Fargate  (2 vCPU / 4 GB, 30-min timeout)        │
│  ecs_handler.py — same scan pipeline, marks processing_method=ECS   │
│  Container: python:3.11-slim + Bandit + Semgrep (multi-stage build)  │
└──────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────┐   ┌────────────────────────┐
│  DynamoDB — ScanResults table          │   │  S3 — ReportBucket     │
│  PK: student_id  SK: scan_id           │   │  reports/{scan_id}.json│
│  GSI: ScanIdIndex (scan_id HASH)       │   │  Private + AES-256     │
│  Fields: status, language, vuln_count, │   │  Presigned URL: 1 hr   │
│          created_at, completed_at,     │   └────────────────────────┘
│          report_s3_key                 │
└────────────────────────────────────────┘
```

---

## Components

### Frontend — S3 Static Website

| File | Responsibility |
|------|---------------|
| `index.html` | Submit form (code, language, student_id) |
| `js/app.js` | Call `POST /scan`, poll `GET /status`, invoke renderer |
| `js/results.js` | Render findings table from report JSON |
| `css/style.css` | Styling |

Hosted in a public S3 bucket configured as a static website.

---

### Lambda A — API Layer

**Runtime:** Python 3.12 | **Memory:** 256 MB | **Timeout:** 30 s  
**Entry point:** `lambda_a/handler.py`

| Route | Handler | Action |
|-------|---------|--------|
| `POST /scan` | `validator.py` + `dispatcher.py` | Validate → write DynamoDB PENDING → enqueue SQS → return `202` |
| `GET /status` | `status.py` | Query DynamoDB GSI → return status + presigned URL if DONE |
| `OPTIONS` | inline | CORS preflight → `200` |

**Input validation** (`validator.py`):
- `code`: non-empty string, ≤ 1 MB
- `language`: one of `python`, `java`, `javascript`, `typescript`, `go`, `ruby`, `c`, `cpp` (case-insensitive)
- `student_id`: non-empty string

**Environment variables required:**

| Variable | Source |
|----------|--------|
| `SQS_QUEUE_URL` | imported from `sast-sqs` CloudFormation stack |
| `DYNAMODB_TABLE` | imported from `sast-dynamodb` stack |
| `S3_BUCKET` | imported from `sast-s3` stack |

---

### SQS — Message Queue

**Stack:** `infrastructure/sqs.yaml`

| Property | Value |
|----------|-------|
| Queue type | Standard |
| Visibility timeout | 300 s |
| Message retention | 24 h |
| Receive wait time | 20 s (long polling) |
| DLQ max receives | 3 |
| DLQ retention | 14 days |
| Encryption | SQS-managed SSE |
| Transport | HTTPS enforced via queue policy |

---

### Lambda B — Scan Engine

**Runtime:** Python 3.12 | **Memory:** 3008 MB | **Timeout:** 900 s  
**Entry point:** `lambda_b/handler.py`

Processing steps per SQS message:

1. Extract `scan_id`, `code`, `language`, `student_id`
2. Run `scanner.py` — routes to Bandit (Python), teacher_scanner/Node.js (JS/TS), or Semgrep (Java/Go/Ruby/C/C++) based on language
3. Parse raw tool output via `result_parser.py` into unified schema
4. Write report JSON to S3 via `s3_writer.py`
5. Update DynamoDB record to `DONE` (or `FAILED` on error)

**Language → Tool routing:**

| Language | Tool | Notes |
|----------|------|-------|
| `python` | Bandit | `bandit -r <file> -f json --silent` |
| `java` | Semgrep | `semgrep --config=auto --json` |
| `javascript`, `typescript` | teacher_scanner | Calls instructor-provided `scanner.js` via Node.js subprocess; falls back to Semgrep if `node` not in PATH |
| `go`, `ruby`, `c`, `cpp` | Semgrep | `semgrep --config=auto --json`; file extension mapping via `ext_map` in `scanner.py` |

**Unified finding schema** (output of `result_parser.py`):

```json
{
  "scan_id": "scan-abc123",
  "language": "python",
  "tool": "bandit",
  "findings": [
    {
      "line": 12,
      "severity": "HIGH",
      "confidence": "MEDIUM",
      "issue": "Use of eval()",
      "code_snippet": "eval(user_input)",
      "rule_id": "B307"
    }
  ],
  "summary": { "HIGH": 1, "MEDIUM": 0, "LOW": 0 },
  "vuln_count": 1
}
```

**CloudWatch alarms** (defined in `lambda_b.yaml`):
- Error rate > threshold
- Duration approaching 15-min timeout
- Throttling events

---

### ECS Fargate — Heavy Scan Fallback

**Stack:** `infrastructure/ecs.yaml`  
**Entry point:** `lambda_b/ecs_handler.py`

Handles scans that exceed Lambda B's capacity. Triggered by `handle_ecs_fallback()` in Lambda B.

| Property | Value |
|----------|-------|
| vCPU | 2 |
| Memory | 4 GB |
| Timeout | 1800 s (30 min) |
| Capacity | FARGATE + FARGATE_SPOT (4:1 ratio for cost savings) |
| Image source | ECR (`scan-on-push` enabled, last 10 images retained) |
| Network | Outbound HTTPS/HTTP only; no inbound |
| DynamoDB extra field | `processing_method = "ECS_FARGATE"` |

**Container build** (`lambda_b/Dockerfile`) — 3-stage:
1. `semgrep-builder`: installs `semgrep>=1.45.0`
2. `bandit-builder`: installs `bandit[toml]>=1.7.5`
3. `runtime`: copies tools from both builders, creates non-root `scanner` user

> **Note:** ECS uses Python 3.11 while Lambda uses Python 3.12 — see [issue #11](https://github.com/sunnythreethree/Student-Assignment-Security-Checking-Platform/issues/11).

---

### DynamoDB — State Store

**Stack:** `infrastructure/dynamodb.yaml`

| Property | Value |
|----------|-------|
| Table name | `ScanResults` |
| PK | `student_id` (String, HASH) |
| SK | `scan_id` (String, RANGE) |
| Billing | PAY_PER_REQUEST (on-demand) |
| PITR | Enabled |
| GSI | `ScanIdIndex` — `scan_id` HASH, ProjectionType ALL |

**Record lifecycle:**

```
Lambda A writes:  { student_id, scan_id, status: "PENDING", language, created_at }
Lambda B updates: { status: "DONE", vuln_count, completed_at, report_s3_key }
                  (or status: "FAILED" on error)
```

---

### S3 — Report Storage

**Stack:** `infrastructure/s3.yaml`

**ReportBucket** (private):
- Versioning: enabled
- Encryption: AES-256
- All public access blocked
- Transport: HTTPS enforced via bucket policy
- Report path: `reports/{scan_id}.json`
- Presigned URL TTL: 3600 s (1 hour)

**FrontendBucket** (public):
- S3 static website hosting (`index.html` as root)
- Public read via bucket policy

---

## Infrastructure Stacks (CloudFormation)

Deploy in this order:

| Order | Stack file | Exports used by |
|-------|-----------|----------------|
| 1 | `dynamodb.yaml` | Lambda A, Lambda B |
| 2 | `s3.yaml` | Lambda A, Lambda B |
| 3 | `sqs.yaml` | Lambda A, Lambda B |
| 4 | `iam_roles.yaml` | Lambda A, Lambda B, ECS |
| 5 | `lambda_a.yaml` | — |
| 6 | `lambda_b.yaml` | — |
| 7 | `ecs.yaml` | — |
| 8 | `cloudwatch.yaml` | — |

> `01_setup_infra.sh` will deploy all stacks in sequence — see [issue #3](https://github.com/sunnythreethree/Student-Assignment-Security-Checking-Platform/issues/3).

---

## API Contract

### POST /scan

**Request:**
```json
{ "code": "print('hello')", "language": "python", "student_id": "neu123" }
```

**Response 202:**
```json
{ "scan_id": "scan-abc123", "status": "PENDING" }
```

**Response 400:**
```json
{ "error": "code exceeds 1MB limit" }
```

### GET /status?scan_id=scan-abc123

**Response (PENDING):**
```json
{ "scan_id": "scan-abc123", "status": "PENDING", "language": "python", "created_at": "2026-04-01T20:00:00Z" }
```

**Response (DONE):**
```json
{
  "scan_id": "scan-abc123",
  "status": "DONE",
  "language": "python",
  "vuln_count": 3,
  "completed_at": "2026-04-01T20:01:30Z",
  "report_url": "https://s3.presigned.url/..."
}
```

---

## Team Ownership

| Component | Owner | GitHub |
|-----------|-------|--------|
| Lambda A, SQS, IAM, CloudWatch, deploy scripts | Jingsi Zhang | @tyrahappy |
| Lambda B, scanner, Docker, ECS | Jiahua | @beibei-ui |
| DynamoDB, S3, result_parser, frontend | Mengshan Li | @sunnythreethree |

---

## Open Issues

| # | Summary | Assignee |
|---|---------|---------|
| [#1](../../issues/1) | `cloudwatch.yaml` empty | @tyrahappy |
| [#2](../../issues/2) | `iam_roles.yaml` empty — hardcoded LabRole | @tyrahappy |
| [#3](../../issues/3) | `01_setup_infra.sh` empty | @tyrahappy |
| [#4](../../issues/4) | `test_validator.py` + `test_dispatcher.py` empty | @tyrahappy |
| [#5](../../issues/5) | `test_scanner.py` empty + timeout not enforced | @beibei-ui |
| [#6](../../issues/6) | Frontend `index.html` + `app.js` + `style.css` empty | @sunnythreethree |
| [#7](../../issues/7) | `04_upload_frontend.sh` empty | @sunnythreethree |
| [#8](../../issues/8) | S3 path inconsistency (`s3_writer.py` vs `s3.yaml`) | @tyrahappy + @sunnythreethree |
| [#9](../../issues/9) | Integration / E2E / load tests + README missing | shared |
| [#10](../../issues/10) | Unsupported language fallback untested (ts/go/ruby/c/cpp) | @beibei-ui |
| [#11](../../issues/11) | Python runtime mismatch: Lambda 3.12 vs ECS 3.11 | @beibei-ui + @tyrahappy |
