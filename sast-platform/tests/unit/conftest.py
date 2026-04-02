# conftest.py — pytest configuration for unit tests
#
# sys.path setup is handled by the parent tests/conftest.py which covers
# all test suites (unit + integration). Nothing extra needed here.

# test_lambda_a.py is a standalone script (no pytest test functions).
# It replaces sys.modules["boto3"] with MagicMock at module level, which
# corrupts boto3 for all subsequently executed tests (moto's internal
# boto3.dynamodb.table import fails with "not a package").
# Exclude it from collection so it does not pollute the test session.
collect_ignore = ["test_lambda_a.py"]
