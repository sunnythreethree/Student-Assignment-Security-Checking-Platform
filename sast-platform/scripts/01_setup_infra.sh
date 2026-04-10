#!/usr/bin/env bash
# 01_setup_infra.sh — Deploy all CloudFormation stacks in dependency order.
#
# Usage:
#   ./01_setup_infra.sh [OPTIONS]
#
# Required:
#   --code-bucket   S3 bucket where lambda_a.zip / lambda_b.zip are uploaded
#
# Optional:
#   --project       Project name prefix        (default: sast-platform)
#   --env           Environment                (default: dev)
#   --region        AWS region                 (default: us-east-1)
#   --vpc-id        VPC ID for ECS tasks       (skip ECS stack if omitted)
#   --subnets       Comma-separated subnet IDs (required when --vpc-id is set)
#   --scanner-image ECR image URI for ECS scanner
#                   (default: placeholder — update after running 04_build_ecs_image.sh)
#
# Environment variable equivalents (override flags):
#   CODE_BUCKET, PROJECT_NAME, ENVIRONMENT, AWS_REGION, VPC_ID, SUBNET_IDS, SCANNER_IMAGE
#
# Deployment order:
#   1. s3          (no upstream deps)
#   2. dynamodb    (no upstream deps)
#   3. sqs         (no upstream deps)
#   4. lambda_a    (needs s3, dynamodb, sqs outputs)
#   5. lambda_b    (needs s3, dynamodb, sqs outputs)
#   6. ecs         (needs s3, dynamodb — OPTIONAL, skipped if --vpc-id not set)
#   7. cloudwatch  (needs all above stacks)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")/infrastructure"

# ── Defaults ──────────────────────────────────────────────────────────────────
PROJECT_NAME="${PROJECT_NAME:-sast-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
CODE_BUCKET="${CODE_BUCKET:-}"
VPC_ID="${VPC_ID:-}"
SUBNET_IDS="${SUBNET_IDS:-}"
SCANNER_IMAGE="${SCANNER_IMAGE:-}"

# ── Argument parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)       PROJECT_NAME="$2";   shift 2 ;;
    --env)           ENVIRONMENT="$2";    shift 2 ;;
    --region)        AWS_REGION="$2";     shift 2 ;;
    --code-bucket)   CODE_BUCKET="$2";    shift 2 ;;
    --vpc-id)        VPC_ID="$2";         shift 2 ;;
    --subnets)       SUBNET_IDS="$2";     shift 2 ;;
    --scanner-image) SCANNER_IMAGE="$2";  shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Validation ─────────────────────────────────────────────────────────────────
if [[ -z "$CODE_BUCKET" ]]; then
  echo "ERROR: --code-bucket is required (S3 bucket containing lambda_a.zip and lambda_b.zip)"
  exit 1
fi

if [[ -n "$VPC_ID" && -z "$SUBNET_IDS" ]]; then
  echo "ERROR: --subnets is required when --vpc-id is set"
  exit 1
fi

# ── Stack name convention (must match cloudwatch.yaml parameter defaults) ──────
STACK_S3="${PROJECT_NAME}-s3"
STACK_DYNAMODB="${PROJECT_NAME}-dynamodb"          # cloudwatch default: sast-platform-dynamodb (adjusted below)
STACK_SQS="${PROJECT_NAME}-sqs"                    # cloudwatch default: sast-sqs — see note
STACK_LAMBDA_A="${PROJECT_NAME}-lambda-a"
STACK_LAMBDA_B="${PROJECT_NAME}-lambda-b"
STACK_ECS="${PROJECT_NAME}-ecs"
STACK_CLOUDWATCH="${PROJECT_NAME}-cloudwatch"

# Shared resource names (consistent across stacks)
# Append the AWS account ID so bucket names are globally unique across
# Learner Lab sessions (S3 bucket names share a single global namespace).
TABLE_NAME="ScanResults"
_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
REPORT_BUCKET="${PROJECT_NAME}-reports-${ENVIRONMENT}-${_ACCOUNT_ID}"
FRONTEND_BUCKET="${PROJECT_NAME}-frontend-${ENVIRONMENT}-${_ACCOUNT_ID}"

# ── Helpers ────────────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✓ $*"; }
fail() { echo "[$(date '+%H:%M:%S')] ✗ $*" >&2; exit 1; }

