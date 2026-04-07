#!/usr/bin/env bash
# 02_deploy_lambda_a.sh — Package and deploy Lambda A (API / dispatch layer).
#
# Usage:
#   ./02_deploy_lambda_a.sh [OPTIONS]
#
# Optional:
#   --project     Project name prefix      (default: sast-platform)
#   --env         Environment              (default: dev)
#   --region      AWS region               (default: us-east-1)
#   --code-bucket S3 bucket for the zip    (required when infra was deployed via
#                                           01_setup_infra.sh --code-bucket <bucket>)
#   --skip-test   Skip smoke-test invocation
#
# Environment variable equivalents (override flags):
#   PROJECT_NAME, ENVIRONMENT, AWS_REGION, CODE_BUCKET, SKIP_TEST
#
# What this script does:
#   1. Validates tool prerequisites and AWS credentials
#   2. Copies lambda_a/ source into a clean build directory
#   3. Skips bundling boto3 (pre-installed in the Lambda Python 3.12 runtime)
#   4. Zips the package and uploads the code to Lambda
#      - Direct upload if package < 50 MB
#      - Via S3 (--code-bucket) if package >= 50 MB
#   5. Optionally copies the zip to S3 so CloudFormation can re-deploy via
#      01_setup_infra.sh without a separate manual upload step
#   6. Smoke-tests the live function with a minimal HTTP event

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LAMBDA_A_DIR="$PROJECT_ROOT/lambda_a"
BUILD_DIR="/tmp/lambda_a_build"
ZIP_FILE="/tmp/lambda_a_deployment.zip"

# ── Defaults ──────────────────────────────────────────────────────────────────
PROJECT_NAME="${PROJECT_NAME:-sast-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
CODE_BUCKET="${CODE_BUCKET:-}"
SKIP_TEST="${SKIP_TEST:-false}"

FUNCTION_NAME="sast-lambda-a"   # must match FunctionName in lambda_a.yaml

# ── Argument parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)      PROJECT_NAME="$2";  shift 2 ;;
    --env)          ENVIRONMENT="$2";   shift 2 ;;
    --region)       AWS_REGION="$2";    shift 2 ;;
    --code-bucket)  CODE_BUCKET="$2";   shift 2 ;;
    --skip-test)    SKIP_TEST="true";   shift   ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo -e "[$(date '+%H:%M:%S')] ${GREEN}✓ $*${NC}"; }
warn() { echo -e "[$(date '+%H:%M:%S')] ${YELLOW}⚠ $*${NC}"; }
fail() { echo -e "[$(date '+%H:%M:%S')] ${RED}✗ $*${NC}" >&2; exit 1; }

# ── Print config ───────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║     SAST Platform — Deploy Lambda A (API layer)      ║"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  Project:    %-38s ║\n" "$PROJECT_NAME"
printf "║  Environment:%-38s ║\n" "$ENVIRONMENT"
printf "║  Region:     %-38s ║\n" "$AWS_REGION"
printf "║  Function:   %-38s ║\n" "$FUNCTION_NAME"
printf "║  Code Bucket:%-38s ║\n" "${CODE_BUCKET:-<not set — direct upload>}"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Prerequisites ───────────────────────────────────────────────────────────
log "Checking prerequisites..."

