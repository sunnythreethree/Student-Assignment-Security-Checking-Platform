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

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sqs      = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")


def create_scan_job(code: str, language: str, student_id: str,
                    sqs_url: str, table_name: str) -> str:
    """
    1. Generate a unique scan_id.
    2. Write PENDING record to DynamoDB.
    3. Send scan job message to SQS.

    Returns:
        scan_id (str)

    Raises:
        Exception if either AWS call fails.
    """
    scan_id   = f"scan-{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(timezone.utc).isoformat()

    # --- Write to DynamoDB (status: PENDING) ---
    table = dynamodb.Table(table_name)
    table.put_item(Item={
        "student_id":  student_id,
        "scan_id":     scan_id,
        "status":      "PENDING",
        "language":    language,
        "created_at":  timestamp,
    })
    logger.info("DynamoDB record created: scan_id=%s student_id=%s", scan_id, student_id)

    # --- Send to SQS ---
    message = {
        "scan_id":    scan_id,
        "student_id": student_id,
        "language":   language,
        "code":       code,
    }
    sqs.send_message(
        QueueUrl=sqs_url,
        MessageBody=json.dumps(message),
    )
    logger.info("SQS message sent: scan_id=%s queue=%s", scan_id, sqs_url)

    return scan_id
