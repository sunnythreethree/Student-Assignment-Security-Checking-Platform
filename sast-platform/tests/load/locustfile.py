"""
locustfile.py — Load test for SAST Platform
CS6620 Group 9

Simulates an entire class submitting assignments simultaneously.
Verifies that SQS buffers requests correctly and Lambda A stays responsive
under concurrent load.

Run with:
    locust -f tests/load/locustfile.py --host=https://<LAMBDA_URL> \
           --users=30 --spawn-rate=5 --run-time=2m --headless

Environment variables:
    STUDENT_KEY   — A valid X-Student-Key for load testing (required)
    LOCUST_HOST   — Override for --host flag
"""

import os
import json
import random

from locust import HttpUser, task, between, events

STUDENT_KEY = os.environ.get("STUDENT_KEY", "load-test-key")

# Sample code snippets to vary the payload (prevents identical message dedup in SQS)
PYTHON_SNIPPETS = [
    "def hello():\n    return 'hello world'\n",
    "x = [i for i in range(100)]\nprint(x)\n",
    "import math\nresult = math.sqrt(16)\nprint(result)\n",
    "data = {'key': 'value'}\nprint(data.get('key'))\n",
    "class Counter:\n    def __init__(self):\n        self.count = 0\n    def increment(self):\n        self.count += 1\n",
]

VULNERABLE_SNIPPETS = [
    # Bandit B602 — shell injection
    "import subprocess\nsubprocess.call('ls', shell=True)\n",
    # Bandit B105 — hardcoded password
    "password = 'hunter2'\nprint(password)\n",
]


class StudentUser(HttpUser):
    """
    Simulates a student submitting a code scan and polling for results.
    Think time of 1-3 seconds between tasks models realistic browser behavior.
    """
    wait_time = between(1, 3)

    def on_start(self):
        self.headers = {
            "X-Student-Key": STUDENT_KEY,
            "Content-Type": "application/json",
        }

    @task(4)
    def submit_clean_scan(self):
        """Most students submit normal code (weight 4)."""
        code = random.choice(PYTHON_SNIPPETS)
        with self.client.post(
            "/",
            headers=self.headers,
            json={"code": code, "language": "python"},
            name="POST /scan (clean)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 202:
                resp.success()
            elif resp.status_code == 429:
                resp.failure("Rate limited")
            else:
                resp.failure(f"Unexpected {resp.status_code}: {resp.text[:100]}")

    @task(1)
    def submit_vulnerable_scan(self):
        """Some students submit vulnerable code (weight 1)."""
        code = random.choice(VULNERABLE_SNIPPETS)
        with self.client.post(
            "/",
            headers=self.headers,
            json={"code": code, "language": "python"},
            name="POST /scan (vulnerable)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 202:
                resp.success()
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(2)
    def poll_status(self):
        """Students poll for status after submitting (weight 2)."""
        # Use a plausible-looking scan_id — will return 404, which is expected
        fake_id = f"scan-{''.join(random.choices('0123456789abcdef', k=8))}"
        with self.client.get(
            "/",
            headers=self.headers,
            params={"scan_id": fake_id},
            name="GET /status",
            catch_response=True,
        ) as resp:
            # 404 is expected for a random scan_id — still measures latency
            if resp.status_code in (200, 404):
                resp.success()
            elif resp.status_code == 401:
                resp.failure("Auth failed — check STUDENT_KEY env var")
            else:
                resp.failure(f"Unexpected {resp.status_code}")

    @task(1)
    def invalid_request(self):
        """
        A small fraction of requests are malformed (missing code).
        Lambda A should reject them quickly with 400 — verifies fast-path latency.
        """
        with self.client.post(
            "/",
            headers=self.headers,
            json={"language": "python"},
            name="POST /scan (invalid — no code)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 400:
                resp.success()
            else:
                resp.failure(f"Expected 400, got {resp.status_code}")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n" + "=" * 60)
    print("  SAST Platform Load Test")
    print("  Scenario: class-wide simultaneous submission")
    print(f"  Target:   {environment.host}")
    print("=" * 60 + "\n")