deploy_stack() {
  local stack_name="$1"
  local template_file="$2"
  shift 2
  local params=("$@")

  log "Deploying stack: $stack_name"
  log "  Template: $template_file"

  local param_args=()
  for p in "${params[@]}"; do
    param_args+=(ParameterKey="${p%%=*}",ParameterValue="${p#*=}")
  done

  # Check if stack already exists
  local stack_status
  stack_status=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" --region "$AWS_REGION" \
    --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST")

  if [[ "$stack_status" == "DOES_NOT_EXIST" ]]; then
    # Create new stack (bypasses changeset hooks in Learner Lab)
    aws cloudformation create-stack \
      --stack-name "$stack_name" \
      --template-body "file://$template_file" \
      --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
      --region "$AWS_REGION" \
      $([[ ${#param_args[@]} -gt 0 ]] && echo "--parameters ${param_args[*]}") \
      > /dev/null
    aws cloudformation wait stack-create-complete \
      --stack-name "$stack_name" --region "$AWS_REGION"
  else
    # Update existing stack
    local update_out
    if update_out=$(aws cloudformation update-stack \
      --stack-name "$stack_name" \
      --template-body "file://$template_file" \
      --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
      --region "$AWS_REGION" \
      $([[ ${#param_args[@]} -gt 0 ]] && echo "--parameters ${param_args[*]}") \
      2>&1); then
      aws cloudformation wait stack-update-complete \
        --stack-name "$stack_name" --region "$AWS_REGION"
    else
      # No changes = not an error
      if echo "$update_out" | grep -q "No updates are to be performed"; then
        log "  No changes to stack: $stack_name"
      else
        echo "$update_out" >&2
        fail "Stack update failed: $stack_name"
      fi
    fi
  fi

  ok "Stack deployed: $stack_name"
}

get_output() {
  local stack_name="$1"
  local output_key="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$output_key'].OutputValue" \
    --output text
}

# ── Print config ───────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       SAST Platform — Infrastructure Setup           ║"
echo "╠══════════════════════════════════════════════════════╣"
printf  "║  Project:    %-38s ║\n" "$PROJECT_NAME"
printf  "║  Environment:%-38s ║\n" "$ENVIRONMENT"
printf  "║  Region:     %-38s ║\n" "$AWS_REGION"
printf  "║  Code Bucket:%-38s ║\n" "$CODE_BUCKET"
if [[ -n "$VPC_ID" ]]; then
printf  "║  VPC:        %-38s ║\n" "$VPC_ID"
printf  "║  Subnets:    %-38s ║\n" "${SUBNET_IDS:0:38}"
else
printf  "║  ECS stack:  %-38s ║\n" "SKIPPED (no --vpc-id)"
fi
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. S3 ──────────────────────────────────────────────────────────────────────
deploy_stack "$STACK_S3" "$INFRA_DIR/s3.yaml" \
  "ReportBucketName=$REPORT_BUCKET" \
  "FrontendBucketName=$FRONTEND_BUCKET"

# ── 2. DynamoDB ────────────────────────────────────────────────────────────────
deploy_stack "$STACK_DYNAMODB" "$INFRA_DIR/dynamodb.yaml" \
  "TableName=$TABLE_NAME"

# ── 3. SQS ────────────────────────────────────────────────────────────────────
deploy_stack "$STACK_SQS" "$INFRA_DIR/sqs.yaml" \
  "QueueName=${PROJECT_NAME}-scan-queue" \
  "DLQName=${PROJECT_NAME}-scan-dlq"

# Query SQS outputs needed by Lambda stacks
SQS_QUEUE_ARN="$(get_output "$STACK_SQS" ScanQueueArn)"
[[ -z "$SQS_QUEUE_ARN" ]] && fail "Could not read ScanQueueArn from $STACK_SQS"
log "SQS Queue ARN: $SQS_QUEUE_ARN"

# ── 4. Lambda A ────────────────────────────────────────────────────────────────
deploy_stack "$STACK_LAMBDA_A" "$INFRA_DIR/lambda_a.yaml" \
  "CodeBucket=$CODE_BUCKET" \
  "CodeKey=lambda_a.zip" \
  "DynamoDBTableName=$TABLE_NAME" \
  "S3ReportBucket=$REPORT_BUCKET" \
  "SQSStackName=$STACK_SQS"

LAMBDA_A_URL="$(get_output "$STACK_LAMBDA_A" LambdaAApiUrl 2>/dev/null || true)"

# ── 5. Lambda B ────────────────────────────────────────────────────────────────
deploy_stack "$STACK_LAMBDA_B" "$INFRA_DIR/lambda_b.yaml" \
  "ProjectName=$PROJECT_NAME" \
  "Environment=$ENVIRONMENT" \
  "SQSQueueArn=$SQS_QUEUE_ARN" \
  "DynamoDBTableName=$TABLE_NAME" \
  "S3BucketName=$REPORT_BUCKET" \
  "CodeBucket=$CODE_BUCKET" \
  "CodeKey=lambda_b.zip" \
  "ECSClusterName=" \
  "ECSTaskDefinitionArn="

# ── 6. ECS (optional) ─────────────────────────────────────────────────────────
ECS_CLUSTER_NAME=""
ECS_TASK_DEF_ARN=""

if [[ -n "$VPC_ID" ]]; then
  DEFAULT_IMAGE="${PROJECT_NAME}.dkr.ecr.${AWS_REGION}.amazonaws.com/sast-scanner:latest"
  SCANNER_IMAGE="${SCANNER_IMAGE:-$DEFAULT_IMAGE}"

  deploy_stack "$STACK_ECS" "$INFRA_DIR/ecs.yaml" \
    "ProjectName=$PROJECT_NAME" \
    "Environment=$ENVIRONMENT" \
    "VpcId=$VPC_ID" \
    "SubnetIds=$SUBNET_IDS" \
    "ScannerImageUri=$SCANNER_IMAGE" \
    "DynamoDBTableName=$TABLE_NAME" \
    "S3BucketName=$REPORT_BUCKET"

  ECS_CLUSTER_NAME="$(get_output "$STACK_ECS" ECSClusterName)"
  ECS_TASK_DEF_ARN="$(get_output "$STACK_ECS" TaskDefinitionArn)"

  # Update Lambda B with ECS references now that ECS stack exists
  log "Updating Lambda B with ECS cluster reference..."
  deploy_stack "$STACK_LAMBDA_B" "$INFRA_DIR/lambda_b.yaml" \
    "ProjectName=$PROJECT_NAME" \
    "Environment=$ENVIRONMENT" \
    "SQSQueueArn=$SQS_QUEUE_ARN" \
    "DynamoDBTableName=$TABLE_NAME" \
    "S3BucketName=$REPORT_BUCKET" \
    "CodeBucket=$CODE_BUCKET" \
    "CodeKey=lambda_b.zip" \
    "ECSClusterName=$ECS_CLUSTER_NAME" \
    "ECSTaskDefinitionArn=$ECS_TASK_DEF_ARN"
else
  log "Skipping ECS stack (--vpc-id not provided). ECS fallback will be unavailable."
fi

# ── 7. CloudWatch ─────────────────────────────────────────────────────────────
deploy_stack "$STACK_CLOUDWATCH" "$INFRA_DIR/cloudwatch.yaml" \
  "ProjectName=$PROJECT_NAME" \
  "Environment=$ENVIRONMENT" \
  "SQSStackName=$STACK_SQS" \
  "DynamoDBStackName=$STACK_DYNAMODB" \
  "LambdaAFunctionName=sast-lambda-a"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║               Deployment Complete                    ║"
echo "╠══════════════════════════════════════════════════════╣"

# Re-query Lambda A URL (may have been set after deploy)
LAMBDA_A_URL="$(get_output "$STACK_LAMBDA_A" LambdaAApiUrl 2>/dev/null || true)"
SQS_URL="$(get_output "$STACK_SQS" ScanQueueUrl)"
DLQ_URL="$(get_output "$STACK_SQS" DeadLetterQueueUrl)"

printf "║  Lambda A URL:   %-34s ║\n" "${LAMBDA_A_URL:-<check console>}"
printf "║  Report Bucket:  %-34s ║\n" "$REPORT_BUCKET"
printf "║  Frontend Bucket:%-34s ║\n" "$FRONTEND_BUCKET"
printf "║  DynamoDB Table: %-34s ║\n" "$TABLE_NAME"
printf "║  SQS Queue URL:  %-34s ║\n" "${SQS_URL:-<check console>}"
printf "║  DLQ URL:        %-34s ║\n" "${DLQ_URL:-<check console>}"
if [[ -n "$ECS_CLUSTER_NAME" ]]; then
printf "║  ECS Cluster:    %-34s ║\n" "$ECS_CLUSTER_NAME"
fi
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Next step: run 02_deploy_lambda_a.sh                ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
