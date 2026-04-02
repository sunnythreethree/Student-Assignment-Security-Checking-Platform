#!/usr/bin/env bash
# 05_test_api.sh — Smoke test for a live SAST Platform deployment
# CS6620 Group 9
#
# Submits a Python code snippet, polls until the scan is DONE, and prints
# the vulnerability count. Use this after each deployment as a quick sanity check.
#
# Usage:
#   ./05_test_api.sh --url <LAMBDA_URL> --key <STUDENT_KEY>
#
# Or via environment variables:
#   LAMBDA_URL=https://... STUDENT_KEY=abc123 ./05_test_api.sh

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
LAMBDA_URL="${LAMBDA_URL:-}"
STUDENT_KEY="${STUDENT_KEY:-}"
POLL_INTERVAL=5
POLL_TIMEOUT=120

# ── Argument parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) LAMBDA_URL="$2";  shift 2 ;;
    --key) STUDENT_KEY="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Validation ─────────────────────────────────────────────────────────────────
if [[ -z "$LAMBDA_URL" ]]; then
  echo "ERROR: LAMBDA_URL is required (--url or env var)"
  echo "       Find it in the CloudFormation output: sast-lambda-a > LambdaAFunctionUrl"
  exit 1
fi
if [[ -z "$STUDENT_KEY" ]]; then
  echo "ERROR: STUDENT_KEY is required (--key or env var)"
  echo "       Generate one with: python scripts/00_seed_auth.py --add-student <your-id>"
  exit 1
fi
if ! command -v curl &>/dev/null; then
  echo "ERROR: curl is required"; exit 1
fi
if ! command -v jq &>/dev/null; then
  echo "ERROR: jq is required (brew install jq / apt install jq)"; exit 1
fi

LAMBDA_URL="${LAMBDA_URL%/}"  # strip trailing slash

# ── Test code payload ─────────────────────────────────────────────────────────
# Contains one known Bandit finding (B602 — shell injection) so vuln_count > 0
TEST_CODE='import subprocess
user_cmd = input("Enter command: ")
subprocess.call(user_cmd, shell=True)
'

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✓ $*"; }
fail() { echo "[$(date '+%H:%M:%S')] ✗ $*" >&2; exit 1; }

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         SAST Platform — API Smoke Test               ║"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  URL: %-46s ║\n" "${LAMBDA_URL:0:46}"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: POST /scan ────────────────────────────────────────────────────────
log "Submitting scan..."

PAYLOAD=$(jq -n --arg code "$TEST_CODE" --arg lang "python" \
  '{"code": $code, "language": $lang}')

SUBMIT_RESP=$(curl -s -w "\n%{http_code}" \
  -X POST "$LAMBDA_URL/scan" \
  -H "Content-Type: application/json" \
  -H "X-Student-Key: $STUDENT_KEY" \
  -d "$PAYLOAD")

HTTP_STATUS=$(echo "$SUBMIT_RESP" | tail -1)
BODY=$(echo "$SUBMIT_RESP" | head -n -1)

if [[ "$HTTP_STATUS" != "202" ]]; then
  fail "POST /scan returned $HTTP_STATUS: $BODY"
fi

SCAN_ID=$(echo "$BODY" | jq -r '.scan_id')
[[ -z "$SCAN_ID" || "$SCAN_ID" == "null" ]] && fail "No scan_id in response: $BODY"

ok "Scan submitted — scan_id: $SCAN_ID"

# ── Step 2: Poll GET /status ──────────────────────────────────────────────────
log "Polling for results (timeout: ${POLL_TIMEOUT}s)..."

DEADLINE=$(( $(date +%s) + POLL_TIMEOUT ))
STATUS="PENDING"

while [[ "$STATUS" == "PENDING" || "$STATUS" == "IN_PROGRESS" ]]; do
  if [[ $(date +%s) -gt $DEADLINE ]]; then
    fail "Timed out waiting for scan to complete (>${POLL_TIMEOUT}s)"
  fi

  sleep "$POLL_INTERVAL"

  STATUS_RESP=$(curl -s -w "\n%{http_code}" \
    "$LAMBDA_URL/status?scan_id=$SCAN_ID" \
    -H "X-Student-Key: $STUDENT_KEY")

  HTTP_STATUS=$(echo "$STATUS_RESP" | tail -1)
  STATUS_BODY=$(echo "$STATUS_RESP" | head -n -1)

  [[ "$HTTP_STATUS" != "200" ]] && fail "GET /status returned $HTTP_STATUS: $STATUS_BODY"

  STATUS=$(echo "$STATUS_BODY" | jq -r '.status')
  log "  status: $STATUS"
done

# ── Step 3: Results ───────────────────────────────────────────────────────────
echo ""
if [[ "$STATUS" == "DONE" ]]; then
  VULN_COUNT=$(echo "$STATUS_BODY" | jq -r '.vuln_count // 0')
  REPORT_URL=$(echo "$STATUS_BODY" | jq -r '.report_url // "N/A"')
  COMPLETED=$(echo "$STATUS_BODY" | jq -r '.completed_at // "N/A"')

  echo "╔══════════════════════════════════════════════════════╗"
  echo "║                   Scan Results                      ║"
  echo "╠══════════════════════════════════════════════════════╣"
  printf "║  scan_id:     %-37s ║\n" "$SCAN_ID"
  printf "║  status:      %-37s ║\n" "DONE"
  printf "║  vuln_count:  %-37s ║\n" "$VULN_COUNT"
  printf "║  completed:   %-37s ║\n" "${COMPLETED:0:37}"
  echo "╠══════════════════════════════════════════════════════╣"
  printf "║  report_url: %-38s ║\n" "(see below)"
  echo "╚══════════════════════════════════════════════════════╝"
  echo ""
  echo "Report URL:"
  echo "$REPORT_URL"
  echo ""

  if [[ "$VULN_COUNT" -gt 0 ]]; then
    ok "Smoke test passed — $VULN_COUNT finding(s) detected as expected."
  else
    echo "WARNING: vuln_count=0 for code that contains a known shell injection."
    echo "         Check that Bandit is installed and Lambda B is processing messages."
  fi
elif [[ "$STATUS" == "FAILED" ]]; then
  ERROR=$(echo "$STATUS_BODY" | jq -r '.error_message // "unknown error"')
  fail "Scan failed: $ERROR"
else
  fail "Unexpected status: $STATUS"
fi
