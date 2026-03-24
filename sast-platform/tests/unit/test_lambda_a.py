"""
run_tests.py — Lambda A Local Unit Tests
Jingsi Zhang | CS6620 Group 9
"""

import sys
import os
import json
import unittest.mock as mock

# Add lambda_a/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda_a"))

# Dummy env vars so handler.py doesn't crash on import
os.environ["SQS_QUEUE_URL"] = "https://sqs.us-east-1.amazonaws.com/123456789/test-queue"
os.environ["DYNAMODB_TABLE"] = "sast-scans-test"
os.environ["S3_BUCKET"] = "sast-reports-test"

# Mock boto3 before importing handler
sys.modules["boto3"] = mock.MagicMock()
sys.modules["botocore"] = mock.MagicMock()
sys.modules["botocore.exceptions"] = mock.MagicMock()
sys.modules["boto3.dynamodb"] = mock.MagicMock()
sys.modules["boto3.dynamodb.conditions"] = mock.MagicMock()

from validator import validate_scan_request, normalize
import handler

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  Passed: [{name}]")
        passed += 1
    else:
        print(f"  Failed: [{name}] {detail}")
        failed += 1

print("=" * 55)
print("  Lambda A — Unit Tests")
print("=" * 55)

# ── Validator: 13 cases ──────────────────────────────────
print("\nValidator Tests (13 cases)")
print("-" * 55)

ok, _ = validate_scan_request({"code": "print(1)", "language": "python", "student_id": "neu123"})
check("valid python", ok)

ok, _ = validate_scan_request({"code": "System.out()", "language": "java", "student_id": "neu456"})
check("valid java", ok)

ok, _ = validate_scan_request({"code": "console.log()", "language": "javascript", "student_id": "neu789"})
check("valid javascript", ok)

ok, _ = validate_scan_request({"language": "python", "student_id": "neu123"})
check("missing code", not ok)

ok, _ = validate_scan_request({"code": "   ", "language": "python", "student_id": "neu123"})
check("empty code", not ok)

ok, _ = validate_scan_request({"code": 12345, "language": "python", "student_id": "neu123"})
check("code not a string", not ok)

ok, _ = validate_scan_request({"code": "x" * (1024 * 1024 + 1), "language": "python", "student_id": "neu123"})
check("code exceeds 1 MB", not ok)

ok, _ = validate_scan_request({"code": "x=1", "student_id": "neu123"})
check("missing language", not ok)

ok, _ = validate_scan_request({"code": "x=1", "language": "cobol", "student_id": "neu123"})
check("unsupported language (cobol)", not ok)

ok, _ = validate_scan_request({"code": "print(1)", "language": "Python", "student_id": "neu123"})
check("language case-insensitive (Python → python)", ok)

ok, _ = validate_scan_request({"code": "x=1", "language": "python"})
check("missing student_id", not ok)

ok, _ = validate_scan_request({"code": "x=1", "language": "python", "student_id": ""})
check("empty student_id", not ok)

ok, _ = validate_scan_request({})
check("empty body", not ok)

# ── Normalize: 1 case ────────────────────────────────────
print("\nNormalize Tests (1 case)")
print("-" * 55)

r = normalize({"code": "  print(1)  ", "language": "Python", "student_id": "  neu123  "})
check("normalize: strip + lowercase", r["language"] == "python" and r["code"] == "print(1)" and r["student_id"] == "neu123")

# ── Handler routing: 8 cases ─────────────────────────────
print("\nHandler Routing Tests (8 cases)")
print("-" * 55)

resp = handler._response(202, {"scan_id": "scan-abc", "status": "PENDING"})
check("_response: 202 with correct body", json.loads(resp["body"])["scan_id"] == "scan-abc")
check("_response: CORS headers present", resp["headers"]["Access-Control-Allow-Origin"] == "*")

resp = handler._response(404, {"error": "not found"})
check("_response: 404 status code", resp["statusCode"] == 404)

event = {"requestContext": {"http": {"method": "OPTIONS"}}, "body": None}
resp = handler.lambda_handler(event, None)
check("OPTIONS preflight → 200", resp["statusCode"] == 200)

event = {"requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"code": "", "language": "python", "student_id": "neu123"})}
resp = handler.lambda_handler(event, None)
check("POST empty code → 400", resp["statusCode"] == 400)

event = {"requestContext": {"http": {"method": "POST"}}, "body": "not-json"}
resp = handler.lambda_handler(event, None)
check("POST invalid JSON → 400", resp["statusCode"] == 400)

event = {"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": {}}
resp = handler.lambda_handler(event, None)
check("GET missing scan_id → 400", resp["statusCode"] == 400)

event = {"requestContext": {"http": {"method": "DELETE"}}, "body": None}
resp = handler.lambda_handler(event, None)
check("unsupported method → 405", resp["statusCode"] == 405)

# ── Summary ──────────────────────────────────────────────
print()
print("=" * 55)
print(f"  Results: {passed} passed, {failed} failed")
print("=" * 55)

if failed == 0:
    print("\n  All tests passed.\n")
else:
    print(f"\n  {failed} tests failed.\n")
    sys.exit(1)
