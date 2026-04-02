"""
test_lambda_a.py — Lambda A Unit Tests
Jingsi Zhang | CS6620 Group 9

Updated for PR #37 auth contract:
- student_id is no longer in the request body (resolved from X-Student-Key header)
- handler._resolve_student is mocked so tests don't hit DynamoDB
"""

import sys
import os
import json
import unittest.mock as mock

# Add lambda_a/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda_a"))

# Dummy env vars so handler.py doesn't crash on import
os.environ["SQS_QUEUE_URL"]  = "https://sqs.us-east-1.amazonaws.com/123456789/test-queue"
os.environ["DYNAMODB_TABLE"] = "sast-scans-test"
os.environ["S3_BUCKET"]      = "sast-reports-test"
os.environ["AUTH_TABLE"]     = "student-auth-test"

# Mock boto3 before importing handler
sys.modules["boto3"]                     = mock.MagicMock()
sys.modules["botocore"]                  = mock.MagicMock()
sys.modules["botocore.exceptions"]       = mock.MagicMock()
sys.modules["boto3.dynamodb"]            = mock.MagicMock()
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

print("=" * 60)
print("  Lambda A — Unit Tests")
print("=" * 60)


# ── Validator: 11 cases ──────────────────────────────────
# student_id is no longer validated here — identity comes from the auth header.
print("\nValidator Tests (11 cases)")
print("-" * 60)

ok, _ = validate_scan_request({"code": "print(1)", "language": "python"})
check("valid python", ok)

ok, _ = validate_scan_request({"code": "System.out()", "language": "java"})
check("valid java", ok)

ok, _ = validate_scan_request({"code": "console.log()", "language": "javascript"})
check("valid javascript", ok)

ok, _ = validate_scan_request({"language": "python"})
check("missing code → invalid", not ok)

ok, _ = validate_scan_request({"code": "   ", "language": "python"})
check("empty code → invalid", not ok)

ok, _ = validate_scan_request({"code": 12345, "language": "python"})
check("code not a string → invalid", not ok)

ok, _ = validate_scan_request({"code": "x" * (1024 * 1024 + 1), "language": "python"})
check("code exceeds 1 MB → invalid", not ok)

ok, _ = validate_scan_request({"code": "x=1"})
check("missing language → invalid", not ok)

ok, _ = validate_scan_request({"code": "x=1", "language": "cobol"})
check("unsupported language (cobol) → invalid", not ok)

ok, _ = validate_scan_request({"code": "print(1)", "language": "Python"})
check("language case-insensitive (Python → python)", ok)

ok, _ = validate_scan_request({})
check("empty body → invalid", not ok)


# ── Normalize: 1 case ────────────────────────────────────
print("\nNormalize Tests (1 case)")
print("-" * 60)

r = normalize({"code": "  print(1)  ", "language": "Python"})
check("normalize: strip + lowercase, returns only {code, language}",
      r["language"] == "python"
      and r["code"] == "print(1)"
      and set(r.keys()) == {"code", "language"})


# ── Helper: _response ────────────────────────────────────
print("\nHandler _response Tests (3 cases)")
print("-" * 60)

resp = handler._response(202, {"scan_id": "scan-abc", "status": "PENDING"})
check("_response: 202 with correct body",
      json.loads(resp["body"])["scan_id"] == "scan-abc")
check("_response: CORS headers present",
      resp["headers"]["Access-Control-Allow-Origin"] == "*")

resp = handler._response(404, {"error": "not found"})
check("_response: 404 status code", resp["statusCode"] == 404)


# ── Handler routing ──────────────────────────────────────
print("\nHandler Routing Tests (9 cases)")
print("-" * 60)

# OPTIONS — no auth required
event = {"requestContext": {"http": {"method": "OPTIONS"}}, "body": None}
resp = handler.lambda_handler(event, None)
check("OPTIONS preflight → 200", resp["statusCode"] == 200)

# Unsupported method
event = {"requestContext": {"http": {"method": "DELETE"}}, "body": None}
resp = handler.lambda_handler(event, None)
check("unsupported method → 405", resp["statusCode"] == 405)

# POST — missing X-Student-Key header → 401
event = {"requestContext": {"http": {"method": "POST"}},
         "headers": {},
         "body": json.dumps({"code": "print(1)", "language": "python"})}
with mock.patch.object(handler, "_resolve_student", return_value=None):
    resp = handler.lambda_handler(event, None)
check("POST missing/invalid X-Student-Key → 401", resp["statusCode"] == 401)

# POST — invalid JSON body → 400 (auth passes, body parse fails)
event = {"requestContext": {"http": {"method": "POST"}},
         "headers": {"x-student-key": "valid-key"},
         "body": "not-json"}
with mock.patch.object(handler, "_resolve_student", return_value="neu123"):
    resp = handler.lambda_handler(event, None)
check("POST invalid JSON → 400", resp["statusCode"] == 400)

# POST — empty code → 400
event = {"requestContext": {"http": {"method": "POST"}},
         "headers": {"x-student-key": "valid-key"},
         "body": json.dumps({"code": "", "language": "python"})}
with mock.patch.object(handler, "_resolve_student", return_value="neu123"):
    resp = handler.lambda_handler(event, None)
check("POST empty code → 400", resp["statusCode"] == 400)

# POST — valid request → 202
event = {"requestContext": {"http": {"method": "POST"}},
         "headers": {"x-student-key": "valid-key"},
         "body": json.dumps({"code": "print(1)", "language": "python"})}
with mock.patch.object(handler, "_resolve_student", return_value="neu123"), \
     mock.patch("handler.create_scan_job", return_value="scan-abc123"):
    resp = handler.lambda_handler(event, None)
check("POST valid request → 202", resp["statusCode"] == 202)

# GET — missing X-Student-Key header → 401
event = {"requestContext": {"http": {"method": "GET"}},
         "headers": {},
         "queryStringParameters": {"scan_id": "scan-abc"}}
with mock.patch.object(handler, "_resolve_student", return_value=None):
    resp = handler.lambda_handler(event, None)
check("GET missing/invalid X-Student-Key → 401", resp["statusCode"] == 401)

# GET — missing scan_id → 400
event = {"requestContext": {"http": {"method": "GET"}},
         "headers": {"x-student-key": "valid-key"},
         "queryStringParameters": {}}
with mock.patch.object(handler, "_resolve_student", return_value="neu123"):
    resp = handler.lambda_handler(event, None)
check("GET missing scan_id → 400", resp["statusCode"] == 400)

# GET — scan not found / wrong owner → 404
event = {"requestContext": {"http": {"method": "GET"}},
         "headers": {"x-student-key": "valid-key"},
         "queryStringParameters": {"scan_id": "scan-missing"}}
with mock.patch.object(handler, "_resolve_student", return_value="neu123"), \
     mock.patch("handler.get_scan_status", side_effect=ValueError("not found")):
    resp = handler.lambda_handler(event, None)
check("GET scan not found → 404", resp["statusCode"] == 404)


# ── Summary ──────────────────────────────────────────────
print()
print("=" * 60)
print(f"  Results: {passed} passed, {failed} failed")
print("=" * 60)

if failed == 0:
    print("\n  All tests passed.\n")
else:
    print(f"\n  {failed} test(s) failed.\n")
    sys.exit(1)
