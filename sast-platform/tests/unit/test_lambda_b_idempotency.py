"""
test_lambda_b_idempotency.py — Unit tests for the IN_PROGRESS claim guard
Jingsi Zhang | CS6620 Group 9

Covers the atomic PENDING → IN_PROGRESS transition added in issue #66.
Uses moto to mock DynamoDB; scanner / S3 / SQS calls are patched out.
"""

import os
import shutil
import sys
import types
import unittest.mock as mock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

REGION     = "us-east-1"
TABLE_NAME = "ScanResults"
BUCKET     = "sast-reports-test"

# handler.py has three module-level guards that would abort import in a test env:
#   1. shutil.which check for bandit/semgrep binaries
#   2. s3_writer imports (S3WriteError etc.)
#   3. env var validation (DYNAMODB_TABLE_NAME, S3_BUCKET_NAME) — added in #22
# Stub / satisfy all three before importing so collection succeeds.
_s3w = types.ModuleType("s3_writer")
_s3w.write_scan_result_to_s3 = mock.MagicMock()
_s3w.get_s3_bucket_from_env  = mock.MagicMock()
_s3w.S3WriteError             = type("S3WriteError", (Exception,), {})
sys.modules["s3_writer"] = _s3w

os.environ.setdefault("DYNAMODB_TABLE_NAME", TABLE_NAME)
os.environ.setdefault("S3_BUCKET_NAME", BUCKET)

with mock.patch("shutil.which", return_value="/usr/bin/fake"):
    import handler as lambda_b_handler


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION",    REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID",     "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN",    "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN",     "testing")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME",   TABLE_NAME)
    monkeypatch.setenv("S3_BUCKET_NAME",        BUCKET)


