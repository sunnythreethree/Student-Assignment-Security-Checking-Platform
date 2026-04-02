"""
test_full_scan_flow.py — End-to-end tests against a live deployment
CS6620 Group 9

Requires a deployed environment. Tests are automatically skipped when
environment variables are not set, so they never block CI.

Required env vars:
    LAMBDA_URL    — Lambda A Function URL (e.g. https://xxxx.lambda-url.us-east-1.on.aws/)
    STUDENT_KEY   — A valid X-Student-Key value seeded in the StudentAuth table

Run against a real deployment:
    LAMBDA_URL=https://... STUDENT_KEY=abc123 pytest tests/e2e/ -v

Run in CI (skipped automatically):
    pytest tests/e2e/ -v
"""

import os
import time
import pytest

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

LAMBDA_URL  = os.environ.get("LAMBDA_URL", "").rstrip("/")
STUDENT_KEY = os.environ.get("STUDENT_KEY", "")

SKIP_REASON = (
    "E2E tests require LAMBDA_URL and STUDENT_KEY environment variables. "
    "Set them to run against a live deployment."
)

needs_deployment = pytest.mark.skipif(
    not LAMBDA_URL or not STUDENT_KEY or not HAS_REQUESTS,
    reason=SKIP_REASON,
)

POLL_INTERVAL = 5   # seconds between status polls
POLL_TIMEOUT  = 120 # seconds before giving up


def _headers():
    return {"X-Student-Key": STUDENT_KEY, "Content-Type": "application/json"}


def _post_scan(code, language="python"):
    resp = requests.post(
        f"{LAMBDA_URL}",
        headers=_headers(),
        json={"code": code, "language": language},
        timeout=15,
    )
    return resp


def _poll_status(scan_id, timeout=POLL_TIMEOUT):
    """Poll GET /status until status != PENDING or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(
            f"{LAMBDA_URL}",
            headers=_headers(),
            params={"scan_id": scan_id},
            timeout=15,
        )
        assert resp.status_code == 200, f"Status poll failed: {resp.status_code} {resp.text}"
        body = resp.json()
        if body["status"] != "PENDING":
            return body
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"Scan {scan_id} did not complete within {timeout}s")


# ── Auth tests ─────────────────────────────────────────────────────────────────

@needs_deployment
class TestAuthentication:

    def test_post_without_key_returns_401(self):
        resp = requests.post(
            f"{LAMBDA_URL}",
            headers={"Content-Type": "application/json"},
            json={"code": "x=1", "language": "python"},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_get_without_key_returns_401(self):
        resp = requests.get(
            f"{LAMBDA_URL}",
            params={"scan_id": "fake-scan-id"},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_post_with_invalid_key_returns_401(self):
        resp = requests.post(
            f"{LAMBDA_URL}",
            headers={"X-Student-Key": "invalid-key-000", "Content-Type": "application/json"},
            json={"code": "x=1", "language": "python"},
            timeout=10,
        )
        assert resp.status_code == 401


# ── Input validation tests ─────────────────────────────────────────────────────

@needs_deployment
class TestInputValidation:

    def test_missing_code_returns_400(self):
        resp = _post_scan("")
        assert resp.status_code == 400

    def test_unsupported_language_returns_400(self):
        resp = _post_scan("x=1", language="cobol")
        assert resp.status_code == 400

    def test_invalid_json_body_returns_400(self):
        resp = requests.post(
            f"{LAMBDA_URL}",
            headers=_headers(),
            data="not-json",
            timeout=10,
        )
        assert resp.status_code == 400


# ── Full scan flow ─────────────────────────────────────────────────────────────

@needs_deployment
class TestFullScanFlow:

    def test_clean_python_code_completes(self):
        """Clean Python code should complete with 0 vulnerabilities."""
        code = "def add(a, b):\n    return a + b\n"
        resp = _post_scan(code, language="python")
        assert resp.status_code == 202
        scan_id = resp.json()["scan_id"]
        assert scan_id.startswith("scan-")

        result = _poll_status(scan_id)
        assert result["status"] == "DONE"
        assert result["vuln_count"] == 0

    def test_vulnerable_python_code_reports_findings(self):
        """Code with known vulnerabilities should return vuln_count > 0."""
        # subprocess with shell=True — Bandit B602
        code = (
            "import subprocess\n"
            "user_input = input()\n"
            "subprocess.call(user_input, shell=True)\n"
        )
        resp = _post_scan(code, language="python")
        assert resp.status_code == 202
        scan_id = resp.json()["scan_id"]

        result = _poll_status(scan_id)
        assert result["status"] == "DONE"
        assert result["vuln_count"] > 0

    def test_done_result_includes_report_url(self):
        """A completed scan should return a presigned S3 URL for the report."""
        code = "x = 1 + 1\n"
        scan_id = _post_scan(code).json()["scan_id"]
        result = _poll_status(scan_id)
        assert result["status"] == "DONE"
        assert "report_url" in result
        assert result["report_url"].startswith("https://")

    def test_status_missing_scan_id_returns_400(self):
        resp = requests.get(f"{LAMBDA_URL}", headers=_headers(), timeout=10)
        assert resp.status_code == 400

    def test_status_unknown_scan_id_returns_404(self):
        resp = requests.get(
            f"{LAMBDA_URL}",
            headers=_headers(),
            params={"scan_id": "scan-doesnotexist"},
            timeout=10,
        )
        assert resp.status_code == 404

    def test_post_scan_returns_202_with_pending_status(self):
        resp = _post_scan("print('hello')", language="python")
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "PENDING"
        assert "scan_id" in body
