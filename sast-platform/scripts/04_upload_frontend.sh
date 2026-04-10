#!/usr/bin/env bash
# 04_upload_frontend.sh — Build and deploy frontend static files to S3.
#
# What it does:
#   1. Pre-flight: verifies AWS CLI, credentials, and bucket existence
#   2. Reads Lambda A Function URL from CloudFormation outputs
#   3. Injects the URL into app.js via the __LAMBDA_URL__ placeholder
#   4. Syncs all frontend files to the S3 frontend bucket with correct
#      Content-Type headers per file type
#   5. Prints the live S3 website URL
#
# Usage:
#   ./04_upload_frontend.sh [OPTIONS]
#
# Optional flags:
#   --project       Project name prefix         (default: sast-platform)
#   --env           Deployment environment       (default: dev)
#   --region        AWS region                   (default: us-east-1)
#   --bucket        Frontend S3 bucket name      (default: <project>-frontend-<env>)
#   --lambda-stack  Lambda A CloudFormation stack name
#                                                (default: <project>-lambda-a)
#   --lambda-url    Skip CF lookup; inject this URL directly
#   --skip-inject   Upload without URL injection (for local testing)
#
# Environment variable equivalents (override flags):
#   PROJECT_NAME, ENVIRONMENT, AWS_REGION, FRONTEND_BUCKET,
#   LAMBDA_A_STACK, LAMBDA_URL, SKIP_INJECT
#
# Deploy order: run AFTER 01_setup_infra.sh and 02_deploy_lambda_a.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(dirname "$SCRIPT_DIR")/frontend"
BUILD_DIR="/tmp/sasc_frontend_build"

# ── Defaults ──────────────────────────────────────────────────────────────────
PROJECT_NAME="${PROJECT_NAME:-sast-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
FRONTEND_BUCKET="${FRONTEND_BUCKET:-}"
LAMBDA_A_STACK="${LAMBDA_A_STACK:-}"
LAMBDA_URL="${LAMBDA_URL:-}"
SKIP_INJECT="${SKIP_INJECT:-false}"

# ── Argument parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)      PROJECT_NAME="$2";    shift 2 ;;
    --env)          ENVIRONMENT="$2";     shift 2 ;;
    --region)       AWS_REGION="$2";      shift 2 ;;
    --bucket)       FRONTEND_BUCKET="$2"; shift 2 ;;
    --lambda-stack) LAMBDA_A_STACK="$2";  shift 2 ;;
    --lambda-url)   LAMBDA_URL="$2";      shift 2 ;;
    --skip-inject)  SKIP_INJECT="true";   shift   ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# Apply defaults that depend on PROJECT_NAME / ENVIRONMENT
