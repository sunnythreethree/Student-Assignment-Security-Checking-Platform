"""
handler.py — Lambda A
CS6620 Group 9

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
from dispatcher import create_scan_job
from status     import get_scan_status
from history    import get_scan_history

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Startup environment variable validation
# ---------------------------------------------------------------------------
_REQUIRED_ENV = ["SQS_QUEUE_URL", "DYNAMODB_TABLE", "S3_BUCKET"]
_missing = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
if _missing:
    raise RuntimeError(f"Missing required environment variables: {_missing}")

SQS_QUEUE_URL  = os.environ["SQS_QUEUE_URL"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
S3_BUCKET      = os.environ["S3_BUCKET"]

ANONYMOUS_ID = "anonymous"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    http_ctx = event.get("requestContext", {}).get("http", {})
    method   = http_ctx.get("method", "").upper()
    path     = http_ctx.get("path", "")

    # CORS preflight
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
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON."})

    # Extract student_id from body (optional — falls back to "anonymous")
    student_id = str(body.get("student_id", "") or "").strip() or ANONYMOUS_ID

    ok, error_msg = validate_scan_request(body)
    if not ok:
        return _response(400, {"error": error_msg})

    clean = normalize(body)

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
    params     = event.get("queryStringParameters") or {}
    scan_id    = params.get("scan_id", "").strip()
    student_id = params.get("student_id", "").strip() or ANONYMOUS_ID

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
    params     = event.get("queryStringParameters") or {}
    student_id = params.get("student_id", "").strip()

    if not student_id:
        return _response(400, {"error": "Query parameter 'student_id' is required."})

    try:
        scans = get_scan_history(
            student_id = student_id,
            table_name = DYNAMODB_TABLE,
        )
    except Exception:
        logger.exception("Failed to fetch history for student_id=%s", student_id)
        return _response(500, {"error": "Internal error. Please try again."})

    return _response(200, {"student_id": student_id, "scans": scans})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type":                 "application/json",
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(obj):
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
