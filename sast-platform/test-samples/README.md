# Test Samples — End-to-End Scanner Validation

This directory contains code samples and an automated test script for validating the deployed SAST platform against a live AWS environment.

These are **not unit tests** — they require a deployed stack and real AWS credentials. For fast, offline unit tests see `tests/unit/`.

---

## Directory Structure

```
test-samples/
├── code/
│   ├── vuln_python.py      # Python — multiple high-severity vulnerabilities
│   ├── sql_inject.py       # Python — SQL injection via string concatenation
│   ├── vuln_js.js          # JavaScript — eval, XSS, hardcoded token
│   ├── VulnJava.java       # Java — SQL injection, command injection, hardcoded creds
│   ├── vuln.go             # Go — command injection, SQL injection
│   └── clean_python.py     # Python — clean code (0 findings expected)
└── e2e_test.py             # Automated test runner
```

---

## Test Scenarios

### Lambda A — API Validation (5 tests)

These verify that the API layer correctly rejects invalid requests.

| ID | Scenario | Expected HTTP |
|----|----------|---------------|
| A1 | Empty `code` field | 400 |
| A2 | Missing `language` field | 400 |
| A3 | Unsupported language (`cobol`) | 400 |
| A4 | Missing `student_id` | 400 or defaults to `anonymous` |
| A5 | Code over 1 MB | 400 |

### Lambda B — Scanner (6 tests)

These verify that the scanner engine detects real vulnerabilities in each supported language.

| ID | File | Language | Vulnerabilities | Tool | Min Findings |
|----|------|----------|-----------------|------|-------------|
| B1 | `vuln_python.py` | Python | `eval()`, `subprocess(shell=True)`, `pickle.loads()`, MD5, hardcoded password | Bandit | 3 |
| B2 | `sql_inject.py` | Python | SQL injection via string concatenation passed directly to `cursor.execute()` | Bandit B608 | 1 |
| B3 | `vuln_js.js` | JavaScript | `eval()`, `document.write(userInput)`, `innerHTML` injection, hardcoded API token | Semgrep | 1 |
| B4 | `VulnJava.java` | Java | SQL injection, `Runtime.exec()` command injection, hardcoded DB password | Semgrep | 1 |
| B5 | `vuln.go` | Go | `exec.Command` with user input, SQL injection via `fmt.Sprintf` | Semgrep | 1 |
| B6 | `clean_python.py` | Python | None — PBKDF2 hashing, `hmac.compare_digest`, `os.urandom` | Bandit | 0 (negative test) |

B6 is a **negative test**: it ensures the scanner does not produce false positives on correctly written code.

---

## Running the Automated Tests

Requires Python 3.8+ and a deployed stack. No third-party packages needed (uses `urllib` from stdlib).

```bash
# Get the Lambda A URL
aws cloudformation describe-stacks \
  --stack-name sast-platform-lambda-a \
  --query "Stacks[0].Outputs[?OutputKey=='LambdaAFunctionUrl'].OutputValue" \
  --output text

# Run all 11 tests
python test-samples/e2e_test.py \
  --url <LAMBDA_URL> \
  --student-id <YOUR_STUDENT_ID>
```

Example output:
```
SAST Platform E2E Tests
Target: https://xxxx.lambda-url.us-east-1.on.aws
Student ID: student001

── Lambda A: API Validation Tests ──────────────────────
  [PASS] A1: Empty code field → HTTP 400 (expected 400)
  [PASS] A2: Missing language → HTTP 400 (expected 400)
  [PASS] A3: Unsupported language → HTTP 400 (expected 400)
  [PASS] A5: Code over 1 MB → HTTP 400 (expected 400)

── Lambda B: Scanner Tests ──────────────────────────────
  [PASS] B1: Python multi-vuln → 5 findings (expected >=3)
  [PASS] B2: Python SQL injection → 2 findings (expected >=1)
  [PASS] B3: JavaScript eval/XSS/hardcoded token → 3 findings (expected >=1)
  [PASS] B4: Java SQL injection + hardcoded creds → 2 findings (expected >=1)
  [PASS] B5: Go command/SQL injection → 2 findings (expected >=1)
  [PASS] B6: Clean Python (no findings expected) → 0 findings (expected 0)

── Summary ──────────────────────────────────────────────
  10/10 tests passed
```

---

## Manual Testing via the UI

You can also drag-and-drop the files in `code/` directly into the frontend:

1. Open the frontend URL in your browser
2. Drag a file from `code/` into the Scanner view — the language is auto-detected from the file extension
3. Click **Scan** and wait for results
4. Check the findings panel against the expected findings table above

> Note: A1–A5 (API validation tests) cannot be tested via the UI — the frontend validates input before submitting. Use `e2e_test.py` for those.

---

## Known Limitations

- **SQL injection in JavaScript/Go/Java**: Semgrep community edition does not perform taint analysis (data-flow tracking). SQL injection is only detected when the concatenated string is passed directly to a known database call in the same expression. Variables used as intermediaries may not be flagged.
- **Hardcoded secrets**: Detection depends on pattern matching and entropy thresholds. Test strings like `hardcoded_api_key_12345` may not match high-confidence secret rules.
- **Concurrent load testing**: See [Issue #106](https://github.com/sunnythreethree/Student-Assignment-Security-Checking-Platform/issues/106) for planned load test support (`--load-test --concurrency 50`).