FRONTEND_BUCKET="${FRONTEND_BUCKET:-${PROJECT_NAME}-frontend-${ENVIRONMENT}}"
LAMBDA_A_STACK="${LAMBDA_A_STACK:-${PROJECT_NAME}-lambda-a}"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARN:${NC} $*"; }
fail() { echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $*" >&2; exit 1; }

# ── Pre-flight checks ─────────────────────────────────────────────────────────

check_aws_cli() {
  command -v aws &>/dev/null || \
    fail "AWS CLI not found. Install: https://aws.amazon.com/cli/"
  log "AWS CLI: $(aws --version 2>&1 | head -1)"
}

check_credentials() {
  local identity
  identity=$(aws sts get-caller-identity --region "$AWS_REGION" 2>/dev/null) || \
    fail "AWS credentials not configured or expired. Run 'aws configure' or check your session."
  local account
  account=$(echo "$identity" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])" 2>/dev/null \
            || echo "$identity" | grep -o '"Account": "[^"]*"' | cut -d'"' -f4)
  log "AWS account: $account  region: $AWS_REGION"
}

check_frontend_dir() {
  [[ -d "$FRONTEND_DIR" ]] || \
    fail "Frontend directory not found: $FRONTEND_DIR"
  [[ -f "$FRONTEND_DIR/index.html" ]] || \
    fail "index.html not found in $FRONTEND_DIR — run issue #6 first"
  [[ -f "$FRONTEND_DIR/js/app.js" ]] || \
    fail "js/app.js not found in $FRONTEND_DIR — run issue #6 first"
}

check_bucket() {
  aws s3api head-bucket --bucket "$FRONTEND_BUCKET" --region "$AWS_REGION" &>/dev/null || \
    fail "Bucket '$FRONTEND_BUCKET' not found or not accessible. Run 01_setup_infra.sh first."
  log "Bucket exists: s3://$FRONTEND_BUCKET"
}

# ── Lambda URL resolution ──────────────────────────────────────────────────────

get_lambda_url_from_cf() {
  local url
  url=$(aws cloudformation describe-stacks \
    --stack-name "$LAMBDA_A_STACK" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='LambdaAApiUrl'].OutputValue" \
    --output text 2>/dev/null || true)

  if [[ -z "$url" || "$url" == "None" ]]; then
    fail "Could not read ApiGatewayUrl from stack '$LAMBDA_A_STACK'.\n  Make sure Lambda A is deployed: ./02_deploy_lambda_a.sh"
  fi

  # Strip trailing slash so URLs are consistent
  echo "${url%/}"
}

# ── Build step: copy to temp dir and write config.json ───────────────────────
#
# config.json is fetched at runtime by app.js — no URL is baked into the JS
# bundle, so updating the endpoint only requires re-deploying this one file.

build_frontend() {
  local lambda_url="$1"

  log "Preparing build directory: $BUILD_DIR"
  rm -rf "$BUILD_DIR"
  cp -r "$FRONTEND_DIR/." "$BUILD_DIR/"

  if [[ "$SKIP_INJECT" == "true" ]]; then
    warn "Skipping config.json generation (--skip-inject)."
    return
  fi

  printf '{"apiUrl":"%s"}' "$lambda_url" > "$BUILD_DIR/config.json"
  log "Generated config.json"
  log "  apiUrl = \"$lambda_url\""
}

# ── Upload: sync each file type with correct Content-Type ─────────────────────
#
# Strategy: three separate syncs filtered by extension so AWS CLI sets the
# right Content-Type header for each group.  --delete is applied to HTML and
# JS (the files users fetch directly); CSS is treated similarly.
# Running --delete per-type is safe: it only removes files matching that
# type's filter that no longer exist locally.

upload_frontend() {
  log "Uploading frontend to s3://$FRONTEND_BUCKET ..."

  # HTML — no-cache so browsers always fetch the latest index.html
  aws s3 sync "$BUILD_DIR" "s3://$FRONTEND_BUCKET" \
    --region "$AWS_REGION" \
    --delete \
    --exclude "*" \
    --include "*.html" \
    --content-type "text/html; charset=utf-8" \
    --cache-control "no-cache, no-store, must-revalidate"
  log "  ✓ HTML uploaded"

  # CSS
  aws s3 sync "$BUILD_DIR" "s3://$FRONTEND_BUCKET" \
    --region "$AWS_REGION" \
    --delete \
    --exclude "*" \
    --include "*.css" \
    --content-type "text/css; charset=utf-8" \
    --cache-control "no-cache, no-store, must-revalidate"
  log "  ✓ CSS uploaded"

  # JavaScript
  aws s3 sync "$BUILD_DIR" "s3://$FRONTEND_BUCKET" \
    --region "$AWS_REGION" \
    --delete \
    --exclude "*" \
    --include "*.js" \
    --content-type "application/javascript; charset=utf-8" \
    --cache-control "no-cache, no-store, must-revalidate"
  log "  ✓ JavaScript uploaded"

  # config.json — no-cache so URL changes are picked up immediately
  if [[ -f "$BUILD_DIR/config.json" ]]; then
    aws s3 cp "$BUILD_DIR/config.json" "s3://$FRONTEND_BUCKET/config.json" \
      --region "$AWS_REGION" \
      --content-type "application/json" \
      --cache-control "no-cache, no-store, must-revalidate"
    log "  ✓ config.json uploaded"
  fi

  # Everything else (favicon, fonts, images…) — no explicit Content-Type override
  aws s3 sync "$BUILD_DIR" "s3://$FRONTEND_BUCKET" \
    --region "$AWS_REGION" \
    --delete \
    --exclude "*.html" \
    --exclude "*.css" \
    --exclude "*.js"
  log "  ✓ Other assets uploaded"
}

# ── Summary ───────────────────────────────────────────────────────────────────

print_summary() {
  local website_url="http://${FRONTEND_BUCKET}.s3-website-${AWS_REGION}.amazonaws.com"

  # Try to read the website URL from the CF stack output (more reliable)
  local cf_url
  cf_url=$(aws cloudformation describe-stacks \
    --stack-name "${PROJECT_NAME}-s3" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='FrontendWebsiteURL'].OutputValue" \
    --output text 2>/dev/null || true)
  [[ -n "$cf_url" && "$cf_url" != "None" ]] && website_url="$cf_url"

  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║               Frontend Deploy Complete               ║"
  echo "╠══════════════════════════════════════════════════════╣"
  printf "║  Bucket:  %-43s ║\n" "$FRONTEND_BUCKET"
  printf "║  URL:     %-43s ║\n" "$website_url"
  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  Next step: open the URL above in your browser       ║"
  echo "╚══════════════════════════════════════════════════════╝"
  echo ""
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║       SAST Platform — Frontend Upload                ║"
  echo "╠══════════════════════════════════════════════════════╣"
  printf "║  Project:  %-42s ║\n" "$PROJECT_NAME"
  printf "║  Env:      %-42s ║\n" "$ENVIRONMENT"
  printf "║  Region:   %-42s ║\n" "$AWS_REGION"
  printf "║  Bucket:   %-42s ║\n" "$FRONTEND_BUCKET"
  printf "║  Stack:    %-42s ║\n" "$LAMBDA_A_STACK"
  echo "╚══════════════════════════════════════════════════════╝"
  echo ""

  log "Running pre-flight checks..."
  check_aws_cli
  check_credentials
  check_frontend_dir
  check_bucket

  if [[ "$SKIP_INJECT" != "true" ]]; then
    if [[ -n "$LAMBDA_URL" ]]; then
      log "Using provided Lambda URL: $LAMBDA_URL"
    else
      log "Reading Lambda A URL from CloudFormation stack: $LAMBDA_A_STACK"
      LAMBDA_URL=$(get_lambda_url_from_cf)
    fi
  else
    LAMBDA_URL="__LAMBDA_URL__"
  fi

  build_frontend "$LAMBDA_URL"
  upload_frontend
  print_summary
}

main "$@"
