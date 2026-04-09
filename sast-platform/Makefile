# Makefile — convenience targets for local development
# Run from the sast-platform/ directory.

.PHONY: install install-a install-b test test-unit test-no-scan test-integration lint deploy help

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  deploy         Full-stack deploy (requires CODE_BUCKET)"
	@echo "                 make deploy CODE_BUCKET=my-bucket [ENV=dev] [REGION=us-east-1]"
	@echo "                 make deploy CODE_BUCKET=my-bucket VPC_ID=vpc-xxx SUBNETS=subnet-xxx"
	@echo "                 make deploy CODE_BUCKET=my-bucket STUDENT_KEY=abc123  (+ smoke test)"
	@echo "  install        Install test deps for both Lambda A and B"
	@echo "  install-a      Install Lambda A deps only"
	@echo "  install-b      Install Lambda B deps only"
	@echo "  test           Run unit + integration tests (no AWS needed)"
	@echo "  test-unit      Run unit tests only"
	@echo "  test-no-scan   Run unit tests, skip scanner (no bandit/semgrep needed)"
	@echo "  test-integration  Run integration tests only"
	@echo "  lint           Run bandit on lambda_a/ and lambda_b/"

deploy:
	@if [ -z "$(CODE_BUCKET)" ]; then echo "ERROR: CODE_BUCKET is required. Usage: make deploy CODE_BUCKET=<bucket>"; exit 1; fi
	cd scripts && ./deploy.sh \
	  --code-bucket "$(CODE_BUCKET)" \
	  $(if $(ENV),--env "$(ENV)") \
	  $(if $(REGION),--region "$(REGION)") \
	  $(if $(VPC_ID),--vpc-id "$(VPC_ID)") \
	  $(if $(SUBNETS),--subnets "$(SUBNETS)") \
	  $(if $(STUDENT_KEY),--student-key "$(STUDENT_KEY)") \
	  $(if $(filter true,$(SKIP_TEST)),--skip-test) \
	  $(if $(filter true,$(SKIP_ECS)),--skip-ecs)

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
