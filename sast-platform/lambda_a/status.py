"""
status.py — Lambda A
Jingsi Zhang | CS6620 Group 9

Handles GET /status?scan_id=xxx
Queries DynamoDB and, when the scan is DONE, returns an S3 presigned URL
so the frontend can fetch the report directly.
"""

import logging
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
s3       = boto3.client("s3")

PRESIGNED_URL_EXPIRY   = 3600   # seconds (1 hour)
SCAN_TTL_HOURS         = 1      # scans older than this are considered expired
POLLING_INTERVAL_S     = 5      # suggested client poll interval (seconds)
ECS_POLLING_INTERVAL_S = 30     # ECS tasks take longer — poll less aggressively

# A scan stuck IN_PROGRESS longer than this threshold is considered stale:
# Lambda B crashed or was OOM-killed before writing DONE/FAILED.
# Covers Lambda B max timeout (900 s / 15 min) AND ECS Fargate scan timeout
# (1800 s / 30 min) plus a 5-minute buffer.
_STALE_IN_PROGRESS_MINUTES = 35


def get_scan_status(scan_id: str, student_id: str, table_name: str, s3_bucket: str) -> dict:
    """
    Look up a scan record using the table primary key (student_id + scan_id).

    Using the primary key instead of the GSI enforces ownership at the database
    level: a student can only retrieve their own scan.  If the (student_id,
    scan_id) pair does not exist — whether because the scan doesn't exist or
    belongs to a different student — a ValueError is raised with the same
    message, preventing cross-tenant enumeration.

    Returns a dict with:
      - status: PENDING | IN_PROGRESS | ECS_QUEUED | DONE | FAILED
      - scan_id, language, created_at
      - (PENDING/IN_PROGRESS/ECS_QUEUED) retry_after_seconds, scan_expires_at
      - (DONE) vuln_count, completed_at, report_url, report_url_expires_at
      - (FAILED) error_message, completed_at
      - (stale IN_PROGRESS) status overridden to FAILED with error_message

    Raises:
        ValueError  if scan_id not found or does not belong to student_id
        ClientError if AWS call fails
    """
    table = dynamodb.Table(table_name)

    # Primary-key lookup — enforces ownership and avoids the GSI entirely
    response = table.get_item(
        Key={"student_id": student_id, "scan_id": scan_id}
    )

    item = response.get("Item")
    if not item:
        # Same error for not-found and wrong-owner — prevents enumeration
        raise ValueError(f"scan_id '{scan_id}' not found.")

    status = item["status"]

    # Stale IN_PROGRESS detection: if the scan has been IN_PROGRESS longer than
    # the maximum possible execution time for Lambda B + ECS, the worker almost
    # certainly crashed without writing a terminal status.  Surface FAILED to the
    # student immediately rather than making them wait up to 24 h for TTL cleanup.
    # The DynamoDB record is NOT updated here — this is a read-only response
    # override so a subsequent Lambda B completion (however unlikely) can still
    # write the real outcome.
    if status == "IN_PROGRESS":
        status = _resolve_in_progress_status(item)

    result = {
        "scan_id":    item["scan_id"],
        "status":     status,
        "language":   item.get("language"),
        "created_at": item.get("created_at"),
    }

    if status in ("PENDING", "IN_PROGRESS", "ECS_QUEUED"):
        # Tell the client how long to wait before the next poll, and when to
        # give up entirely so it doesn't loop forever on a stuck scan.
        # ECS tasks take up to 30 min — use a longer interval to reduce noise.
        result["retry_after_seconds"] = (
            ECS_POLLING_INTERVAL_S if status == "ECS_QUEUED"
            else POLLING_INTERVAL_S
        )

        created_at = item.get("created_at")
        if created_at:
            created_dt = datetime.fromisoformat(created_at.rstrip("Z")).replace(tzinfo=timezone.utc)
            expires_at = created_dt + timedelta(hours=SCAN_TTL_HOURS)
            result["scan_expires_at"] = expires_at.isoformat()

    elif status == "FAILED":
        result["completed_at"]  = item.get("completed_at")
        result["error_message"] = item.get("error_message", "Scan failed.")

    elif status == "DONE":
        result["vuln_count"]   = item.get("vuln_count", 0)
        result["completed_at"] = item.get("completed_at")

        s3_key = item.get("s3_report_key")
        if s3_key:
            result["report_url"] = _generate_presigned_url(s3_bucket, s3_key)
            # Tell the client exactly when this URL stops working so it can
            # warn the user before they get a silent 403 from S3.
            result["report_url_expires_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_EXPIRY)
            ).isoformat()

    return result


def _resolve_in_progress_status(item: dict) -> str:
    """
    Return "FAILED" if the IN_PROGRESS scan is older than _STALE_IN_PROGRESS_MINUTES,
    otherwise return "IN_PROGRESS" unchanged.
    """
    created_at_str = item.get("created_at", "")
    if not created_at_str:
        return "IN_PROGRESS"

    try:
        # Handle both "+00:00" (from datetime.now(timezone.utc).isoformat()) and
        # trailing "Z" (from the legacy datetime.utcnow().isoformat() + "Z").
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - created_at
        if age > timedelta(minutes=_STALE_IN_PROGRESS_MINUTES):
            logger.warning(
                "Scan %s has been IN_PROGRESS for %s — treating as stale FAILED",
                item.get("scan_id"), age,
            )
            return "FAILED"
    except (ValueError, TypeError):
        # Unparseable created_at — do not override status
        logger.warning("Could not parse created_at=%r for scan_id=%s", created_at_str, item.get("scan_id"))

    return "IN_PROGRESS"


def _generate_presigned_url(bucket: str, key: str) -> str:
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PRESIGNED_URL_EXPIRY,
    )
    logger.info("Presigned URL generated for key=%s", key)
    return url
