"""
vuln_python.py — intentionally vulnerable Python file for scanner testing.
Expected: multiple HIGH/MEDIUM findings from Bandit.
"""
import os
import pickle
import hashlib
import subprocess

# B106: hardcoded password
PASSWORD = "supersecret123"

# B307: use of eval()
def run_expression(user_input):
    return eval(user_input)

# B602/B603: shell injection via subprocess
def run_command(cmd):
    subprocess.call(cmd, shell=True)

# B301: insecure deserialization with pickle
def load_data(raw_bytes):
    return pickle.loads(raw_bytes)

# B303: weak hash algorithm (MD5)
def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()

# B105: hardcoded password in variable
def check_auth(user_password):
    secret = "admin1234"
    return user_password == secret
