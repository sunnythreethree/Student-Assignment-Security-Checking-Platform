# Multi-stage build for security scanning tools
# Stage 1: Install bandit + semgrep together so pip resolves shared deps
#           (separate stages caused typing_extensions version conflict)
FROM python:3.12-slim as tools-builder

RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install both tools in one pip call so pip picks compatible dep versions
RUN pip install --no-cache-dir \
    "bandit[toml]>=1.7.5" \
    "semgrep>=1.45.0"

# Download comprehensive rules at build time (no internet needed at runtime).
# Stores each ruleset as a separate yaml; scanner.py uses the directory.
RUN mkdir -p /semgrep-rules && \
    curl -sSf "https://semgrep.dev/c/p/javascript"    -o /semgrep-rules/javascript.yaml && \
    curl -sSf "https://semgrep.dev/c/p/owasp-top-ten" -o /semgrep-rules/owasp.yaml && \
    curl -sSf "https://semgrep.dev/c/p/secrets"       -o /semgrep-rules/secrets.yaml && \
    echo "Rules downloaded:" && ls -lh /semgrep-rules/

# Stage 2: Final runtime image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# Create non-root user for security (with home dir so semgrep can write state)
RUN groupadd -r scanner && useradd -r -g scanner -m -d /home/scanner scanner

# Install system dependencies + Node.js (for teacher's scanner.js)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy tools and rules from builder
COPY --from=tools-builder /usr/local/bin/semgrep    /usr/local/bin/semgrep
COPY --from=tools-builder /usr/local/bin/pysemgrep  /usr/local/bin/pysemgrep
COPY --from=tools-builder /usr/local/bin/bandit     /usr/local/bin/bandit
COPY --from=tools-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=tools-builder /semgrep-rules /semgrep-rules

# Install additional Python dependencies + Lambda Runtime Interface Client
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    awslambdaric \
    && rm /tmp/requirements.txt

# Lambda requires app code in /var/task
WORKDIR /var/task

# Copy application code
COPY . .

# Create temp directory for scanning (writable by all users)
RUN mkdir -p /tmp/scanner && chmod 777 /tmp/scanner

# Change ownership to scanner user
RUN chown -R scanner:scanner /var/task

# Switch to non-root user
USER scanner

# Set up PATH to ensure tools are accessible
ENV PATH="/usr/local/bin:${PATH}"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import subprocess; subprocess.run(['bandit', '--version'], check=True); subprocess.run(['semgrep', '--version'], check=True); subprocess.run(['node', '--version'], check=True)" || exit 1

# Lambda entry point via awslambdaric
# ECS tasks override this with: CMD ["python", "ecs_handler.py"]
ENTRYPOINT ["/usr/local/bin/python", "-m", "awslambdaric"]
CMD ["handler.lambda_handler"]
