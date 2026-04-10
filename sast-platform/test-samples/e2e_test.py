"""
e2e_test.py — End-to-end integration tests for the SAST Platform.

Submits real vulnerable/clean code files to the deployed API and verifies
that the scanner returns expected findings.

Usage:
    python e2e_test.py --url <LAMBDA_URL> --student-id <YOUR_ID>

Example:
    python e2e_test.py \
        --url https://xxxx.lambda-url.us-east-1.on.aws/ \
        --student-id student001
"""
import argparse
import json
import sys
import time
from pathlib import Path

import urllib.request
import urllib.error

CODE_DIR = Path(__file__).parent / "code"

# ── Test definitions ──────────────────────────────────────────────────────────
# Each entry: (test_id, description, file, language, expect_findings, min_count)
# expect_findings=True  → must have >= min_count findings
# expect_findings=False → must have 0 findings (clean code)

SCAN_TESTS = [
    ("B1", "Python multi-vuln (eval/pickle/MD5/hardcoded)", "vuln_python.py",  "python",     True,  3),
    ("B2", "Python SQL injection",                          "sql_inject.py",   "python",     True,  1),
    ("B3", "JavaScript eval/XSS/hardcoded token",           "vuln_js.js",      "javascript", True,  1),
    ("B4", "Java SQL injection + hardcoded creds",          "VulnJava.java",   "java",       True,  1),
    ("B5", "Go command/SQL injection",                      "vuln.go",         "go",         True,  1),
    ("B6", "Clean Python (no findings expected)",           "clean_python.py", "python",     False, 0),
]

# Lambda A validation tests (no file needed — just bad payloads)
API_TESTS = [
    ("A1", "Empty code field",           {"code": "",         "language": "python",  "student_id": "test"},  400),
    ("A2", "Missing language",           {"code": "x=1",      "student_id": "test"},                         400),
    ("A3", "Unsupported language",       {"code": "x=1",      "language": "cobol",   "student_id": "test"},  400),
    ("A5", "Code over 1 MB",             {"code": "x" * 1_100_000, "language": "python", "student_id": "test"}, 400),
]

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"


def post_json(url, payload, timeout=10):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {}
        return e.code, body


def get_json(url, timeout=10):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def poll_until_done(base_url, scan_id, student_id, max_wait=300):
    """Poll /status until DONE or FAILED, return final status data."""
    deadline = time.time() + max_wait
    interval = 3
    while time.time() < deadline:
        url = f"{base_url}/status?scan_id={scan_id}&student_id={student_id}"
        _, data = get_json(url)
        status = data.get("status")
        if status == "DONE":
            return data
        if status == "FAILED":
            return data
        hint = data.get("retry_after_seconds", interval)
        time.sleep(min(hint, 10))
    return {"status": "TIMEOUT"}


def fetch_report(report_url):
    _, data = get_json(report_url)
    return data


def run_api_tests(base_url):
    print("\n── Lambda A: API Validation Tests ──────────────────────")
    results = []
    for test_id, desc, payload, expected_status in API_TESTS:
        status, _ = post_json(f"{base_url}/scan", payload)
        ok = (status == expected_status)
        print(f"  [{PASS if ok else FAIL}] {test_id}: {desc} → HTTP {status} (expected {expected_status})")
        results.append(ok)
    return results


def run_scan_tests(base_url, student_id):
    print("\n── Lambda B: Scanner Tests ──────────────────────────────")
    results = []
    for test_id, desc, filename, language, expect_findings, min_count in SCAN_TESTS:
        code_path = CODE_DIR / filename
        if not code_path.exists():
            print(f"  [{SKIP}] {test_id}: {desc} → file not found: {filename}")
            results.append(None)
            continue

        code = code_path.read_text()
        status, data = post_json(f"{base_url}/scan", {
            "code": code,
            "language": language,
            "student_id": student_id,
        })

        if status != 202:
            print(f"  [{FAIL}] {test_id}: {desc} → submit failed HTTP {status}")
            results.append(False)
            continue

        scan_id = data.get("scan_id")
        print(f"  [ .. ] {test_id}: {desc} → scan_id={scan_id}, polling...", end="", flush=True)
        final = poll_until_done(base_url, scan_id, student_id)

        if final["status"] == "TIMEOUT":
            print(f"\r  [{FAIL}] {test_id}: {desc} → timed out waiting for result")
            results.append(False)
            continue

        if final["status"] == "FAILED":
            print(f"\r  [{FAIL}] {test_id}: {desc} → scan FAILED")
            results.append(False)
            continue

        report_url = final.get("report_url")
        report = fetch_report(report_url) if report_url else {}
        findings = report.get("findings", [])
        count = len(findings)

        if expect_findings:
            ok = count >= min_count
            print(f"\r  [{PASS if ok else FAIL}] {test_id}: {desc} → {count} findings (expected >={min_count})")
            if not ok:
                print(f"         findings: {[f.get('rule_id') for f in findings]}")
        else:
            ok = count == 0
            print(f"\r  [{PASS if ok else FAIL}] {test_id}: {desc} → {count} findings (expected 0)")

        results.append(ok)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",        required=True, help="Lambda A base URL")
    parser.add_argument("--student-id", default="e2e-test-user", help="Student ID for test scans")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    print(f"\nSAST Platform E2E Tests")
    print(f"Target: {base_url}")
    print(f"Student ID: {args.student_id}")

    api_results  = run_api_tests(base_url)
    scan_results = run_scan_tests(base_url, args.student_id)

    all_results = [r for r in api_results + scan_results if r is not None]
    passed = sum(all_results)
    total  = len(all_results)

    print(f"\n── Summary ──────────────────────────────────────────────")
    print(f"  {passed}/{total} tests passed")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
