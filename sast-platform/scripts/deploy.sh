#!/usr/bin/env bash
# deploy.sh — Single-command full-stack deployment for the SAST Platform.
#
# Runs all deployment steps in order:
#   1. 01_setup_infra.sh    → CloudFormation stacks (S3, DynamoDB, SQS, Lambda A/B, ECS, CloudWatch)
#   2. 02_deploy_lambda_a.sh → Package and deploy Lambda A code
#   3. 03_deploy_lambda_b.sh → Package and deploy Lambda B code
#   4. 04_build_ecs_image.sh → Build and push ECS scanner image (only if --vpc-id is set)
#   5. 04_upload_frontend.sh → Inject Lambda URL and sync frontend to S3
#   6. 05_test_api.sh       → End-to-end smoke test (requires --student-key)
#
# Usage:
#   ./deploy.sh --code-bucket <bucket> [OPTIONS]
#
# Required:
#   --code-bucket    S3 bucket for Lambda deployment packages
#
# Optional:
#   --project        Project name prefix        (default: sast-platform)
#   --env            Deployment environment      (default: dev)
#   --region         AWS region                 (default: us-east-1)
#   --vpc-id         VPC ID — enables ECS Fargate stack + ECS image build
#   --subnets        Comma-separated subnet IDs  (required with --vpc-id)
#   --scanner-image  ECR image URI override for ECS scanner
#   --student-key    API key for smoke test      (skips 05_test_api.sh if omitted)
#   --skip-ecs       Skip ECS image build even when --vpc-id is provided
#   --skip-test      Skip end-to-end smoke test unconditionally
#
# Environment variable equivalents (flags take precedence):
#   CODE_BUCKET, PROJECT_NAME, ENVIRONMENT, AWS_REGION,
#   VPC_ID, SUBNET_IDS, SCANNER_IMAGE, STUDENT_KEY, SKIP_TEST

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Defaults ──────────────────────────────────────────────────────────────────
PROJECT_NAME="${PROJECT_NAME:-sast-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
CODE_BUCKET="${CODE_BUCKET:-}"
VPC_ID="${VPC_ID:-}"
SUBNET_IDS="${SUBNET_IDS:-}"
SCANNER_IMAGE="${SCANNER_IMAGE:-}"
STUDENT_KEY="${STUDENT_KEY:-}"
SKIP_TEST="${SKIP_TEST:-false}"
SKIP_ECS=false

# ── Argument parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --code-bucket)   CODE_BUCKET="$2";    shift 2 ;;
    --project)       PROJECT_NAME="$2";   shift 2 ;;
    --env)           ENVIRONMENT="$2";    shift 2 ;;
    --region)        AWS_REGION="$2";     shift 2 ;;
    --vpc-id)        VPC_ID="$2";         shift 2 ;;
    --subnets)       SUBNET_IDS="$2";     shift 2 ;;
    --scanner-image) SCANNER_IMAGE="$2";  shift 2 ;;
    --student-key)   STUDENT_KEY="$2";    shift 2 ;;
    --skip-ecs)      SKIP_ECS=true;       shift   ;;
    --skip-test)     SKIP_TEST="true";    shift   ;;
    *) echo "ERROR: Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Validation ─────────────────────────────────────────────────────────────────
if [[ -z "$CODE_BUCKET" ]]; then
  echo "ERROR: --code-bucket is required."
  echo "       Usage: ./deploy.sh --code-bucket <s3-bucket> [OPTIONS]"
  exit 1
fi

if [[ -n "$VPC_ID" && -z "$SUBNET_IDS" ]]; then
  echo "ERROR: --subnets is required when --vpc-id is set."
  exit 1
fi

# ── Export env vars consumed by child scripts ─────────────────────────────────
export PROJECT_NAME ENVIRONMENT AWS_REGION CODE_BUCKET SKIP_TEST

