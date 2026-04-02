"""
status.py — Lambda A
Jingsi Zhang | CS6620 Group 9

Handles GET /status?scan_id=xxx
Queries DynamoDB and, when the scan is DONE, returns an S3 presigned URL
so the frontend can fetch the report directly.
"""

import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
s3       = boto3.client("s3")

PRESIGNED_URL_EXPIRY = 3600  # seconds (1 hour)


def get_scan_status(scan_id: str, student_id: str, table_name: str, s3_bucket: str) -> dict:
    """
    Look up a scan record using the table primary key (student_id + scan_id).

    Using the primary key instead of the GSI enforces ownership at the database
    level: a student can only retrieve their own scan.  If the (student_id,
    scan_id) pair does not exist — whether because the scan doesn't exist or
    belongs to a different student — a ValueError is raised with the same
    message, preventing cross-tenant enumeration.

    Returns a dict with:
      - status: PENDING | IN_PROGRESS | DONE | FAILED
      - scan_id
      - language
      - created_at
      - (when DONE) vuln_count, completed_at, report_url (presigned S3 URL)

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

    result = {
        "scan_id":    item["scan_id"],
        "status":     item["status"],
        "language":   item.get("language"),
        "created_at": item.get("created_at"),
    }

    if item["status"] == "DONE":
        result["vuln_count"]   = item.get("vuln_count", 0)
        result["completed_at"] = item.get("completed_at")

        s3_key = item.get("s3_report_key")
        if s3_key:
            result["report_url"] = _generate_presigned_url(s3_bucket, s3_key)

    return result


def _generate_presigned_url(bucket: str, key: str) -> str:
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PRESIGNED_URL_EXPIRY,
    )
    logger.info("Presigned URL generated for key=%s", key)
    return url
