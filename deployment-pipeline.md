# Deployment Pipeline

**Project:** SAST Platform (Student Assignment Security Checking Platform)  
**File:** `.github/workflows/cd.yml`  
**Trigger:** Every push to `main` branch

---

## Overview

The CD pipeline uses **smart path filtering** — it only deploys the components that actually changed. This avoids unnecessary rebuilds and keeps most merges fast. The pipeline runs 7 jobs, where jobs 3–7 are conditional on which files changed.

```
push to main
    │
    ├── test          (always runs)
    ├── changes       (always runs — detects what changed)
    │
    └── [if infra changed]    deploy-infra
    └── [if lambda_a changed] deploy-lambda-a
    └── [if lambda_b changed] deploy-lambda-b
                              build-ecs
    └── [if frontend changed] deploy-frontend
```

---

## Job 1: `test`

**Runs:** Always  
**Purpose:** Gate — all deploy jobs implicitly depend on this passing

Installs Python 3.12 and runs the unit test suite with mocked AWS credentials:

```bash
pip install -r sast-platform/requirements-ci.txt
python -m pytest tests/unit/ -v --tb=short
```

No real AWS credentials are used. Tests run against mocked AWS services.

---

## Job 2: `changes`

**Runs:** Always  
**Purpose:** Detect which parts of the repo changed using `dorny/paths-filter@v3`

Outputs four boolean flags consumed by downstream jobs:

| Output flag | Triggered by |
|-------------|-------------|
| `lambda_a`  | `sast-platform/lambda_a/**` |
| `lambda_b`  | `sast-platform/lambda_b/**` or `sast-platform/scripts/04_build_ecs_image.sh` |
| `frontend`  | `sast-platform/frontend/**` |
| `infra`     | `sast-platform/infrastructure/**` or `sast-platform/scripts/01_setup_infra.sh` |

Note: `lambda_b` and `build-ecs` share the same path filter — any change to Lambda B code or the ECS build script triggers both `deploy-lambda-b` and `build-ecs`.

---

## Job 3: `deploy-infra`

**Runs:** Only when `infra == true`  
**Needs:** `test`, `changes`  
**Script:** `01_setup_infra.sh`

Deploys all CloudFormation stacks in dependency order. Steps:

1. **Resolve code bucket** — uses `CODE_BUCKET` secret, or auto-derives `sast-deploy-{ACCOUNT_ID}`
2. **Ensure code bucket exists** — creates the S3 deployment bucket if it doesn't exist (handles new Learner Lab accounts)
3. **Pre-package Lambda A → S3** — zips `lambda_a/*.py`, uploads to `s3://{CODE_BUCKET}/lambda_a.zip`
4. **Pre-package Lambda B → S3** — zips `lambda_b/*.py` + JS files + pip dependencies, **excludes `ecs_handler.py`** (ECS-only), uploads to `s3://{CODE_BUCKET}/lambda_b.zip`
5. **Deploy CloudFormation stacks** — auto-detects default VPC and subnets, runs `01_setup_infra.sh`

CloudFormation stacks deployed (in dependency order):

| Stack | Resources |
|-------|-----------|
| `sast-platform-s3` | Report bucket, frontend bucket |
| `sast-platform-dynamodb` | `ScanResults` DynamoDB table |
| `sast-platform-sqs` | Scan queue + dead-letter queue |
| `sast-platform-lambda-a` | API Lambda function |
| `sast-platform-lambda-b` | Router/scanner Lambda function |
| `sast-platform-ecs` | ECS cluster, task definition, security group (only when VPC detected) |
| `sast-platform-cloudwatch` | Alarms and dashboards |

> **Note:** `01_setup_infra.sh`'s `deploy_stack()` skips stacks already in `CREATE_COMPLETE` or `UPDATE_COMPLETE` state. This is intentional for AWS Learner Lab compatibility (changeset operations are restricted).

---

## Job 4: `deploy-lambda-a`

**Runs:** Only when `lambda_a == true`  
**Needs:** `test`, `changes`, `deploy-infra` (success or skipped)  
**Script:** `02_deploy_lambda_a.sh`

Updates the Lambda A function code. Uses `--skip-test` flag to avoid running integration tests in CI.

---

## Job 5: `deploy-lambda-b`

