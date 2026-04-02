"""
dispatcher.py — Lambda A
Jingsi Zhang | CS6620 Group 9

Writes the scan job to SQS and creates the initial DynamoDB record.
Called after validation passes.
"""

import json
import uuid
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sqs      = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")
s3       = boto3.client("s3")


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
    timestamp   = datetime.now(timezone.utc).isoformat()
    s3_code_key = f"uploads/{scan_id}.txt"

    # --- Upload code to S3 (avoids SQS 256KB message size limit) ---
    s3.put_object(
        Bucket=s3_bucket,
        Key=s3_code_key,
        Body=code.encode("utf-8"),
        ContentType="text/plain",
    )
    logger.info("Code uploaded to S3: key=%s bucket=%s", s3_code_key, s3_bucket)

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
        raise

    return scan_id
