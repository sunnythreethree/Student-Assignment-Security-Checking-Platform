"""
handler.py — Lambda A
Jingsi Zhang | CS6620 Group 9

Entry point for the Lambda Function URL.
Routes:
  POST /scan      → validate input → dispatch to SQS + DynamoDB → return 202
  GET  /status    → query DynamoDB → return scan status (+ presigned URL when DONE)
  GET  /history   → query DynamoDB → return last 50 scans for a student
"""

import json
import logging
import os

from validator  import validate_scan_request, normalize
from dispatcher import create_scan_job, check_rate_limit
from status     import get_scan_status
from auth       import lookup_student
from history    import get_scan_history

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Startup environment variable validation
# Raises RuntimeError at import time so Lambda reports Runtime.ImportModuleError
# with the full list of missing vars — visible in CloudWatch immediately.
# ---------------------------------------------------------------------------
_REQUIRED_ENV = ["SQS_QUEUE_URL", "DYNAMODB_TABLE", "S3_BUCKET", "AUTH_TABLE"]
_missing = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
if _missing:
    raise RuntimeError(f"Missing required environment variables: {_missing}")

SQS_QUEUE_URL  = os.environ["SQS_QUEUE_URL"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
S3_BUCKET      = os.environ["S3_BUCKET"]
AUTH_TABLE     = os.environ["AUTH_TABLE"]


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    http_ctx = event.get("requestContext", {}).get("http", {})
    method   = http_ctx.get("method", "").upper()
    path     = http_ctx.get("path", "")

    # CORS preflight — no auth required
    if method == "OPTIONS":
        return _response(200, {})

    if method == "POST":
        return _handle_post_scan(event)

    if method == "GET":
        if path == "/history":
            return _handle_get_history(event)
        return _handle_get_status(event)

    return _response(405, {"error": f"Method '{method}' not allowed."})


# ---------------------------------------------------------------------------
# POST /scan
# ---------------------------------------------------------------------------

def _handle_post_scan(event):
    # Authenticate — resolve X-Student-Key → student_id
    try:
        student_id = _resolve_student(event)
    except Exception:
        logger.exception("Auth table lookup failed")
        return _response(500, {"error": "Internal error. Please try again."})
    if not student_id:
        return _response(401, {"error": "Missing or invalid X-Student-Key header."})

    # Parse body
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON."})

    # Validate (student_id now comes from auth, not body)
    ok, error_msg = validate_scan_request(body)
    if not ok:
        return _response(400, {"error": error_msg})

    # Normalize
    clean = normalize(body)

    # Rate-limit check — max RATE_LIMIT_PER_HOUR scans per student per hour
    if not check_rate_limit(student_id, DYNAMODB_TABLE):
        logger.warning("Rate limit exceeded: student_id=%s", student_id)
        resp = _response(429, {
            "error":       "Rate limit exceeded. Maximum 10 scan submissions per hour.",
            "retry_after": 3600,
        })
        resp["headers"]["Retry-After"] = "3600"
        return resp

    # Dispatch
    try:
        scan_id = create_scan_job(
            code       = clean["code"],
            language   = clean["language"],
            student_id = student_id,
            sqs_url    = SQS_QUEUE_URL,
            table_name = DYNAMODB_TABLE,
            s3_bucket  = S3_BUCKET,
        )
    except Exception:
        logger.exception("Failed to dispatch scan job")
        return _response(500, {"error": "Internal error. Please try again."})

    logger.info("Scan job created: scan_id=%s student_id=%s", scan_id, student_id)
    return _response(202, {
        "scan_id": scan_id,
        "status":  "PENDING",
        "message": "Scan job submitted. Poll GET /status?scan_id=<id> for results.",
    })


# ---------------------------------------------------------------------------
# GET /status?scan_id=xxx
# ---------------------------------------------------------------------------

def _handle_get_status(event):
    # Authenticate
    try:
        student_id = _resolve_student(event)
    except Exception:
        logger.exception("Auth table lookup failed")
        return _response(500, {"error": "Internal error. Please try again."})
    if not student_id:
        return _response(401, {"error": "Missing or invalid X-Student-Key header."})

    params  = event.get("queryStringParameters") or {}
    scan_id = params.get("scan_id", "").strip()

    if not scan_id:
        return _response(400, {"error": "Query parameter 'scan_id' is required."})

    try:
        result = get_scan_status(
            scan_id    = scan_id,
            student_id = student_id,
            table_name = DYNAMODB_TABLE,
            s3_bucket  = S3_BUCKET,
        )
    except ValueError as e:
        return _response(404, {"error": str(e)})
    except Exception:
        logger.exception("Failed to fetch scan status for scan_id=%s", scan_id)
        return _response(500, {"error": "Internal error. Please try again."})

    return _response(200, result)


# ---------------------------------------------------------------------------
# GET /history?student_id=xxx
# ---------------------------------------------------------------------------

def _handle_get_history(event):
    # Authenticate
    try:
        student_id = _resolve_student(event)
    except Exception:
        logger.exception("Auth table lookup failed")
        return _response(500, {"error": "Internal error. Please try again."})
    if not student_id:
        return _response(401, {"error": "Missing or invalid X-Student-Key header."})

    try:
        scans = get_scan_history(
            student_id = student_id,
            table_name = DYNAMODB_TABLE,
        )
    except Exception:
        logger.exception("Failed to fetch scan history for student_id=%s", student_id)
        return _response(500, {"error": "Internal error. Please try again."})

    return _response(200, {"student_id": student_id, "scans": scans})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_student(event) -> str | None:
    """
    Extract X-Student-Key from request headers and resolve it to a student_id.
    Returns None if the header is absent or the key is not found in the auth table.
    """
    headers = event.get("headers") or {}
    api_key = headers.get("x-student-key", "").strip()
    if not api_key:
        return None
    return lookup_student(api_key, AUTH_TABLE)


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(body),
    }
