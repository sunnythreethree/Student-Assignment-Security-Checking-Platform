#!/bin/bash

# 04_build_ecs_image.sh -- Build and push the ECS Fargate scanner Docker image.
#
# Builds an image containing Bandit + Semgrep for use by ECS Fargate tasks.
# The image is pushed to ECR and referenced by the ECS task definition.
#
# Usage:
#   ./04_build_ecs_image.sh
#
# Environment variable overrides:
#   PROJECT_NAME, ENVIRONMENT, AWS_REGION, IMAGE_TAG, SKIP_TEST, KEEP_LOCAL_IMAGES

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_NAME="${PROJECT_NAME:-sast-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REPOSITORY_NAME="${PROJECT_NAME}-${ENVIRONMENT}-scanner"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY_NAME}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LAMBDA_B_DIR="$PROJECT_ROOT/lambda_b"

echo -e "${GREEN}Building and pushing ECS scanner image${NC}"
echo "Project:    $PROJECT_NAME"
echo "Env:        $ENVIRONMENT"
echo "AWS Account: $AWS_ACCOUNT_ID"
echo "ECR Repo:   $ECR_URI"
echo "Image Tag:  $IMAGE_TAG"
echo

check_dependencies() {
    echo -e "${YELLOW}Checking dependencies...${NC}"

    local missing_tools=()

    if ! command -v docker &> /dev/null; then
        missing_tools+=("docker")
    fi

    if ! command -v aws &> /dev/null; then
        missing_tools+=("aws-cli")
    fi

    if [ ${#missing_tools[@]} -ne 0 ]; then
        echo -e "${RED}Missing required tools: ${missing_tools[*]}${NC}"
        echo "Install Docker and AWS CLI before running this script"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        echo -e "${RED}Docker daemon is not running${NC}"
        exit 1
    fi

    if ! aws sts get-caller-identity &> /dev/null; then
        echo -e "${RED}AWS credentials not configured or invalid${NC}"
        exit 1
    fi

    echo -e "${GREEN}Dependencies check passed${NC}"
}

ecr_login() {
    echo -e "${YELLOW}Logging in to ECR...${NC}"

    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

    echo -e "${GREEN}ECR login successful${NC}"
}

ensure_ecr_repository() {
    echo -e "${YELLOW}Checking ECR repository...${NC}"

    if ! aws ecr describe-repositories --repository-names "$ECR_REPOSITORY_NAME" --region "$AWS_REGION" &>/dev/null; then
        echo "Creating ECR repository: $ECR_REPOSITORY_NAME"
        aws ecr create-repository \
            --repository-name "$ECR_REPOSITORY_NAME" \
            --region "$AWS_REGION" \
            --image-scanning-configuration scanOnPush=false \
            --image-tag-mutability MUTABLE > /dev/null

        local lifecycle_policy='{
            "rules": [
                {
                    "rulePriority": 1,
                    "description": "Keep last 10 images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": 10
                    },
                    "action": {
                        "type": "expire"
                    }
                }
            ]
        }'

        aws ecr put-lifecycle-policy \
            --repository-name "$ECR_REPOSITORY_NAME" \
            --region "$AWS_REGION" \
            --lifecycle-policy-text "$lifecycle_policy" > /dev/null
    else
        echo "ECR repository $ECR_REPOSITORY_NAME already exists"
    fi

    echo -e "${GREEN}ECR repository ready${NC}"
}

build_docker_image() {
    echo -e "${YELLOW}Building Docker image...${NC}"

    cd "$LAMBDA_B_DIR"

    docker buildx build \
        --platform linux/amd64 \
        --tag "$ECR_REPOSITORY_NAME:$IMAGE_TAG" \
        --tag "$ECR_URI:$IMAGE_TAG" \
        --build-arg BUILD_DATE="$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
        --build-arg VERSION="$IMAGE_TAG" \
        --build-arg PROJECT_NAME="$PROJECT_NAME" \
        --build-arg ENVIRONMENT="$ENVIRONMENT" \
        --load \
        .

    echo -e "${GREEN}Docker image built${NC}"

    echo "Image details:"
    docker images "$ECR_REPOSITORY_NAME:$IMAGE_TAG" --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"
}