missing=()
command -v aws    &>/dev/null || missing+=("aws-cli")
command -v zip    &>/dev/null || missing+=("zip")
command -v python3 &>/dev/null || missing+=("python3")
[[ ${#missing[@]} -eq 0 ]] || fail "Missing tools: ${missing[*]}"

aws sts get-caller-identity --region "$AWS_REGION" &>/dev/null \
  || fail "AWS credentials not configured or invalid"

ok "Prerequisites OK"

# ── 2. Verify the Lambda function exists ───────────────────────────────────────
log "Verifying Lambda function '$FUNCTION_NAME' exists..."

if ! aws lambda get-function \
       --function-name "$FUNCTION_NAME" \
       --region "$AWS_REGION" &>/dev/null; then
  fail "Function '$FUNCTION_NAME' not found. Run 01_setup_infra.sh first."
fi

ok "Function exists"

# ── 3. Build package ───────────────────────────────────────────────────────────
log "Building deployment package..."

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Copy only runtime source files — no tests, no __pycache__
cp "$LAMBDA_A_DIR"/*.py "$BUILD_DIR/"

# boto3 is pre-installed in the Python 3.12 Lambda runtime; do not bundle it.
# If you add third-party runtime deps to requirements.txt in future, install them here:
#   python3 -m pip install --target "$BUILD_DIR" --upgrade \
#     --no-deps <new-dep>

# Remove any stale bytecode that crept in
find "$BUILD_DIR" -name "*.pyc" -delete 2>/dev/null || true
find "$BUILD_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

ok "Source files copied"

# ── 4. Zip ────────────────────────────────────────────────────────────────────
log "Creating zip..."

rm -f "$ZIP_FILE"
(cd "$BUILD_DIR" && zip -qr "$ZIP_FILE" . -x "*.DS_Store")

SIZE_BYTES=$(stat -f%z "$ZIP_FILE" 2>/dev/null || stat -c%s "$ZIP_FILE")
SIZE_HUMAN=$(du -h "$ZIP_FILE" | cut -f1)
log "Package size: $SIZE_HUMAN"

ok "Zip created: $ZIP_FILE"

# ── 5. Upload code to Lambda ───────────────────────────────────────────────────
DIRECT_LIMIT=$((50 * 1024 * 1024))   # 50 MB

if [[ "$SIZE_BYTES" -le "$DIRECT_LIMIT" ]]; then
  log "Uploading directly to Lambda..."
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_FILE" \
    --region "$AWS_REGION" \
    --output json > /dev/null
  ok "Code uploaded directly"
else
  [[ -n "$CODE_BUCKET" ]] \
    || fail "Package > 50 MB: --code-bucket is required for S3 upload"

  S3_KEY="lambda_a/$(date +%Y%m%d-%H%M%S)/lambda_a.zip"
  log "Package > 50 MB — uploading to s3://$CODE_BUCKET/$S3_KEY ..."
  aws s3 cp "$ZIP_FILE" "s3://$CODE_BUCKET/$S3_KEY" --region "$AWS_REGION"

  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --s3-bucket "$CODE_BUCKET" \
    --s3-key "$S3_KEY" \
    --region "$AWS_REGION" \
    --output json > /dev/null
  ok "Code uploaded via S3"
fi

# ── 6. Sync zip to S3 code bucket (keeps CloudFormation re-deployable) ─────────
if [[ -n "$CODE_BUCKET" ]]; then
  log "Syncing lambda_a.zip to s3://$CODE_BUCKET/lambda_a.zip for CloudFormation..."
  aws s3 cp "$ZIP_FILE" "s3://$CODE_BUCKET/lambda_a.zip" --region "$AWS_REGION"
  ok "S3 code bucket updated"
fi

# ── 7. Wait for update to complete ────────────────────────────────────────────
log "Waiting for function update to propagate..."
aws lambda wait function-updated \
  --function-name "$FUNCTION_NAME" \
  --region "$AWS_REGION"
ok "Function active"

# ── 8. Smoke test ─────────────────────────────────────────────────────────────
if [[ "$SKIP_TEST" == "true" ]]; then
  warn "Skipping smoke test (--skip-test set)"
else
  log "Running smoke test (OPTIONS preflight — expects 200, no auth required)..."

  # Minimal Lambda Function URL event for CORS preflight
  TEST_EVENT='{"requestContext":{"http":{"method":"OPTIONS","path":"/"}},"headers":{}}'
  RESPONSE_FILE="/tmp/lambda_a_smoke_response.json"

  STATUS_CODE=$(aws lambda invoke \
    --function-name "$FUNCTION_NAME" \
    --payload "$TEST_EVENT" \
    --region "$AWS_REGION" \
    --cli-binary-format raw-in-base64-out \
    --log-type None \
    "$RESPONSE_FILE" \
    --query 'StatusCode' \
    --output text)

  if [[ "$STATUS_CODE" == "200" ]]; then
    HTTP_CODE=$(python3 -c \
      "import json,sys; print(json.load(open('$RESPONSE_FILE')).get('statusCode','?'))")
    ok "Smoke test passed — Lambda invocation: $STATUS_CODE, HTTP statusCode: $HTTP_CODE"
  else
    warn "Lambda invoke returned status $STATUS_CODE — check CloudWatch logs:"
    warn "  aws logs tail /aws/lambda/$FUNCTION_NAME --follow --region $AWS_REGION"
  fi
fi

# ── 9. Cleanup ────────────────────────────────────────────────────────────────
rm -rf "$BUILD_DIR" "$ZIP_FILE" "/tmp/lambda_a_smoke_response.json" 2>/dev/null || true

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║              Lambda A Deployment Complete            ║"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  Function: %-42s ║\n" "$FUNCTION_NAME"
printf "║  Region:   %-42s ║\n" "$AWS_REGION"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Useful commands:                                    ║"
printf "║  %-52s ║\n" "aws logs tail /aws/lambda/$FUNCTION_NAME \\"
printf "║  %-52s ║\n" "    --follow --region $AWS_REGION"
echo "║                                                      ║"
echo "║  Next step: run 03_deploy_lambda_b.sh                ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
