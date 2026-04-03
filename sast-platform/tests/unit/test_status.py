"""
test_status.py — Unit tests for status.py (GET /status handler)
CS6620 Group 9

Covers all scan status values including ECS_QUEUED and FAILED,
which were previously unhandled (issue #91).
"""

import unittest.mock as mock
from datetime import datetime, timezone, timedelta

import pytest
from moto import mock_aws
import boto3

import status


REGION     = "us-east-1"
TABLE_NAME = "ScanResults"
BUCKET     = "sast-test-bucket"
STUDENT_ID = "test-student"
SCAN_ID    = "scan-abc12345"


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION",    REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID",     "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN",     "testing")


@pytest.fixture
def table(aws_credentials):
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        tbl = ddb.create_table(
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
        tbl.meta.client.get_waiter("table_exists").wait(TableName=TABLE_NAME)

        with mock.patch.object(status, "dynamodb", ddb):
            yield tbl


def _put(table, item_fields):
    """Insert a scan record into the mock table."""
    table.put_item(Item={
        "student_id": STUDENT_ID,
        "scan_id":    SCAN_ID,
        **item_fields,
    })


def _get_status(s3_bucket=BUCKET):
    return status.get_scan_status(
        scan_id=SCAN_ID,
        student_id=STUDENT_ID,
        table_name=TABLE_NAME,
        s3_bucket=s3_bucket,
    )


# ── Not found ─────────────────────────────────────────────────────────────────

class TestNotFound:
    def test_missing_scan_raises_value_error(self, table):
        with pytest.raises(ValueError, match="not found"):
            _get_status()

    def test_wrong_owner_raises_value_error(self, table):
        _put(table, {"status": "DONE", "language": "python"})
        with pytest.raises(ValueError, match="not found"):
            status.get_scan_status(
                scan_id=SCAN_ID,
                student_id="other-student",
                table_name=TABLE_NAME,
                s3_bucket=BUCKET,
            )


# ── PENDING / IN_PROGRESS ─────────────────────────────────────────────────────

class TestPendingInProgress:
    @pytest.mark.parametrize("scan_status", ["PENDING", "IN_PROGRESS"])
    def test_retry_after_seconds_present(self, table, scan_status):
        _put(table, {"status": scan_status, "language": "python",
                     "created_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert result["retry_after_seconds"] == status.POLLING_INTERVAL_S

    @pytest.mark.parametrize("scan_status", ["PENDING", "IN_PROGRESS"])
    def test_scan_expires_at_present(self, table, scan_status):
        _put(table, {"status": scan_status, "language": "python",
                     "created_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert "scan_expires_at" in result

    @pytest.mark.parametrize("scan_status", ["PENDING", "IN_PROGRESS"])
    def test_no_report_url(self, table, scan_status):
        _put(table, {"status": scan_status, "language": "python",
                     "created_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert "report_url" not in result


# ── ECS_QUEUED ────────────────────────────────────────────────────────────────

class TestECSQueued:
    def test_retry_after_seconds_present(self, table):
        """ECS_QUEUED must include retry_after_seconds so the client keeps polling."""
        _put(table, {"status": "ECS_QUEUED", "language": "java",
                     "created_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert "retry_after_seconds" in result

    def test_retry_interval_is_ecs_value(self, table):
        """ECS scans take longer — the hint should use ECS_POLLING_INTERVAL_S, not POLLING_INTERVAL_S."""
        _put(table, {"status": "ECS_QUEUED", "language": "java",
                     "created_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert result["retry_after_seconds"] == status.ECS_POLLING_INTERVAL_S

    def test_scan_expires_at_present(self, table):
        _put(table, {"status": "ECS_QUEUED", "language": "java",
                     "created_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert "scan_expires_at" in result

    def test_no_report_url(self, table):
        _put(table, {"status": "ECS_QUEUED", "language": "java",
                     "created_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert "report_url" not in result

    def test_status_field_is_ecs_queued(self, table):
        _put(table, {"status": "ECS_QUEUED", "language": "java",
                     "created_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert result["status"] == "ECS_QUEUED"


# ── FAILED ────────────────────────────────────────────────────────────────────

class TestFailed:
    def test_error_message_present(self, table):
        """FAILED status must expose the error_message so the client can show it."""
        _put(table, {"status": "FAILED", "language": "python",
                     "completed_at": datetime.now(timezone.utc).isoformat(),
                     "error_message": "Bandit execution failed"})
        result = _get_status()
        assert result["error_message"] == "Bandit execution failed"

    def test_default_error_message_when_missing(self, table):
        """If no error_message is stored, a safe default is returned."""
        _put(table, {"status": "FAILED", "language": "python",
                     "completed_at": datetime.now(timezone.utc).isoformat()})
        result = _get_status()
        assert "error_message" in result
        assert result["error_message"]  # non-empty

    def test_completed_at_present(self, table):
        _put(table, {"status": "FAILED", "language": "python",
                     "completed_at": "2025-04-02T10:35:00Z"})
        result = _get_status()
        assert result["completed_at"] == "2025-04-02T10:35:00Z"

    def test_no_retry_after_seconds(self, table):
        """FAILED is terminal — no polling hint needed."""
        _put(table, {"status": "FAILED", "language": "python"})
        result = _get_status()
        assert "retry_after_seconds" not in result


# ── DONE ──────────────────────────────────────────────────────────────────────

class TestDone:
    def test_vuln_count_present(self, table):
        _put(table, {"status": "DONE", "language": "python",
                     "vuln_count": 3, "s3_report_key": "reports/s/scan.json"})
        with mock.patch.object(status, "s3") as mock_s3:
            mock_s3.generate_presigned_url.return_value = "https://example.com/report"
            result = _get_status()
        assert result["vuln_count"] == 3

    def test_report_url_present(self, table):
        _put(table, {"status": "DONE", "language": "python",
                     "vuln_count": 0, "s3_report_key": "reports/s/scan.json"})
        with mock.patch.object(status, "s3") as mock_s3:
            mock_s3.generate_presigned_url.return_value = "https://example.com/report"
            result = _get_status()
        assert result["report_url"] == "https://example.com/report"

    def test_no_retry_after_seconds(self, table):
        """DONE is terminal — no polling hint needed."""
        _put(table, {"status": "DONE", "language": "python", "vuln_count": 0})
        with mock.patch.object(status, "s3") as mock_s3:
            mock_s3.generate_presigned_url.return_value = "https://x.com/r"
            result = _get_status()
        assert "retry_after_seconds" not in result
