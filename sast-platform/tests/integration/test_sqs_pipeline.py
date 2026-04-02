"""
test_sqs_pipeline.py — Integration tests for Lambda A dispatch pipeline
CS6620 Group 9

Tests the Lambda A → SQS → DynamoDB pipeline using moto (no real AWS needed).
Verifies that create_scan_job():
  1. Writes a PENDING record to DynamoDB with all required fields
  2. Uploads code to S3 (S3-staging, PR #48)
  3. Enqueues an SQS message containing s3_code_key (not raw code)

Run with:
    pytest tests/integration/test_sqs_pipeline.py -v
"""

import json
import unittest.mock as mock

import boto3
import pytest
from moto import mock_aws

import dispatcher

REGION     = "us-east-1"
TABLE_NAME = "ScanResults"
QUEUE_NAME = "sast-scan-queue"
BUCKET     = "sast-test-bucket"


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION",    REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID",     "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN",     "testing")


@pytest.fixture
def pipeline(aws_credentials):
    """
    Spin up moto-backed SQS queue + DynamoDB table + S3 bucket and patch
    dispatcher's module-level boto3 clients to use them.

    S3 must be included because create_scan_job() uploads code to S3 before
    writing to DynamoDB or SQS (S3-staging migration, PR #48).
    """
    with mock_aws():
        sqs_client   = boto3.client("sqs",       region_name=REGION)
        ddb_resource = boto3.resource("dynamodb", region_name=REGION)
        s3_client    = boto3.client("s3",         region_name=REGION)

        queue_url = sqs_client.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
        s3_client.create_bucket(Bucket=BUCKET)

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

        with mock.patch.object(dispatcher, "sqs",      sqs_client), \
             mock.patch.object(dispatcher, "dynamodb", ddb_resource), \
             mock.patch.object(dispatcher, "s3",       s3_client):
            yield {
                "queue_url":  queue_url,
                "sqs_client": sqs_client,
                "s3_client":  s3_client,
                "table":      table,
            }


def _dispatch(pipeline, **kwargs):
    defaults = dict(
        code="import os\nos.system('ls')",
        language="python",
        student_id="neu-test-001",
        sqs_url=pipeline["queue_url"],
        table_name=TABLE_NAME,
        s3_bucket=BUCKET,
    )
    defaults.update(kwargs)
    return dispatcher.create_scan_job(**defaults)


def _read_dynamo(pipeline, student_id, scan_id):
    return pipeline["table"].get_item(
        Key={"student_id": student_id, "scan_id": scan_id}
    ).get("Item")


def _receive_sqs(pipeline):
    msgs = pipeline["sqs_client"].receive_message(
        QueueUrl=pipeline["queue_url"],
        MaxNumberOfMessages=1,
    ).get("Messages", [])
    return json.loads(msgs[0]["Body"]) if msgs else None


# ── DynamoDB record tests ──────────────────────────────────────────────────────

class TestDynamoDBPipeline:

    def test_pending_record_created(self, pipeline):
        scan_id = _dispatch(pipeline, student_id="s-db-1")
        item = _read_dynamo(pipeline, "s-db-1", scan_id)
        assert item is not None

    def test_initial_status_is_pending(self, pipeline):
        scan_id = _dispatch(pipeline, student_id="s-db-2")
        item = _read_dynamo(pipeline, "s-db-2", scan_id)
        assert item["status"] == "PENDING"

    def test_language_stored_in_record(self, pipeline):
        scan_id = _dispatch(pipeline, student_id="s-db-3", language="java")
        item = _read_dynamo(pipeline, "s-db-3", scan_id)
        assert item["language"] == "java"

    def test_created_at_is_iso_timestamp(self, pipeline):
        scan_id = _dispatch(pipeline, student_id="s-db-4")
        item = _read_dynamo(pipeline, "s-db-4", scan_id)
        assert "T" in item.get("created_at", "")

    def test_scan_id_matches_return_value(self, pipeline):
        scan_id = _dispatch(pipeline, student_id="s-db-5")
        item = _read_dynamo(pipeline, "s-db-5", scan_id)
        assert item["scan_id"] == scan_id

    def test_multiple_scans_same_student(self, pipeline):
        id1 = _dispatch(pipeline, student_id="s-multi")
        id2 = _dispatch(pipeline, student_id="s-multi")
        assert id1 != id2
        assert _read_dynamo(pipeline, "s-multi", id1) is not None
        assert _read_dynamo(pipeline, "s-multi", id2) is not None

    def test_expires_at_set_in_record(self, pipeline):
        """TTL field must be present so stuck PENDING records auto-expire."""
        scan_id = _dispatch(pipeline, student_id="s-db-6")
        item = _read_dynamo(pipeline, "s-db-6", scan_id)
        assert "expires_at" in item
        assert int(item["expires_at"]) > 0  # DynamoDB returns Decimal, not int


