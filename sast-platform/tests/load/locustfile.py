"""
Load test for the SAST Platform API (Lambda A Function URL).

Usage:
    pip install locust
    locust -f locustfile.py --host https://owptzx2rc5j5qtlevonifzrv6a0fwynz.lambda-url.us-east-1.on.aws

Then open http://localhost:8089 and set:
    - Number of users: 50
    - Spawn rate:      10
    - Run time:        60s

Or headless (CI / quick verification):
    locust -f locustfile.py \
        --host https://owptzx2rc5j5qtlevonifzrv6a0fwynz.lambda-url.us-east-1.on.aws \
        --headless -u 50 -r 10 -t 60s \
        --csv results/load_test
"""

import random
import string
import uuid
from locust import HttpUser, task, between


# ---------------------------------------------------------------------------
# Sample code payloads — one per supported language
# ---------------------------------------------------------------------------

PYTHON_SNIPPETS = [
    "import os\nresult = eval(input())\nprint(result)",
    "import subprocess\nsubprocess.run(['ls', '-la'])",
    "password = 'hardcoded_secret_123'\nprint(password)",
    "import pickle\ndata = pickle.loads(user_input)",
    "exec(open('script.py').read())",
]

JAVA_SNIPPETS = [
    "public class Main {\n    public static void main(String[] args) {\n        Runtime.getRuntime().exec(args[0]);\n    }\n}",
    "import java.sql.*;\nString query = \"SELECT * FROM users WHERE id = \" + userId;",
]

JAVASCRIPT_SNIPPETS = [
    "const result = eval(userInput);",
    "const exec = require('child_process').exec;\nexec(process.argv[1]);",
    "document.innerHTML = userInput;",
]

LANGUAGE_POOL = (
    [("python", s) for s in PYTHON_SNIPPETS] +
    [("java", s) for s in JAVA_SNIPPETS] +
    [("javascript", s) for s in JAVASCRIPT_SNIPPETS]
)


def _random_student_id() -> str:
    """Generate a random student ID for each virtual user."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"load-test-{suffix}"


# ---------------------------------------------------------------------------
# Locust user
# ---------------------------------------------------------------------------

class ScanUser(HttpUser):
    """
    Simulates a student submitting code for scanning and polling for results.

    wait_time: pause between tasks to simulate realistic user behaviour.
    """
    wait_time = between(1, 3)

    def on_start(self):
        """Called once when a simulated user starts."""
        self.student_id = _random_student_id()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    @task(3)
    def submit_scan(self):
        """POST /scan — submit a code snippet for analysis."""
        language, code = random.choice(LANGUAGE_POOL)
        payload = {
            "code": code,
            "language": language,
            "student_id": self.student_id,
        }
        with self.client.post(
            "/scan",
            json=payload,
            name="POST /scan",
            catch_response=True,
        ) as resp:
            if resp.status_code == 202:
                # Store scan_id so the poll task can use it
                data = resp.json()
                self._last_scan_id = data.get("scan_id")
                resp.success()
            elif resp.status_code == 429:
                # Lambda throttled — expected under high load, not a failure
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}: {resp.text[:200]}")

    @task(1)
    def poll_status(self):
        """GET /status — poll for scan result."""
        scan_id = getattr(self, "_last_scan_id", None)
        if not scan_id:
            return  # No scan submitted yet by this user

        with self.client.get(
            f"/status?scan_id={scan_id}&student_id={self.student_id}",
            name="GET /status",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}: {resp.text[:200]}")