@pytest.fixture
def ddb_table():
    """Spin up a moto-backed DynamoDB table and yield it."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        table = ddb.create_table(
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
        yield table


def _seed(table, student_id, scan_id, status):
    table.put_item(Item={
        "student_id": student_id,
        "scan_id":    scan_id,
        "status":     status,
        "language":   "python",
        "created_at": "2026-01-01T00:00:00+00:00",
    })


# ── Claim succeeds: PENDING → IN_PROGRESS ────────────────────────────────────

class TestClaimSucceeds:

    def test_pending_record_is_flipped_to_in_progress(self, ddb_table):
        _seed(ddb_table, "neu1", "scan-aaa", "PENDING")

        # Patch everything after the claim so the test stays focused
        with mock.patch.object(lambda_b_handler, "_fetch_code_from_s3", return_value="x=1"), \
             mock.patch("handler.scan_code_with_timeout", return_value={"tool": "bandit", "findings": [], "raw_output": {}}), \
             mock.patch("handler.normalize_result", return_value={"vuln_count": 0}), \
             mock.patch("handler.write_scan_result_to_s3", return_value=("reports/key.json", "https://presigned")), \
             mock.patch("handler.update_scan_status"), \
             mock.patch.object(lambda_b_handler, "_delete_uploaded_code"):
            lambda_b_handler.process_scan_request(
                scan_id="scan-aaa", language="python", student_id="neu1",
                table=ddb_table, s3_bucket_name=BUCKET, s3_code_key="uploads/scan-aaa.txt",
            )

        item = ddb_table.get_item(Key={"student_id": "neu1", "scan_id": "scan-aaa"})["Item"]
        # After the claim the status is IN_PROGRESS (update_scan_status is mocked,
        # so the final DONE write doesn't happen — IN_PROGRESS persists).
        assert item["status"] == "IN_PROGRESS"


# ── Duplicate skipped: already IN_PROGRESS ───────────────────────────────────

class TestDuplicateSkipped:

    def test_in_progress_returns_skipped(self, ddb_table):
        """Second invocation finds status=IN_PROGRESS → claim fails → skip."""
        _seed(ddb_table, "neu2", "scan-bbb", "IN_PROGRESS")

        result = lambda_b_handler.process_scan_request(
            scan_id="scan-bbb", language="python", student_id="neu2",
            table=ddb_table, s3_bucket_name=BUCKET, s3_code_key="uploads/scan-bbb.txt",
        )

        assert result["success"] is True
        assert result.get("skipped") is True

    def test_done_returns_skipped(self, ddb_table):
        """Duplicate after scan already completed → claim fails → skip."""
        _seed(ddb_table, "neu3", "scan-ccc", "DONE")

        result = lambda_b_handler.process_scan_request(
            scan_id="scan-ccc", language="python", student_id="neu3",
            table=ddb_table, s3_bucket_name=BUCKET, s3_code_key="uploads/scan-ccc.txt",
        )

        assert result["success"] is True
        assert result.get("skipped") is True

    def test_failed_returns_skipped(self, ddb_table):
        """Duplicate after scan failed → claim fails → skip."""
        _seed(ddb_table, "neu4", "scan-ddd", "FAILED")

        result = lambda_b_handler.process_scan_request(
            scan_id="scan-ddd", language="python", student_id="neu4",
            table=ddb_table, s3_bucket_name=BUCKET, s3_code_key="uploads/scan-ddd.txt",
        )

        assert result["success"] is True
        assert result.get("skipped") is True

    def test_skipped_invocation_does_not_run_scanner(self, ddb_table):
        """No scan work should be done when the invocation is skipped."""
        _seed(ddb_table, "neu5", "scan-eee", "IN_PROGRESS")

        with mock.patch("handler.scan_code_with_timeout") as mock_scan:
            lambda_b_handler.process_scan_request(
                scan_id="scan-eee", language="python", student_id="neu5",
                table=ddb_table, s3_bucket_name=BUCKET, s3_code_key="uploads/scan-eee.txt",
            )

        mock_scan.assert_not_called()


# ── lambda_handler batchItemFailures format ───────────────────────────────────

def _make_sqs_event(*message_ids):
    """Build a minimal SQS event with the given messageIds."""
    return {
        "Records": [
            {
                "messageId": mid,
                "body": '{"scan_id":"scan-x","s3_code_key":"uploads/x.txt","language":"python","student_id":"neu9"}',
            }
            for mid in message_ids
        ]
    }


class TestLambdaHandlerBatchFailures:

    def test_all_succeed_returns_empty_batch_failures(self):
        """When every record succeeds, batchItemFailures must be empty."""
        event = _make_sqs_event("msg-1", "msg-2")
        with mock.patch.object(lambda_b_handler, "process_scan_request",
                               return_value={"success": True}):
            resp = lambda_b_handler.lambda_handler(event, None)
        assert resp == {"batchItemFailures": []}

    def test_failed_record_appears_in_batch_failures(self):
        """A record whose process_scan_request returns success=False must be retried."""
        event = _make_sqs_event("msg-fail", "msg-ok")

        # First call (msg-fail) → failure; second call (msg-ok) → success.
        results = [{"success": False, "error": "oops"}, {"success": True}]
        with mock.patch.object(lambda_b_handler, "process_scan_request",
                               side_effect=results):
            resp = lambda_b_handler.lambda_handler(event, None)

        assert {"itemIdentifier": "msg-fail"} in resp["batchItemFailures"]
        assert {"itemIdentifier": "msg-ok"} not in resp["batchItemFailures"]

    def test_exception_in_record_adds_to_batch_failures(self):
        """An unhandled exception for one record adds it to batchItemFailures."""
        event = _make_sqs_event("msg-exc")
        with mock.patch.object(lambda_b_handler, "process_scan_request",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(lambda_b_handler, "_delete_uploaded_code"), \
             mock.patch.object(lambda_b_handler, "update_scan_status"):
            resp = lambda_b_handler.lambda_handler(event, None)
        assert resp["batchItemFailures"] == [{"itemIdentifier": "msg-exc"}]

    def test_setup_failure_marks_all_records_failed(self):
        """If env var setup fails, every record in the batch must be retried."""
        event = _make_sqs_event("msg-a", "msg-b", "msg-c")
        with mock.patch.object(lambda_b_handler, "get_s3_bucket_from_env",
                               side_effect=ValueError("S3_BUCKET_NAME is not set")):
            resp = lambda_b_handler.lambda_handler(event, None)
        ids = {f["itemIdentifier"] for f in resp["batchItemFailures"]}
        assert ids == {"msg-a", "msg-b", "msg-c"}

    def test_response_has_only_batch_item_failures_key(self):
        """Response must not include the old statusCode/body keys."""
        event = _make_sqs_event("msg-1")
        with mock.patch.object(lambda_b_handler, "process_scan_request",
                               return_value={"success": True}):
            resp = lambda_b_handler.lambda_handler(event, None)
        assert set(resp.keys()) == {"batchItemFailures"}
