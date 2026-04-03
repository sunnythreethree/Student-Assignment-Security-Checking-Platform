# Multi-stage build for security scanning tools
# Stage 1: Build Semgrep
FROM python:3.12-slim as semgrep-builder

# Install system dependencies for building
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Semgrep
RUN pip install --no-cache-dir semgrep>=1.45.0

# Stage 2: Build Bandit
FROM python:3.12-slim as bandit-builder

# Install Bandit
RUN pip install --no-cache-dir bandit[toml]>=1.7.5

# Stage 3: Final runtime image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# Create non-root user for security
RUN groupadd -r scanner && useradd -r -g scanner scanner

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy tools from builder stages
COPY --from=semgrep-builder /usr/local/bin/semgrep /usr/local/bin/semgrep
COPY --from=semgrep-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=bandit-builder /usr/local/bin/bandit /usr/local/bin/bandit
COPY --from=bandit-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Install additional Python dependencies + Lambda Runtime Interface Client
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    awslambdaric \
    && rm /tmp/requirements.txt

# Lambda requires app code in /var/task
WORKDIR /var/task

# Copy application code
COPY . .

# Pre-download semgrep rules into the image so scans don't need outbound
# internet access at runtime (critical for VPC deployments without NAT).
# `--dump-config` fetches the ruleset and writes it to stdout as YAML.
RUN mkdir -p /semgrep-rules && \
    semgrep --config=p/owasp-top-ten --dump-config > /semgrep-rules/rules.yaml && \
    chmod 644 /semgrep-rules/rules.yaml

# Create temp directory for scanning (writable by all users)
RUN mkdir -p /tmp/scanner && chmod 777 /tmp/scanner

# Change ownership to scanner user
RUN chown -R scanner:scanner /var/task

# Switch to non-root user
USER scanner

# Set up PATH to ensure tools are accessible
ENV PATH="/usr/local/bin:${PATH}"

# Health check (used by ECS; Lambda ignores it)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import subprocess; subprocess.run(['bandit', '--version'], check=True); subprocess.run(['semgrep', '--version'], check=True)" || exit 1

# Lambda entry point via awslambdaric
# ECS tasks override this with: CMD ["python", "ecs_handler.py"]
ENTRYPOINT ["/usr/local/bin/python", "-m", "awslambdaric"]
CMD ["handler.lambda_handler"]
