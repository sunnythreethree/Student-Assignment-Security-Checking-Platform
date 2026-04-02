# conftest.py — pytest configuration for unit tests
#
# Adds lambda_a/ and lambda_b/ to sys.path so test files can import
# validator, dispatcher, result_parser, scanner, etc. without installing
# the packages. Loaded automatically by pytest before any test in this directory.

import sys
import os

_HERE = os.path.dirname(__file__)

LAMBDA_A_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "lambda_a"))
LAMBDA_B_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "lambda_b"))

for _dir in (LAMBDA_A_DIR, LAMBDA_B_DIR):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)
