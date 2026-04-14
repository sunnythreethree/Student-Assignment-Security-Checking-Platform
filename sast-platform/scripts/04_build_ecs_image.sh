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

    docker build \
        --tag "$ECR_REPOSITORY_NAME:$IMAGE_TAG" \
        --tag "$ECR_URI:$IMAGE_TAG" \
        --build-arg BUILD_DATE="$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
        --build-arg VERSION="$IMAGE_TAG" \
        --build-arg PROJECT_NAME="$PROJECT_NAME" \
        --build-arg ENVIRONMENT="$ENVIRONMENT" \
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

show_deployment_info() {
    echo
    echo -e "${GREEN}=== Image Info ===${NC}"
    echo -e "ECR URI: ${GREEN}$ECR_URI:$IMAGE_TAG${NC}"
    echo -e "Image size: $(docker images "$ECR_URI:$IMAGE_TAG" --format "{{.Size}}")"
    echo
    echo "Next steps:"
    echo "1. Update the ECS task definition image URI"
    echo "2. Set the ECS_TASK_DEFINITION environment variable in Lambda B"
    echo "3. Ensure the ECS service has permission to pull from ECR"
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
    cleanup_local_images
    show_deployment_info

    echo
    echo -e "${GREEN}ECS image build and push complete${NC}"
}

trap 'echo -e "${RED}Build failed${NC}"; exit 1' ERR

main "$@"
