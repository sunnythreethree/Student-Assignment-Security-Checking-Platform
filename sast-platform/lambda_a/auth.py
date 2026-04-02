"""
auth.py — Lambda A
Jingsi Zhang | CS6620 Group 9

API-key authentication for the simplified course-project auth model.
Resolves an X-Student-Key header value to a student_id via the StudentAuth
DynamoDB table (PK: api_key, attribute: student_id).
"""

import logging

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")


def lookup_student(api_key: str, table_name: str) -> str | None:
    """
    Look up the student_id associated with api_key.

    Returns:
        student_id (str)  if the key exists in the auth table
        None              if the key is not found
    """
    table = dynamodb.Table(table_name)
    resp  = table.get_item(Key={"api_key": api_key})
    item  = resp.get("Item")
    if not item:
        logger.warning("Unknown api_key presented (last 4: ...%s)", api_key[-4:])
        return None
    return item["student_id"]
