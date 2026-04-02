"""
handler.py — Lambda A
Jingsi Zhang | CS6620 Group 9

Entry point for the Lambda Function URL.
Routes:
  POST /scan    → validate input → dispatch to SQS + DynamoDB → return 202
  GET  /status  → query DynamoDB → return scan status (+ presigned URL when DONE)
"""

import json
import logging
import os

from validator  import validate_scan_request, normalize
from dispatcher import create_scan_job
from status     import get_scan_status

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Environment variables — set these in the Lambda console or CloudFormation
SQS_QUEUE_URL  = os.environ["SQS_QUEUE_URL"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
S3_BUCKET      = os.environ["S3_BUCKET"]


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    method = (
        event.get("requestContext", {})
             .get("http", {})
             .get("method", "")
             .upper()
    )

    # CORS preflight
    if method == "OPTIONS":
        return _response(200, {})

    if method == "POST":
        return _handle_post_scan(event)

    if method == "GET":
        return _handle_get_status(event)

    return _response(405, {"error": f"Method '{method}' not allowed."})


# ---------------------------------------------------------------------------
# POST /scan
# ---------------------------------------------------------------------------

def _handle_post_scan(event):
    # Parse body
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON."})

    # Validate
    ok, error_msg = validate_scan_request(body)
    if not ok:
        return _response(400, {"error": error_msg})

    # Normalize
    clean = normalize(body)

    # Dispatch
    try:
        scan_id = create_scan_job(
            code       = clean["code"],
            language   = clean["language"],
            student_id = clean["student_id"],
            sqs_url    = SQS_QUEUE_URL,
            table_name = DYNAMODB_TABLE,
            s3_bucket  = S3_BUCKET,
        )
    except Exception as e:
        logger.exception("Failed to dispatch scan job")
        return _response(500, {"error": "Internal error. Please try again."})

    logger.info("Scan job created: scan_id=%s", scan_id)
    return _response(202, {
        "scan_id": scan_id,
        "status":  "PENDING",
        "message": "Scan job submitted. Poll GET /status?scan_id=<id> for results.",
    })


# ---------------------------------------------------------------------------
# GET /status?scan_id=xxx
# ---------------------------------------------------------------------------

def _handle_get_status(event):
    params  = event.get("queryStringParameters") or {}
    scan_id = params.get("scan_id", "").strip()

    if not scan_id:
        return _response(400, {"error": "Query parameter 'scan_id' is required."})

    try:
        result = get_scan_status(
            scan_id    = scan_id,
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
# Helper
# ---------------------------------------------------------------------------

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