# ── SQS message tests ──────────────────────────────────────────────────────────

class TestSQSPipeline:

    def test_message_enqueued(self, pipeline):
        _dispatch(pipeline)
        assert _receive_sqs(pipeline) is not None

    def test_message_has_scan_id(self, pipeline):
        scan_id = _dispatch(pipeline)
        assert _receive_sqs(pipeline)["scan_id"] == scan_id

    def test_message_has_student_id(self, pipeline):
        _dispatch(pipeline, student_id="s-sqs-1")
        assert _receive_sqs(pipeline)["student_id"] == "s-sqs-1"

    def test_message_has_language(self, pipeline):
        _dispatch(pipeline, language="javascript")
        assert _receive_sqs(pipeline)["language"] == "javascript"

    def test_message_has_s3_code_key(self, pipeline):
        """After S3-staging migration, SQS message carries s3_code_key, not raw code."""
        _dispatch(pipeline, code="console.log(1)")
        msg = _receive_sqs(pipeline)
        assert "s3_code_key" in msg
        assert msg["s3_code_key"].startswith("uploads/")
        assert "code" not in msg

    def test_message_body_is_valid_json(self, pipeline):
        _dispatch(pipeline)
        raw = pipeline["sqs_client"].receive_message(
            QueueUrl=pipeline["queue_url"], MaxNumberOfMessages=1
        )["Messages"][0]["Body"]
        assert isinstance(json.loads(raw), dict)


# ── S3 upload tests ────────────────────────────────────────────────────────────

class TestS3Staging:

    def test_code_uploaded_to_s3(self, pipeline):
        """Code must be uploaded to S3 before the SQS message is sent."""
        scan_id = _dispatch(pipeline, code="x = 1")
        msg = _receive_sqs(pipeline)
        s3_key = msg["s3_code_key"]
        obj = pipeline["s3_client"].get_object(Bucket=BUCKET, Key=s3_key)
        assert obj["Body"].read().decode("utf-8") == "x = 1"

    def test_s3_key_matches_scan_id(self, pipeline):
        scan_id = _dispatch(pipeline)
        msg = _receive_sqs(pipeline)
        assert scan_id in msg["s3_code_key"]


# ── End-to-end pipeline consistency ───────────────────────────────────────────

class TestPipelineConsistency:

    def test_scan_id_consistent_across_dynamo_and_sqs(self, pipeline):
        """scan_id returned, stored in DynamoDB, and sent to SQS must all match."""
        returned_id = _dispatch(pipeline, student_id="s-consistency")
        msg  = _receive_sqs(pipeline)
        item = _read_dynamo(pipeline, "s-consistency", returned_id)
        assert returned_id == msg["scan_id"] == item["scan_id"]

    def test_student_id_consistent_across_dynamo_and_sqs(self, pipeline):
        returned_id = _dispatch(pipeline, student_id="s-cross-check")
        msg  = _receive_sqs(pipeline)
        item = _read_dynamo(pipeline, "s-cross-check", returned_id)
        assert msg["student_id"] == item["student_id"] == "s-cross-check"

    def test_s3_key_consistent_across_sqs_and_s3(self, pipeline):
        """s3_code_key in SQS message must point to an actual S3 object."""
        _dispatch(pipeline, student_id="s-s3-check")
        msg = _receive_sqs(pipeline)
        s3_key = msg["s3_code_key"]
        obj = pipeline["s3_client"].get_object(Bucket=BUCKET, Key=s3_key)
        assert obj["Body"].read()  # object exists and has content
