# vulnerable_python.py — sample file for SAST Platform demo
#
# Submit this file via the UI (language: python) to produce known HIGH findings.
# Expected Bandit results:
#   B602 HIGH   — subprocess call with shell=True (command injection)
#   B307 HIGH   — use of eval
#   B301 MEDIUM — use of pickle.loads
#   B105 LOW    — hardcoded password string

import subprocess
import pickle
import os

ADMIN_PASSWORD = "hunter2"          # B105: hardcoded password


def run_command(user_input):
    # B602: subprocess with shell=True — arbitrary command injection
    subprocess.call(user_input, shell=True)


def evaluate_expression(user_input):
    # B307: eval — arbitrary code execution
    return eval(user_input)


def load_data(user_bytes):
    # B301: pickle.loads — arbitrary object deserialization
    return pickle.loads(user_bytes)


if __name__ == "__main__":
    cmd = input("Enter command: ")
    run_command(cmd)

    expr = input("Enter expression: ")
    result = evaluate_expression(expr)
    print("Result:", result)
