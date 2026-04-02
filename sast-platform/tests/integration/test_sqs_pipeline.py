"""
test_sqs_pipeline.py — Integration tests for Lambda A dispatch pipeline
CS6620 Group 9

Tests the Lambda A → SQS → DynamoDB pipeline using moto (no real AWS needed).
Verifies that create_scan_job():
  1. Writes a PENDING record to DynamoDB with all required fields
  2. Enqueues an SQS message containing the correct scan payload

Run with:
    pytest tests/integration/test_sqs_pipeline.py -v
"""

import sys
import os
import json
import unittest.mock as mock

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda_a"))

import dispatcher

REGION     = "us-east-1"
TABLE_NAME = "ScanResults"
QUEUE_NAME = "sast-scan-queue"


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
    Spin up a moto-backed SQS queue + DynamoDB table and patch dispatcher's
    module-level boto3 clients to use them.
    """
    with mock_aws():
        sqs_client   = boto3.client("sqs",       region_name=REGION)
        ddb_resource = boto3.resource("dynamodb", region_name=REGION)

        queue_url = sqs_client.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]

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
             mock.patch.object(dispatcher, "dynamodb", ddb_resource):
            yield {
                "queue_url":  queue_url,
                "sqs_client": sqs_client,
                "table":      table,
            }


def _dispatch(pipeline, **kwargs):
    defaults = dict(
        code="import os\nos.system('ls')",
        language="python",
        student_id="neu-test-001",
        sqs_url=pipeline["queue_url"],
        table_name=TABLE_NAME,
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

    def test_message_has_code(self, pipeline):
        _dispatch(pipeline, code="console.log(1)")
        assert _receive_sqs(pipeline)["code"] == "console.log(1)"

    def test_message_body_is_valid_json(self, pipeline):
        _dispatch(pipeline)
        raw = pipeline["sqs_client"].receive_message(
            QueueUrl=pipeline["queue_url"], MaxNumberOfMessages=1
        )["Messages"][0]["Body"]
        assert isinstance(json.loads(raw), dict)


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