test_docker_image() {
    echo -e "${YELLOW}Testing Docker image...${NC}"

    echo "Testing scanner tool availability..."
    docker run --rm "$ECR_REPOSITORY_NAME:$IMAGE_TAG" python -c "
import subprocess
try:
    subprocess.run(['bandit', '--version'], check=True, capture_output=True)
    subprocess.run(['semgrep', '--version'], check=True, capture_output=True)
    print('Scanner tools OK')
except Exception as e:
    print(f'Scanner tools test failed: {e}')
    exit(1)
"

    echo "Testing Python module imports..."
    docker run --rm "$ECR_REPOSITORY_NAME:$IMAGE_TAG" python -c "
try:
    import boto3
    import scanner
    import result_parser
    import s3_writer
    print('Python module imports OK')
except Exception as e:
    print(f'Python module import failed: {e}')
    exit(1)
"

    echo -e "${GREEN}Docker image tests passed${NC}"
}

push_docker_image() {
    echo -e "${YELLOW}Pushing image to ECR...${NC}"

    docker push "$ECR_URI:$IMAGE_TAG"

    if [ "$IMAGE_TAG" = "latest" ]; then
        local timestamp_tag
        timestamp_tag="$(date +%Y%m%d-%H%M%S)"
        docker tag "$ECR_URI:$IMAGE_TAG" "$ECR_URI:$timestamp_tag"
        docker push "$ECR_URI:$timestamp_tag"
        echo "Also pushed timestamp tag: $timestamp_tag"
    fi

    echo -e "${GREEN}Image push complete${NC}"
}

cleanup_local_images() {
    echo -e "${YELLOW}Cleaning up...${NC}"

    if [ "${KEEP_LOCAL_IMAGES}" != "true" ]; then
        docker image prune -f &>/dev/null || true
        echo "Local images retained, remove manually if needed:"
        echo "  docker rmi $ECR_REPOSITORY_NAME:$IMAGE_TAG"
        echo "  docker rmi $ECR_URI:$IMAGE_TAG"
    else
        echo "Keeping local images (KEEP_LOCAL_IMAGES=true)"
    fi

    echo -e "${GREEN}Cleanup complete${NC}"
}

update_ecs_task_definition() {
    local stack_name="${PROJECT_NAME}-ecs"

    if ! aws cloudformation describe-stacks \
            --stack-name "$stack_name" --region "$AWS_REGION" &>/dev/null; then
        echo "ECS CloudFormation stack '$stack_name' not found — skipping task definition update"
        return 0
    fi

    echo -e "${YELLOW}Updating ECS task definition with new image URI...${NC}"
    aws cloudformation deploy \
        --stack-name "$stack_name" \
        --region "$AWS_REGION" \
        --use-previous-template \
        --parameter-overrides "ScannerImageUri=${ECR_URI}:${IMAGE_TAG}" \
        --no-fail-on-empty-changeset \
        --capabilities CAPABILITY_IAM
    echo -e "${GREEN}ECS task definition updated → ${ECR_URI}:${IMAGE_TAG}${NC}"
}