**Runs:** Only when `lambda_b == true`  
**Needs:** `test`, `changes`, `deploy-infra` (success or skipped)  
**Script:** `03_deploy_lambda_b.sh`

Updates the Lambda B function code (inline scanner + SQS router). `SKIP_TEST=true` is set to skip local Docker tests.

---

## Job 6: `build-ecs`

**Runs:** Only when `lambda_b == true` (same filter as `deploy-lambda-b`)  
**Needs:** `test`, `changes`, `deploy-infra` (success or skipped)  
**Script:** `04_build_ecs_image.sh`

Builds and pushes the ECS Fargate Docker image, then syncs Lambda B's ECS-related environment variables. Steps performed by the script:

1. **ECR login** — authenticates Docker to ECR
2. **Ensure ECR repository exists** — creates `sast-platform-dev-scanner` repo if needed
3. **Build Docker image** — `docker buildx build --platform linux/amd64` (x86_64 required for Fargate)
4. **Push image to ECR** — pushes `:latest` and a timestamped tag
5. **Update ECS task definition** — runs `aws cloudformation update-stack --use-previous-template` on `sast-platform-ecs` with the new image URI
6. **Sync Lambda B env vars** — reads authoritative values from CloudFormation stack outputs and updates Lambda B:

| Lambda B env var | Source |
|-----------------|--------|
| `ECS_CLUSTER_NAME` | `sast-platform-ecs` stack output `ECSClusterName` |
| `ECS_TASK_DEFINITION` | Hardcoded family name (`sast-platform-dev-scanner`) |
| `ECS_SUBNETS` | Auto-detected from default VPC |
| `ECS_SECURITY_GROUPS` | `sast-platform-ecs` stack output `ECSSecurityGroupId` |
| `S3_BUCKET_NAME` | `sast-platform-s3` stack output `ReportBucketNameOutput` |

> **Why `lambda_b` triggers `build-ecs`:** The ECS container code (`ecs_handler.py`, `scanner.py`, etc.) lives in `lambda_b/`. Any change to that directory needs both the Lambda zip and the Docker image rebuilt.

---

## Job 7: `deploy-frontend`

**Runs:** Only when `frontend == true`  
**Needs:** `test`, `changes`, `deploy-infra` (success or skipped)  
**Script:** `04_upload_frontend.sh`

Uploads the static frontend files to the S3 frontend bucket (`sast-platform-frontend-dev-{ACCOUNT_ID}`), which is configured for static website hosting.

---

## Job Dependency Graph

```
test ──────────────────────────────────────────────────────┐
changes ───────────────────────────────────────────────────┤
                                                            ▼
                                               [if infra]  deploy-infra
                                                            │
                          ┌─────────────────────────────────┤
                          │                                  │
              [if lambda_a]                      [if lambda_b]
          deploy-lambda-a                    deploy-lambda-b
                                             build-ecs
                          │
              [if frontend]
          deploy-frontend
```

All deploy jobs use `always()` + explicit result checks so they can run even when `deploy-infra` was skipped (most common case).

---

## GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | Learner Lab access key |
| `AWS_SECRET_ACCESS_KEY` | Learner Lab secret key |
| `AWS_SESSION_TOKEN` | Learner Lab session token (required for temporary credentials) |
| `AWS_REGION` | AWS region (defaults to `us-east-1` if not set) |
| `CODE_BUCKET` | S3 bucket for Lambda deployment zips (auto-derived from account ID if not set) |

> **Learner Lab note:** Session tokens expire when the lab session ends. All three AWS credential secrets must be updated each time a new Learner Lab session is started.

---

## What Triggers Each Job

| Changed file | Jobs triggered |
|-------------|---------------|
| `lambda_a/*.py` | `test`, `changes`, `deploy-lambda-a` |
| `lambda_b/*.py` or `lambda_b/*.js` | `test`, `changes`, `deploy-lambda-b`, `build-ecs` |
| `scripts/04_build_ecs_image.sh` | `test`, `changes`, `deploy-lambda-b`, `build-ecs` |
| `infrastructure/*.yaml` | `test`, `changes`, `deploy-infra` |
| `scripts/01_setup_infra.sh` | `test`, `changes`, `deploy-infra` |
| `frontend/**` | `test`, `changes`, `deploy-frontend` |
| Any file | `test`, `changes` (always) |
