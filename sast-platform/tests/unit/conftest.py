# conftest.py — pytest configuration for unit tests
#
# Adds lambda_a/ and lambda_b/ to sys.path so test files can import
# validator, dispatcher, scanner, etc. without inline sys.path manipulation.
# This file is loaded automatically by pytest before any test in this directory.

import sys
import os

LAMBDA_A_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "lambda_a")
LAMBDA_B_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "lambda_b")

for path in (LAMBDA_A_DIR, LAMBDA_B_DIR):
    abs_path = os.path.abspath(path)
    if abs_path not in sys.path:
        sys.path.insert(0, abs_path)
