# conftest.py — pytest configuration for unit tests
#
# Adds lambda_a/ to sys.path so test files can import validator, dispatcher, etc.
# This file is loaded automatically by pytest before any test in this directory.

import sys
import os

LAMBDA_A_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "lambda_a")
if LAMBDA_A_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(LAMBDA_A_DIR))
