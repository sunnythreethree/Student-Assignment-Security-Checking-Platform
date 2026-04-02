# Makefile — convenience targets for local development
# Run from the sast-platform/ directory.

.PHONY: install install-a install-b test test-unit test-no-scan test-integration lint help

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  install        Install test deps for both Lambda A and B"
	@echo "  install-a      Install Lambda A deps only"
	@echo "  install-b      Install Lambda B deps only"
	@echo "  test           Run unit + integration tests (no AWS needed)"
	@echo "  test-unit      Run unit tests only"
	@echo "  test-no-scan   Run unit tests, skip scanner (no bandit/semgrep needed)"
	@echo "  test-integration  Run integration tests only"
	@echo "  lint           Run bandit on lambda_a/ and lambda_b/"

install: install-a install-b

install-a:
	pip install -r lambda_a/requirements.txt

install-b:
	pip install -r lambda_b/requirements.txt

test:
	pytest tests/unit tests/integration -v

test-unit:
	pytest tests/unit -v

test-no-scan:
	pytest tests/unit -v -k "not scanner"

test-integration:
	pytest tests/integration -v

lint:
	bandit -r lambda_a/ lambda_b/ -ll