# ── Helpers ───────────────────────────────────────────────────────────────────
step() { echo ""; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; echo "[$1/7] $2"; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✓ $*"; }

get_cf_output() {
  local stack="$1" key="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" \
    --output text 2>/dev/null || true
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         SAST Platform — Full-Stack Deploy            ║"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  Project:    %-38s ║\n" "$PROJECT_NAME"
printf "║  Environment:%-38s ║\n" "$ENVIRONMENT"
printf "║  Region:     %-38s ║\n" "$AWS_REGION"
printf "║  Code Bucket:%-38s ║\n" "$CODE_BUCKET"
if [[ -n "$VPC_ID" && "$SKIP_ECS" == "false" ]]; then
printf "║  ECS:        %-38s ║\n" "enabled (VPC: $VPC_ID)"
else
printf "║  ECS:        %-38s ║\n" "skipped"
fi
printf "║  Smoke test: %-38s ║\n" "$([[ -n "$STUDENT_KEY" && "$SKIP_TEST" != "true" ]] && echo "enabled" || echo "skipped")"
echo "╚══════════════════════════════════════════════════════╝"

# ── Step 0: Pre-package Lambda zips → S3 code bucket ─────────────────────────
# CloudFormation Lambda stacks reference lambda_a.zip / lambda_b.zip from the
# code bucket.  On a fresh deploy those zips don't exist yet, so we package and
# upload them BEFORE deploying the CF stacks.
step 0 "Pre-packaging Lambda code → s3://$CODE_BUCKET"

PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Lambda A (pure Python, no extra deps)
_LAMBDA_A_BUILD="/tmp/sasc_lambda_a_prebuild"
_LAMBDA_A_ZIP="/tmp/sasc_lambda_a_prebuild.zip"
rm -rf "$_LAMBDA_A_BUILD" "$_LAMBDA_A_ZIP"
mkdir -p "$_LAMBDA_A_BUILD"
cp "$PROJECT_ROOT/lambda_a/"*.py "$_LAMBDA_A_BUILD/"
find "$_LAMBDA_A_BUILD" -name "*.pyc" -delete 2>/dev/null || true
(cd "$_LAMBDA_A_BUILD" && zip -qr "$_LAMBDA_A_ZIP" .)
aws s3 cp "$_LAMBDA_A_ZIP" "s3://$CODE_BUCKET/lambda_a.zip" --region "$AWS_REGION"
ok "lambda_a.zip uploaded to s3://$CODE_BUCKET"

# Lambda B (needs bandit + deps from requirements.txt)
_LAMBDA_B_BUILD="/tmp/sasc_lambda_b_prebuild"
_LAMBDA_B_ZIP="/tmp/sasc_lambda_b_prebuild.zip"
rm -rf "$_LAMBDA_B_BUILD" "$_LAMBDA_B_ZIP"
mkdir -p "$_LAMBDA_B_BUILD"
cp "$PROJECT_ROOT/lambda_b/"*.py "$_LAMBDA_B_BUILD/"
cp "$PROJECT_ROOT/lambda_b/requirements.txt" "$_LAMBDA_B_BUILD/"
rm -f "$_LAMBDA_B_BUILD/ecs_handler.py"
python3 -m pip install --target "$_LAMBDA_B_BUILD" -r "$_LAMBDA_B_BUILD/requirements.txt" \
  --quiet --upgrade
find "$_LAMBDA_B_BUILD" -name "*.pyc" -delete 2>/dev/null || true
find "$_LAMBDA_B_BUILD" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$_LAMBDA_B_BUILD" -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find "$_LAMBDA_B_BUILD" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
(cd "$_LAMBDA_B_BUILD" && zip -qr "$_LAMBDA_B_ZIP" .)
aws s3 cp "$_LAMBDA_B_ZIP" "s3://$CODE_BUCKET/lambda_b.zip" --region "$AWS_REGION"
ok "lambda_b.zip uploaded to s3://$CODE_BUCKET"

# ── Step 1: CloudFormation infrastructure ─────────────────────────────────────
step 1 "Deploying CloudFormation stacks (infra)"

INFRA_ARGS=(
  --code-bucket "$CODE_BUCKET"
  --project     "$PROJECT_NAME"
  --env         "$ENVIRONMENT"
  --region      "$AWS_REGION"
)
[[ -n "$VPC_ID" ]]       && INFRA_ARGS+=(--vpc-id "$VPC_ID")
[[ -n "$SUBNET_IDS" ]]   && INFRA_ARGS+=(--subnets "$SUBNET_IDS")
[[ -n "$SCANNER_IMAGE" ]] && INFRA_ARGS+=(--scanner-image "$SCANNER_IMAGE")

"$SCRIPT_DIR/01_setup_infra.sh" "${INFRA_ARGS[@]}"
ok "Infrastructure stacks deployed"

# ── Step 2: Lambda A ──────────────────────────────────────────────────────────
step 2 "Updating Lambda A code (API layer)"

"$SCRIPT_DIR/02_deploy_lambda_a.sh" \
  --code-bucket "$CODE_BUCKET" \
  --project     "$PROJECT_NAME" \
  --env         "$ENVIRONMENT" \
  --region      "$AWS_REGION" \
  --skip-test
ok "Lambda A deployed"

# ── Step 3: Lambda B ──────────────────────────────────────────────────────────
step 3 "Updating Lambda B code (scanner engine)"

# 03_deploy_lambda_b.sh reads PROJECT_NAME, ENVIRONMENT, AWS_REGION, CODE_BUCKET from env
SKIP_TEST="true" "$SCRIPT_DIR/03_deploy_lambda_b.sh"
ok "Lambda B deployed"

# ── Step 4: ECS image (optional) ──────────────────────────────────────────────
if [[ -n "$VPC_ID" && "$SKIP_ECS" == "false" ]]; then
  step 4 "Building and pushing ECS scanner image"
  SKIP_TEST="true" "$SCRIPT_DIR/04_build_ecs_image.sh"
  ok "ECS image built and pushed"
else
  step 4 "ECS image build — skipped"
  echo "(pass --vpc-id and omit --skip-ecs to enable)"
fi

# ── Step 5: Frontend ──────────────────────────────────────────────────────────
step 5 "Uploading frontend to S3"


"$SCRIPT_DIR/04_upload_frontend.sh" \
  --project "$PROJECT_NAME" \
  --env     "$ENVIRONMENT" \
  --region  "$AWS_REGION"
ok "Frontend uploaded"

# ── Step 6: Smoke test (optional) ─────────────────────────────────────────────
step 6 "End-to-end smoke test"


if [[ "$SKIP_TEST" == "true" ]]; then
  echo "Skipped (--skip-test)"
elif [[ -z "$STUDENT_KEY" ]]; then
  echo "Skipped (no --student-key provided)."
  echo "To run manually:"
  LAMBDA_URL="$(get_cf_output "${PROJECT_NAME}-lambda-a" LambdaAFunctionUrl)"
  echo "  LAMBDA_URL=$LAMBDA_URL \\"
  echo "  STUDENT_KEY=<your-key> \\"
  echo "  ./05_test_api.sh"
else
  LAMBDA_URL="$(get_cf_output "${PROJECT_NAME}-lambda-a" LambdaAFunctionUrl)"
  if [[ -z "$LAMBDA_URL" || "$LAMBDA_URL" == "None" ]]; then
    echo "WARNING: Could not resolve Lambda A URL from CloudFormation — skipping smoke test."
  else
    "$SCRIPT_DIR/05_test_api.sh" --url "$LAMBDA_URL" --key "$STUDENT_KEY"
    ok "Smoke test passed"
  fi
fi

# ── Final summary ─────────────────────────────────────────────────────────────
LAMBDA_URL="$(get_cf_output "${PROJECT_NAME}-lambda-a" LambdaAFunctionUrl || true)"
FRONTEND_URL="$(get_cf_output "${PROJECT_NAME}-s3" FrontendWebsiteURL || true)"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║            Deployment Complete                       ║"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  Lambda A URL:   %-34s ║\n" "${LAMBDA_URL:-<check console>}"
printf "║  Frontend URL:   %-34s ║\n" "${FRONTEND_URL:-<check console>}"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Seed API keys:                                      ║"
printf "║  %-52s ║\n" "python scripts/00_seed_auth.py \\"
printf "║  %-52s ║\n" "  --table StudentAuth --add-student <id>"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
