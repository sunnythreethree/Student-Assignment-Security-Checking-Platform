"""
dispatcher.py — Lambda A
Jingsi Zhang | CS6620 Group 9

Writes the scan job to SQS and creates the initial DynamoDB record.
Called after validation passes.
"""

import json
import time
import uuid
import logging
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sqs      = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")
s3       = boto3.client("s3")

RATE_LIMIT_PER_HOUR = 10


def check_rate_limit(student_id: str, table_name: str,
                     limit: int = RATE_LIMIT_PER_HOUR) -> bool:
    """
    Check and atomically increment the per-student hourly submission counter.

    Uses a synthetic record in the ScanResults table:
      student_id = <student_id>
      scan_id    = "rate#<hour>"   (never conflicts with real "scan-..." IDs)

    The record carries a TTL so DynamoDB cleans it up automatically after 2 hours.

    Returns:
        True  — student is within the limit, request may proceed.
        False — limit exceeded, caller should return 429.
    """
    hour         = int(time.time() // 3600)
    rate_scan_id = f"rate#{hour}"
    expires_at   = int(time.time()) + 7200  # expire 2 hours after the window opens

    table    = dynamodb.Table(table_name)
    response = table.update_item(
        Key={
            "student_id": student_id,
            "scan_id":    rate_scan_id,
        },
        UpdateExpression="ADD submission_count :one SET expires_at = if_not_exists(expires_at, :exp)",
        ExpressionAttributeValues={":one": 1, ":exp": expires_at},
        ReturnValues="UPDATED_NEW",
    )
    count = int(response["Attributes"]["submission_count"])
    logger.info("Rate-limit check: student_id=%s hour=%s count=%d limit=%d",
                student_id, hour, count, limit)
    return count <= limit


def create_scan_job(code: str, language: str, student_id: str,
                    sqs_url: str, table_name: str, s3_bucket: str) -> str:
    """
    1. Generate a unique scan_id.
    2. Upload code to S3 (uploads/{scan_id}.txt) to avoid SQS 256KB limit.
    3. Write PENDING record to DynamoDB.
    4. Send scan job message to SQS (carrying S3 key, not raw code).

    Returns:
        scan_id (str)

    Raises:
        Exception if any AWS call fails.
    """
    scan_id     = f"scan-{uuid.uuid4().hex[:8]}"
    now         = datetime.now(timezone.utc)
    timestamp   = now.isoformat()
    # TTL: auto-expire records 24 h from creation so stuck PENDING scans don't
    # accumulate. DynamoDB TTL expects a Unix epoch integer (seconds).
    expires_at  = int((now + timedelta(hours=24)).timestamp())
    s3_code_key = f"uploads/{scan_id}.txt"

    # --- Upload code to S3 (avoids SQS 256KB message size limit) ---
    s3.put_object(
        Bucket=s3_bucket,
        Key=s3_code_key,
        Body=code.encode("utf-8"),
        ContentType="text/plain",
    )
    logger.info("Code uploaded to S3: key=%s bucket=%s", s3_code_key, s3_bucket)

    db_written = False  # track whether DynamoDB write succeeded
    try:
        # --- Write to DynamoDB (status: PENDING) ---
        # ConditionExpression prevents overwriting an existing record if scan_id
        # is somehow reused (defensive guard against idempotency violations).
        table = dynamodb.Table(table_name)
        try:
            table.put_item(
                Item={
                    "student_id":  student_id,
                    "scan_id":     scan_id,
                    "status":      "PENDING",
                    "language":    language,
                    "created_at":  timestamp,
                    "expires_at":  expires_at,
                },
                ConditionExpression="attribute_not_exists(scan_id)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.warning("scan_id %s already exists — skipping duplicate write", scan_id)
                # Clean up the S3 upload we just created — it won't be processed.
                try:
                    s3.delete_object(Bucket=s3_bucket, Key=s3_code_key)
                except Exception:
                    logger.warning("Could not clean up S3 for duplicate scan_id=%s", scan_id)
                return scan_id
            raise
        db_written = True
        logger.info("DynamoDB record created: scan_id=%s student_id=%s", scan_id, student_id)

        # --- Send to SQS (S3 key only — no raw code to stay within 256KB limit) ---
        message = {
            "scan_id":     scan_id,
            "student_id":  student_id,
            "language":    language,
            "s3_code_key": s3_code_key,
        }
        sqs.send_message(
            QueueUrl=sqs_url,
            MessageBody=json.dumps(message),
        )
        logger.info("SQS message sent: scan_id=%s queue=%s", scan_id, sqs_url)
    except Exception:
        # DynamoDB or SQS failed — Lambda B will never receive the message, so
        # _delete_uploaded_code in handler.py will never run.  Clean up the
        # S3 object here to prevent it from being orphaned.
        try:
            s3.delete_object(Bucket=s3_bucket, Key=s3_code_key)
            logger.warning("S3 upload cleaned up after DynamoDB/SQS failure: key=%s", s3_code_key)
        except Exception:
            logger.exception("S3 cleanup also failed: key=%s", s3_code_key)

        # If DynamoDB was written but SQS failed, mark the record FAILED so
        # the student gets a clear error instead of a forever-PENDING scan.
        if db_written:
            try:
                table.update_item(
                    Key={"student_id": student_id, "scan_id": scan_id},
                    UpdateExpression="SET #status = :failed, completed_at = :now",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":failed": "FAILED",
                        ":now": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.warning("DynamoDB record marked FAILED after SQS error: scan_id=%s", scan_id)
            except Exception:
                logger.exception("DynamoDB FAILED update also failed: scan_id=%s", scan_id)

        raise

    return scan_id
