#!/bin/bash

# 03_deploy_lambda_b.sh -- Package and deploy Lambda B (scanner engine).
#
# Usage:
#   ./03_deploy_lambda_b.sh
#
# Environment variable overrides:
#   PROJECT_NAME, ENVIRONMENT, AWS_REGION, CODE_BUCKET, SKIP_TEST

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_NAME="${PROJECT_NAME:-sast-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
LAMBDA_FUNCTION_NAME="${PROJECT_NAME}-${ENVIRONMENT}-scanner"
CODE_BUCKET="${CODE_BUCKET:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LAMBDA_B_DIR="$PROJECT_ROOT/lambda_b"
BUILD_DIR="/tmp/lambda_b_build"

echo -e "${GREEN}Starting Lambda B deployment (scanner engine)${NC}"
echo "Project:   $PROJECT_NAME"
echo "Env:       $ENVIRONMENT"
echo "Region:    $AWS_REGION"
echo "Function:  $LAMBDA_FUNCTION_NAME"
echo

check_dependencies() {
    echo -e "${YELLOW}Checking dependencies...${NC}"

    local missing_tools=()

    if ! command -v aws &> /dev/null; then
        missing_tools+=("aws-cli")
    fi

    if ! command -v zip &> /dev/null; then
        missing_tools+=("zip")
    fi

    if ! command -v python3 &> /dev/null; then
        missing_tools+=("python3")
    fi

    if [ ${#missing_tools[@]} -ne 0 ]; then
        echo -e "${RED}Missing required tools: ${missing_tools[*]}${NC}"
        exit 1
    fi

    if ! aws sts get-caller-identity &> /dev/null; then
        echo -e "${RED}AWS credentials not configured or invalid${NC}"
        exit 1
    fi

    echo -e "${GREEN}Dependencies check passed${NC}"
}

prepare_build_dir() {
    echo -e "${YELLOW}Preparing build directory...${NC}"

    rm -rf "$BUILD_DIR"
    mkdir -p "$BUILD_DIR"

    cp -r "$LAMBDA_B_DIR"/* "$BUILD_DIR/"

    # Remove files not needed in Lambda zip
    rm -f "$BUILD_DIR/Dockerfile"
    rm -f "$BUILD_DIR/ecs_handler.py"

    echo -e "${GREEN}Build directory prepared${NC}"
}

install_dependencies() {
    echo -e "${YELLOW}Installing Python dependencies...${NC}"

    cd "$BUILD_DIR"

    if [ ! -f "requirements.txt" ]; then
        echo -e "${RED}requirements.txt not found${NC}"
        exit 1
    fi

    # Install dependencies into current directory
    # boto3/botocore are pre-installed in the Lambda runtime but pinned here for version consistency
    python3 -m pip install --target . -r requirements.txt --upgrade

    # Remove unnecessary files to reduce package size
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name "test" -exec rm -rf {} + 2>/dev/null || true

    echo -e "${GREEN}Dependencies installed${NC}"
}

create_deployment_package() {
    echo -e "${YELLOW}Creating deployment package...${NC}"

    cd "$BUILD_DIR"

    local zip_file="/tmp/lambda_b_deployment.zip"
    rm -f "$zip_file"

    zip -r "$zip_file" . -x "*.git*" "*.DS_Store*" "*.pyc" "__pycache__/*"

    local file_size
    file_size=$(du -h "$zip_file" | cut -f1)
    echo "Package size: $file_size"

    local size_bytes
    size_bytes=$(stat -c%s "$zip_file" 2>/dev/null || stat -f%z "$zip_file")
    local max_size=$(( 250 * 1024 * 1024 ))

    if [ "$size_bytes" -gt "$max_size" ]; then
        echo -e "${YELLOW}WARNING: Package is large ($file_size), S3 deployment may be needed${NC}"
    fi

    echo "Package path: $zip_file"
    echo -e "${GREEN}Deployment package created${NC}"
}

check_lambda_function() {
    echo -e "${YELLOW}Checking Lambda function...${NC}"

    if aws lambda get-function --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION" &>/dev/null; then
        echo "Lambda function $LAMBDA_FUNCTION_NAME exists"
        return 0
    else
        echo "Lambda function $LAMBDA_FUNCTION_NAME not found, deploy infrastructure first"
        return 1
    fi
}

update_lambda_function() {
    echo -e "${YELLOW}Updating Lambda function code...${NC}"

    local zip_file="/tmp/lambda_b_deployment.zip"
    local size_bytes
    size_bytes=$(stat -c%s "$zip_file" 2>/dev/null || stat -f%z "$zip_file")
    local direct_limit=$(( 50 * 1024 * 1024 ))

    if [ "$size_bytes" -gt "$direct_limit" ]; then
        echo "Package exceeds 50MB, deploying via S3..."
        update_lambda_via_s3 "$zip_file"
    else
        echo "Package is under 50MB, uploading directly..."
        aws lambda update-function-code \
            --function-name "$LAMBDA_FUNCTION_NAME" \
            --zip-file "fileb://$zip_file" \
            --region "$AWS_REGION" > /dev/null

        # Sync to CODE_BUCKET so CloudFormation re-deployments stay in sync.
        if [[ -n "$CODE_BUCKET" ]]; then
            echo "Syncing package to CODE_BUCKET: s3://$CODE_BUCKET/lambda_b.zip"
            aws s3 cp "$zip_file" "s3://$CODE_BUCKET/lambda_b.zip" --region "$AWS_REGION"
        fi
    fi

    echo -e "${GREEN}Lambda function code updated${NC}"
}

update_lambda_via_s3() {
    local zip_file="$1"
    local s3_bucket="${PROJECT_NAME}-${ENVIRONMENT}-deployments"
    local s3_key="lambda_b/$(date +%Y%m%d-%H%M%S)/lambda_b.zip"

    echo "Uploading package to S3..."

    if ! aws s3api head-bucket --bucket "$s3_bucket" --region "$AWS_REGION" 2>/dev/null; then
        echo "Creating deployment bucket: $s3_bucket"
        aws s3api create-bucket --bucket "$s3_bucket" --region "$AWS_REGION"
    fi

    aws s3 cp "$zip_file" "s3://$s3_bucket/$s3_key" --region "$AWS_REGION"

    aws lambda update-function-code \
        --function-name "$LAMBDA_FUNCTION_NAME" \
        --s3-bucket "$s3_bucket" \
        --s3-key "$s3_key" \
        --region "$AWS_REGION" > /dev/null

    echo "S3 update complete"

    # Sync to CODE_BUCKET so CloudFormation re-deployments pick up the latest code.
    if [[ -n "$CODE_BUCKET" ]]; then
        echo "Syncing package to CODE_BUCKET: s3://$CODE_BUCKET/lambda_b.zip"
        aws s3 cp "$zip_file" "s3://$CODE_BUCKET/lambda_b.zip" --region "$AWS_REGION"
    fi
}

update_lambda_configuration() {
    echo -e "${YELLOW}Updating Lambda configuration...${NC}"

    local current_timeout
    current_timeout=$(aws lambda get-function-configuration \
        --function-name "$LAMBDA_FUNCTION_NAME" \
        --region "$AWS_REGION" \
        --query 'Timeout' --output text)

    local current_memory
    current_memory=$(aws lambda get-function-configuration \
        --function-name "$LAMBDA_FUNCTION_NAME" \
        --region "$AWS_REGION" \
        --query 'MemorySize' --output text)

    echo "Current config - timeout: ${current_timeout}s, memory: ${current_memory}MB"

    local recommended_timeout=900
    local recommended_memory=3008

    if [ "$current_timeout" -lt "$recommended_timeout" ] || [ "$current_memory" -lt "$recommended_memory" ]; then
        echo "Updating configuration for optimal scan performance..."
        aws lambda update-function-configuration \
            --function-name "$LAMBDA_FUNCTION_NAME" \
            --timeout "$recommended_timeout" \
            --memory-size "$recommended_memory" \
            --region "$AWS_REGION" > /dev/null
        echo "Configuration updated - timeout: ${recommended_timeout}s, memory: ${recommended_memory}MB"
    else
        echo "Configuration is already optimal"
    fi

    echo -e "${GREEN}Lambda configuration updated${NC}"
}

test_lambda_function() {
    echo -e "${YELLOW}Testing Lambda function...${NC}"

    local test_event='{
        "Records": [
            {
                "messageId": "test-message-id",
                "body": "{\"scan_id\": \"test-scan-123\", \"code\": \"print(\\\"hello world\\\")\", \"language\": \"python\", \"student_id\": \"test-student\"}"
            }
        ]
    }'

    echo "Sending test event..."
    local response
    response=$(aws lambda invoke \
        --function-name "$LAMBDA_FUNCTION_NAME" \
        --payload "$test_event" \
        --region "$AWS_REGION" \
        /tmp/lambda_b_test_response.json 2>&1)

    if echo "$response" | grep -q "StatusCode.*200"; then
        echo -e "${GREEN}Lambda function test passed${NC}"

        if [ -f "/tmp/lambda_b_test_response.json" ]; then
            echo "Response:"
            cat /tmp/lambda_b_test_response.json | jq . 2>/dev/null || cat /tmp/lambda_b_test_response.json
        fi
    else
        echo -e "${RED}Lambda function test failed${NC}"
        echo "$response"
        return 1
    fi
}

cleanup() {
    echo -e "${YELLOW}Cleaning up temporary files...${NC}"
    rm -rf "$BUILD_DIR"
    rm -f "/tmp/lambda_b_deployment.zip"
    rm -f "/tmp/lambda_b_test_response.json"
    echo -e "${GREEN}Cleanup complete${NC}"
}

main() {
    echo -e "${GREEN}=== Lambda B deployment started ===${NC}"

    check_dependencies

    if ! check_lambda_function; then
        echo -e "${RED}Run infrastructure deployment script first to create the Lambda function${NC}"
        exit 1
    fi

    prepare_build_dir
    install_dependencies
    create_deployment_package
    update_lambda_function
    update_lambda_configuration

    if [ "${SKIP_TEST}" != "true" ]; then
        test_lambda_function
    fi

    cleanup

    echo
    echo -e "${GREEN}Lambda B deployment complete${NC}"
    echo -e "Function: ${GREEN}$LAMBDA_FUNCTION_NAME${NC}"
    echo -e "Region:   ${GREEN}$AWS_REGION${NC}"
    echo
    echo "Tips:"
    echo "- View live logs: aws logs tail /aws/lambda/$LAMBDA_FUNCTION_NAME --follow"
    echo "- Monitor metrics and errors in the AWS console"
    echo "- Verify the SQS trigger is correctly configured"
}

trap cleanup ERR

main "$@"