# After updating the ECS stack, sync Lambda B's ECS-related env vars so it can
# actually launch Fargate tasks:
#   ECS_TASK_DEFINITION  → family name (no revision), always picks latest
#   ECS_SUBNETS          → default-VPC subnets (auto-detected)
#   ECS_SECURITY_GROUPS  → security group from the ECS CloudFormation stack
update_lambda_b_task_def_env() {
    local lambda_func="${PROJECT_NAME}-${ENVIRONMENT}-scanner"
    local task_def_family="${PROJECT_NAME}-${ENVIRONMENT}-scanner"
    local ecs_stack="${PROJECT_NAME}-ecs"

    echo -e "${YELLOW}Syncing Lambda B ECS env vars...${NC}"

    # --- Auto-detect default VPC subnets ---
    local vpc_id subnet_ids sg_id
    vpc_id=$(aws ec2 describe-vpcs --region "$AWS_REGION" \
        --filters "Name=isDefault,Values=true" \
        --query "Vpcs[0].VpcId" --output text 2>/dev/null || true)

    if [[ -n "$vpc_id" && "$vpc_id" != "None" ]]; then
        subnet_ids=$(aws ec2 describe-subnets --region "$AWS_REGION" \
            --filters "Name=vpc-id,Values=$vpc_id" "Name=defaultForAz,Values=true" \
            --query "Subnets[:2].SubnetId" --output text 2>/dev/null \
            | tr '[:space:]' ',' | sed 's/,$//' || true)
        echo "Subnets: $subnet_ids"
    fi

    # --- Get ECS cluster name + security group from CloudFormation stack output ---
    local cluster_name
    cluster_name=$(aws cloudformation describe-stacks \
        --stack-name "$ecs_stack" --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='ECSClusterName'].OutputValue" \
        --output text 2>/dev/null || true)
    echo "Cluster: $cluster_name"

    sg_id=$(aws cloudformation describe-stacks \
        --stack-name "$ecs_stack" --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='ECSSecurityGroupId'].OutputValue" \
        --output text 2>/dev/null || true)
    echo "Security group: $sg_id"

    # --- Fetch current Lambda B env vars (to avoid clobbering other vars) ---
    local current_env
    current_env=$(aws lambda get-function-configuration \
        --function-name "$lambda_func" \
        --region "$AWS_REGION" \
        --query 'Environment.Variables' \
        --output json 2>/dev/null || echo '{}')

    # --- Merge updates into existing env vars ---
    local new_env
    new_env=$(python3 - <<PYEOF
import json
env = json.loads('''$current_env''')
env['ECS_TASK_DEFINITION'] = '$task_def_family'
if '$subnet_ids':
    env['ECS_SUBNETS'] = '$subnet_ids'
if '$sg_id' and '$sg_id' != 'None':
    env['ECS_SECURITY_GROUPS'] = '$sg_id'
if '$cluster_name' and '$cluster_name' != 'None':
    env['ECS_CLUSTER_NAME'] = '$cluster_name'
print(json.dumps({'Variables': env}))
PYEOF
)

    aws lambda update-function-configuration \
        --function-name "$lambda_func" \
        --region "$AWS_REGION" \
        --environment "$new_env" > /dev/null

    echo -e "${GREEN}Lambda B synced: task_def=$task_def_family subnets=$subnet_ids sg=$sg_id${NC}"
}

show_deployment_info() {
    echo
    echo -e "${GREEN}=== Image Info ===${NC}"
    echo -e "ECR URI: ${GREEN}$ECR_URI:$IMAGE_TAG${NC}"
    echo
    echo "Local test command:"
    echo "docker run --rm -e SCAN_ID=test -e STUDENT_ID=test -e LANGUAGE=python -e CODE_CONTENT='print(\"hello\")' $ECR_URI:$IMAGE_TAG"
    echo
}

main() {
    echo -e "${GREEN}=== ECS image build started ===${NC}"

    check_dependencies
    ecr_login
    ensure_ecr_repository
    build_docker_image

    if [ "${SKIP_TEST}" != "true" ]; then
        test_docker_image
    fi

    push_docker_image
    update_ecs_task_definition
    update_lambda_b_task_def_env
    cleanup_local_images
    show_deployment_info

    echo
    echo -e "${GREEN}ECS image build and push complete${NC}"
}

trap 'echo -e "${RED}Build failed${NC}"; exit 1' ERR

main "$@"
