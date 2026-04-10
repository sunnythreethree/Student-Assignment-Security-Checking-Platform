"""
history.py — Lambda A

Handles GET /history?student_id=xxx
Returns the last 50 scans for a student, newest first.

DynamoDB layout:
  PK: student_id (HASH)   SK: scan_id (RANGE)
A single Query on the base table returns all scans for a student without
needing a GSI — student_id is already the partition key.
"""

import logging
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

MAX_HISTORY_ITEMS = int(os.environ.get("MAX_HISTORY_ITEMS", "50"))


def get_scan_history(student_id: str, table_name: str) -> list:
    """
    Return up to MAX_HISTORY_ITEMS scans for student_id, sorted newest first.

    Args:
        student_id: Student identifier (DynamoDB partition key).
        table_name: DynamoDB table name.

    Returns:
        List of scan summary dicts.

    Raises:
        ClientError if the DynamoDB call fails.
    """
    table = dynamodb.Table(table_name)

    response = table.query(
        KeyConditionExpression=Key("student_id").eq(student_id),
        Limit=MAX_HISTORY_ITEMS,
    )
    # Filter out synthetic rate-limit records (scan_id starts with "rate#")
    # before formatting — they have no "status"/"language"/"created_at" fields
    # and cause a KeyError in _format_item().
    items = [
        i for i in response.get("Items", [])
        if not i.get("scan_id", "").startswith("rate#")
    ]

    # scan_id is not time-ordered, so sort by created_at in Python
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    logger.info(
        "Fetched %d history items for student_id=%s", len(items), student_id
    )
    return [_format_item(item) for item in items]


def _format_item(item: dict) -> dict:
    """Reduce a raw DynamoDB item to the fields exposed in the API response."""
    result = {
        "scan_id":    item["scan_id"],
        "status":     item["status"],
        "language":   item.get("language"),
        "created_at": item.get("created_at"),
    }
    if item.get("status") == "DONE":
        result["vuln_count"]   = item.get("vuln_count", 0)
        result["completed_at"] = item.get("completed_at")
    return result
