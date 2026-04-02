"""
test_dispatcher.py — Unit tests for lambda_a/dispatcher.py
Jingsi Zhang | CS6620 Group 9

Uses moto to mock AWS (SQS + DynamoDB) without real credentials.

Run with:
    pytest tests/unit/test_dispatcher.py -v
"""

import json
import unittest.mock as mock

import boto3
import pytest
from moto import mock_aws

import dispatcher


# ── Constants ──────────────────────────────────────────────────────────────────

REGION      = "us-east-1"
TABLE_NAME  = "ScanResults"
QUEUE_NAME  = "test-scan-queue"
BUCKET_NAME = "sast-uploads-test"


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    """Prevent any accidental real AWS calls."""
    monkeypatch.setenv("AWS_DEFAULT_REGION",        REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID",         "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY",     "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN",        "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN",         "testing")


@pytest.fixture
def aws_env():
    """
    Spin up moto-backed S3 bucket, SQS queue, and DynamoDB table, then patch
    the module-level boto3 clients inside dispatcher.py so they use the
    moto-backed resources.  S3 is required because PR #48 moved raw code out
    of the SQS message into S3 to bypass the 256 KB limit.
    """
    with mock_aws():
        s3_client    = boto3.client("s3",         region_name=REGION)
        sqs_client   = boto3.client("sqs",        region_name=REGION)
        ddb_resource = boto3.resource("dynamodb", region_name=REGION)

        # Create S3 bucket
        s3_client.create_bucket(Bucket=BUCKET_NAME)

        # Create SQS queue
        resp      = sqs_client.create_queue(QueueName=QUEUE_NAME)
        queue_url = resp["QueueUrl"]

        # Create DynamoDB table (mirrors dynamodb.yaml)
        table = ddb_resource.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "student_id", "KeyType": "HASH"},
                {"AttributeName": "scan_id",    "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "student_id", "AttributeType": "S"},
                {"AttributeName": "scan_id",    "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)

        # Patch module-level clients so dispatcher uses the moto-backed ones
        with mock.patch.object(dispatcher, "s3",       s3_client), \
             mock.patch.object(dispatcher, "sqs",      sqs_client), \
             mock.patch.object(dispatcher, "dynamodb", ddb_resource):
            yield {
                "queue_url":   queue_url,
                "sqs_client":  sqs_client,
                "s3_client":   s3_client,
                "bucket_name": BUCKET_NAME,
                "table":       table,
            }


# ── Helper ─────────────────────────────────────────────────────────────────────

def _call_create(aws_env, code="print(1)", language="python", student_id="neu123"):
    return dispatcher.create_scan_job(
        code=code,
        language=language,
        student_id=student_id,
        sqs_url=aws_env["queue_url"],
        table_name=TABLE_NAME,
        s3_bucket=BUCKET_NAME,
    )


# ── Return value ───────────────────────────────────────────────────────────────

class TestCreateScanJobReturnValue:

    def test_returns_string(self, aws_env):
        scan_id = _call_create(aws_env)
        assert isinstance(scan_id, str)

    def test_scan_id_has_expected_prefix(self, aws_env):
        scan_id = _call_create(aws_env)
        assert scan_id.startswith("scan-")

    def test_scan_id_is_unique_per_call(self, aws_env):
        id1 = _call_create(aws_env)
        id2 = _call_create(aws_env)
        assert id1 != id2


# ── DynamoDB record ────────────────────────────────────────────────────────────

class TestDynamoDBRecord:

    def test_record_is_created(self, aws_env):
        scan_id = _call_create(aws_env, student_id="neu-db-test")
        resp = aws_env["table"].get_item(
            Key={"student_id": "neu-db-test", "scan_id": scan_id}
        )
        assert "Item" in resp

    def test_status_is_pending(self, aws_env):
        scan_id = _call_create(aws_env, student_id="neu-status")
        item = aws_env["table"].get_item(
            Key={"student_id": "neu-status", "scan_id": scan_id}
        )["Item"]
        assert item["status"] == "PENDING"

    def test_language_stored_correctly(self, aws_env):
        scan_id = _call_create(aws_env, student_id="neu-lang", language="java")
        item = aws_env["table"].get_item(
            Key={"student_id": "neu-lang", "scan_id": scan_id}
        )["Item"]
        assert item["language"] == "java"

    def test_student_id_stored_correctly(self, aws_env):
        scan_id = _call_create(aws_env, student_id="s-unique-99")
        item = aws_env["table"].get_item(
            Key={"student_id": "s-unique-99", "scan_id": scan_id}
        )["Item"]
        assert item["student_id"] == "s-unique-99"

    def test_created_at_is_present(self, aws_env):
        scan_id = _call_create(aws_env, student_id="neu-ts")
        item = aws_env["table"].get_item(
            Key={"student_id": "neu-ts", "scan_id": scan_id}
        )["Item"]
        assert "created_at" in item
        assert item["created_at"]  # non-empty

    def test_scan_id_matches_return_value(self, aws_env):
        scan_id = _call_create(aws_env, student_id="neu-match")
        item = aws_env["table"].get_item(
            Key={"student_id": "neu-match", "scan_id": scan_id}
        )["Item"]
        assert item["scan_id"] == scan_id


# ── SQS message ────────────────────────────────────────────────────────────────

class TestSQSMessage:

    def _receive_one(self, aws_env):
        msgs = aws_env["sqs_client"].receive_message(
            QueueUrl=aws_env["queue_url"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        ).get("Messages", [])
        assert msgs, "Expected one SQS message but queue was empty"
        return json.loads(msgs[0]["Body"])

    def test_message_is_sent(self, aws_env):
        _call_create(aws_env)
        msgs = aws_env["sqs_client"].receive_message(
            QueueUrl=aws_env["queue_url"],
            MaxNumberOfMessages=1,
        ).get("Messages", [])
        assert len(msgs) == 1

    def test_message_contains_scan_id(self, aws_env):
        scan_id = _call_create(aws_env)
        body = self._receive_one(aws_env)
        assert body["scan_id"] == scan_id

    def test_message_contains_student_id(self, aws_env):
        _call_create(aws_env, student_id="neu-sqs")
        body = self._receive_one(aws_env)
        assert body["student_id"] == "neu-sqs"

    def test_message_contains_language(self, aws_env):
        _call_create(aws_env, language="javascript")
        body = self._receive_one(aws_env)
        assert body["language"] == "javascript"

    def test_message_contains_s3_code_key(self, aws_env):
        """PR #48: raw code is staged to S3; SQS carries s3_code_key, not code."""
        scan_id = _call_create(aws_env, code="x = 42")
        body = self._receive_one(aws_env)
        assert body["s3_code_key"] == f"uploads/{scan_id}.txt"

    def test_message_body_is_valid_json(self, aws_env):
        _call_create(aws_env)
        msgs = aws_env["sqs_client"].receive_message(
            QueueUrl=aws_env["queue_url"],
            MaxNumberOfMessages=1,
        ).get("Messages", [])
        # json.loads will raise if not valid JSON
        parsed = json.loads(msgs[0]["Body"])
        assert isinstance(parsed, dict)


# ── Failure cases ──────────────────────────────────────────────────────────────

class TestCreateScanJobFailures:

    def test_dynamodb_error_raises_exception(self, aws_env):
        """If DynamoDB put_item fails, create_scan_job should propagate the exception."""
        mock_table = mock.MagicMock()
        mock_table.put_item.side_effect = Exception("DynamoDB unavailable")
        with mock.patch.object(dispatcher.dynamodb, "Table", return_value=mock_table):
            with pytest.raises(Exception, match="DynamoDB unavailable"):
                _call_create(aws_env)

    def test_sqs_error_raises_exception(self, aws_env):
        """If SQS send_message fails, create_scan_job should propagate the exception."""
        with mock.patch.object(
            dispatcher.sqs, "send_message",
            side_effect=Exception("SQS unavailable")
        ):
            with pytest.raises(Exception, match="SQS unavailable"):
                _call_create(aws_env)
