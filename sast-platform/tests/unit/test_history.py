"""
test_history.py — Unit tests for lambda_a/history.py
Uses moto to mock DynamoDB without real AWS credentials.

Run with:
    pytest tests/unit/test_history.py -v
"""

from datetime import datetime, timezone, timedelta
import unittest.mock as mock

import boto3
import pytest
from moto import mock_aws

import history


# ── Constants ──────────────────────────────────────────────────────────────────

REGION     = "us-east-1"
TABLE_NAME = "test-scan-results"


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION",    REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID",     "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN",    "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN",     "testing")


@pytest.fixture
def ddb_table():
    """Moto-backed DynamoDB table with the same schema as dynamodb.yaml."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=REGION)
        table = resource.create_table(
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

        with mock.patch.object(history, "dynamodb", resource):
            yield table


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ts(offset_minutes=0):
    """ISO-8601 timestamp, optionally offset from now."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    return dt.isoformat()


def _put(table, student_id, scan_id, status="DONE", language="python",
         created_at=None, vuln_count=None, completed_at=None):
    item = {
        "student_id": student_id,
        "scan_id":    scan_id,
        "status":     status,
        "language":   language,
        "created_at": created_at or _ts(),
    }
    if vuln_count is not None:
        item["vuln_count"] = vuln_count
    if completed_at:
        item["completed_at"] = completed_at
    table.put_item(Item=item)
    return item


def _call(ddb_table, student_id="stu-1"):
    return history.get_scan_history(student_id=student_id, table_name=TABLE_NAME)


# ── Empty state ────────────────────────────────────────────────────────────────

class TestEmptyHistory:

    def test_no_scans_returns_empty_list(self, ddb_table):
        result = _call(ddb_table, student_id="nobody")
        assert result == []


# ── Return shape ───────────────────────────────────────────────────────────────

class TestReturnShape:

    def test_pending_item_has_required_fields(self, ddb_table):
        _put(ddb_table, "stu-1", "scan-001", status="PENDING")
        items = _call(ddb_table)
        assert len(items) == 1
        item = items[0]
        assert item["scan_id"]    == "scan-001"
        assert item["status"]     == "PENDING"
        assert item["language"]   == "python"
        assert "created_at" in item

    def test_pending_item_does_not_include_vuln_count(self, ddb_table):
        _put(ddb_table, "stu-1", "scan-001", status="PENDING")
        item = _call(ddb_table)[0]
        assert "vuln_count" not in item

    def test_done_item_includes_vuln_count(self, ddb_table):
        _put(ddb_table, "stu-1", "scan-001", status="DONE", vuln_count=5)
        item = _call(ddb_table)[0]
        assert item["vuln_count"] == 5

    def test_done_item_includes_completed_at(self, ddb_table):
        ts = _ts()
        _put(ddb_table, "stu-1", "scan-001", status="DONE",
             vuln_count=0, completed_at=ts)
        item = _call(ddb_table)[0]
        assert item["completed_at"] == ts

    def test_failed_item_does_not_include_vuln_count(self, ddb_table):
        _put(ddb_table, "stu-1", "scan-001", status="FAILED")
        item = _call(ddb_table)[0]
        assert "vuln_count" not in item


# ── Ordering ───────────────────────────────────────────────────────────────────

class TestOrdering:

    def test_newest_first(self, ddb_table):
        _put(ddb_table, "stu-order", "scan-old",    created_at=_ts(-10))
        _put(ddb_table, "stu-order", "scan-middle", created_at=_ts(-5))
        _put(ddb_table, "stu-order", "scan-new",    created_at=_ts(0))
        items = _call(ddb_table, student_id="stu-order")
        assert [i["scan_id"] for i in items] == ["scan-new", "scan-middle", "scan-old"]


# ── Isolation ─────────────────────────────────────────────────────────────────

class TestIsolation:

    def test_only_returns_scans_for_requested_student(self, ddb_table):
        _put(ddb_table, "stu-a", "scan-a1")
        _put(ddb_table, "stu-b", "scan-b1")
        result_a = _call(ddb_table, student_id="stu-a")
        result_b = _call(ddb_table, student_id="stu-b")
        assert all(i["scan_id"] == "scan-a1" for i in result_a)
        assert all(i["scan_id"] == "scan-b1" for i in result_b)

    def test_multiple_scans_for_same_student(self, ddb_table):
        for i in range(3):
            _put(ddb_table, "stu-multi", f"scan-{i:03d}")
        result = _call(ddb_table, student_id="stu-multi")
        assert len(result) == 3


# ── Limit ─────────────────────────────────────────────────────────────────────

class TestLimit:

    def test_limit_is_respected(self, ddb_table):
        # Insert more items than MAX_HISTORY_ITEMS
        original_limit = history.MAX_HISTORY_ITEMS
        history.MAX_HISTORY_ITEMS = 3
        try:
            for i in range(5):
                _put(ddb_table, "stu-limit", f"scan-{i:03d}")
            result = _call(ddb_table, student_id="stu-limit")
            assert len(result) <= 3
        finally:
            history.MAX_HISTORY_ITEMS = original_limit
