"""
Lambda B Main Handler
Responsibilities:
1. Extract scan task information from SQS messages
2. Call scanning engine for code security analysis
3. Parse and standardize scan results
4. Write results to S3 and update DynamoDB status
"""
import json
import os
import shutil
import logging
import boto3
from typing import Dict, Any, List
from botocore.exceptions import ClientError

# Skip binary and env-var checks when running under pytest (unit tests mock
# subprocess.run and boto3; the binaries and real env vars are not present).
import sys as _sys
_TESTING = "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("CI") == "true"

if not _TESTING:
    # Check scanner binaries; accept /var/task/bin/bandit for zip-based deployments.
    # shutil.which() is checked first so unit-test mocks work correctly —
    # if which() returns a non-None path we trust it without os.path.isfile.
    _bandit_which = shutil.which("bandit")
    _bandit_found = bool(_bandit_which) or any(
        os.path.isfile(p) for p in [
            os.path.join(os.path.dirname(_sys.executable), "bandit"),
            "/var/task/bin/bandit",
        ]
    )
    if not _bandit_found:
        raise RuntimeError("Required scanner binary 'bandit' not found")
    if not shutil.which("semgrep"):
        logging.getLogger(__name__).warning("Optional binary 'semgrep' not found; non-Python scans will fail")

    # ---------------------------------------------------------------------------
    # Startup environment variable validation
    # Raises RuntimeError at import time so Lambda reports Runtime.ImportModuleError
    # with the full list of missing vars — visible in CloudWatch immediately.
    # ---------------------------------------------------------------------------
    _REQUIRED_ENV = ["DYNAMODB_TABLE_NAME", "S3_BUCKET_NAME"]
    _missing_env = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
    if _missing_env:
        raise RuntimeError(f"Missing required environment variables: {_missing_env}")

from scanner import scan_code_with_timeout
from result_parser import normalize_result
from s3_writer import write_scan_result_to_s3, get_s3_bucket_from_env, S3WriteError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Code size threshold for ECS fallback.
# Submissions larger than this are offloaded to ECS Fargate instead of
# being scanned inline, avoiding Lambda timeout/memory limits.
LAMBDA_CODE_SIZE_LIMIT = 250_000  # ~250 KB

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
sqs = boto3.client('sqs')
s3 = boto3.client('s3')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda B main entry point

    Returns the batchItemFailures format required by SQS ReportBatchItemFailures.
    Any record whose messageId appears in the list will be retried by SQS (up to
    MaxReceiveCount times before landing on the DLQ).  Records that are NOT in
    the list are deleted from the queue — SQS treats them as successfully processed.

    Args:
        event: SQS event data
        context: Lambda runtime context

    Returns:
        {"batchItemFailures": [{"itemIdentifier": <messageId>}, ...]}
    """
    logger.info(f"Lambda B started processing SQS event: {json.dumps(event)}")

    records = event.get('Records', [])
    # Collect messageIds that should be retried by SQS.
    failed_item_ids: List[str] = []

    try:
        # Get environment variables
        table_name = os.environ.get('DYNAMODB_TABLE_NAME')
        if not table_name:
            raise ValueError("Environment variable DYNAMODB_TABLE_NAME not set")

        s3_bucket_name = get_s3_bucket_from_env()
        table = dynamodb.Table(table_name)

        logger.info(f"Received {len(records)} SQS messages")

        for record in records:
            message_id  = record['messageId']
            s3_code_key = None  # ensure cleanup is possible even if message parsing fails
            scan_id     = None
            student_id  = None
            try:
                # Parse message
                message_body = json.loads(record['body'])
                scan_id      = message_body['scan_id']
                s3_code_key  = message_body['s3_code_key']
                language     = message_body['language']
                student_id   = message_body['student_id']

                logger.info(f"Started processing scan task - scan_id: {scan_id}, language: {language}")

                # process_scan_request fetches code from S3 internally so that
                # _delete_uploaded_code is guaranteed to run on every exit path,
                # including S3 fetch failures.
                result = process_scan_request(
                    scan_id=scan_id,
                    language=language,
                    student_id=student_id,
                    table=table,
                    s3_bucket_name=s3_bucket_name,
                    s3_code_key=s3_code_key,
                )

                if result['success']:
                    logger.info(f"Scan task completed - scan_id: {scan_id}")
                else:
                    # Tell SQS to retry this message.  On retry, the idempotency
                    # guard in process_scan_request will skip scans already marked
                    # DONE or FAILED, so retrying a permanently-failed scan is safe.
                    failed_item_ids.append(message_id)
                    logger.error(f"Scan task failed - scan_id: {scan_id}, error: {result['error']}")

            except Exception as e:
                error_msg = f"Failed to process SQS message: {str(e)}"
                logger.error(error_msg)
                # Clean up S3 upload if we got far enough to know the key but
                # failed before process_scan_request could handle cleanup itself.
                _delete_uploaded_code(s3_bucket_name, s3_code_key)
                # Mark the scan FAILED in DynamoDB so it doesn't stay PENDING forever
                if scan_id and student_id:
                    try:
                        update_scan_status(table, student_id, scan_id, 'FAILED', error_message=error_msg)
                    except Exception as db_error:
                        logger.error(f"Failed to update FAILED status - scan_id: {scan_id}, error: {str(db_error)}")
                failed_item_ids.append(message_id)

        successful_count = len(records) - len(failed_item_ids)
        logger.info(f"Lambda B processing completed - successful: {successful_count}, failed: {len(failed_item_ids)}")

    except Exception as e:
        # Setup failed (e.g. missing env var) — mark every record as failed so
        # SQS retries the whole batch.
        logger.error(f"Lambda B processing exception: {str(e)}")
        failed_item_ids = [r['messageId'] for r in records]

    return {
        "batchItemFailures": [
            {"itemIdentifier": mid} for mid in failed_item_ids
        ]
    }


def process_scan_request(scan_id: str, language: str, student_id: str,
                        table: Any, s3_bucket_name: str,
                        s3_code_key: str = None) -> Dict[str, Any]:
    """
    Process single scan request.

    Fetches source code from S3 internally so that _delete_uploaded_code is
    guaranteed to run on every exit path — including S3 fetch failures.
    Previously, fetching outside this function meant a fetch error left the
    uploaded object in S3 permanently.
    """
    try:
        # Idempotency guard: atomically claim this scan by flipping PENDING →
        # IN_PROGRESS.  If another Lambda B invocation already claimed it (status
        # is IN_PROGRESS, DONE, or FAILED), the condition fails and we skip.
        # This prevents concurrent duplicate processing when SQS redelivers a
        # message while the first invocation is still running.
        try:
            table.update_item(
                Key={"student_id": student_id, "scan_id": scan_id},
                UpdateExpression="SET #status = :in_progress",
                ConditionExpression="#status = :pending",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":in_progress": "IN_PROGRESS",
                    ":pending":     "PENDING",
                },
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                current = table.get_item(
                    Key={"student_id": student_id, "scan_id": scan_id}
                ).get("Item", {})
                logger.info(
                    "scan_id %s already claimed (status=%s) — skipping duplicate",
                    scan_id, current.get("status", "unknown"),
                )
                return {"success": True, "scan_id": scan_id, "skipped": True}
            raise

        # Non-Python languages (Java, JS, TS, Go, Ruby, C, C++) require Semgrep,
        # which is not bundled in the Lambda zip (too large). Route them to ECS
        # Fargate where the container image has Semgrep installed.
        SEMGREP_LANGUAGES = {'java', 'javascript', 'js', 'typescript', 'go', 'ruby', 'c', 'cpp'}
        if language.lower() in SEMGREP_LANGUAGES:
            logger.info(
                f"Language '{language}' requires Semgrep — routing to ECS Fargate - scan_id: {scan_id}"
            )
            update_scan_status(table, student_id, scan_id, 'ECS_QUEUED')
            ecs_result = handle_ecs_fallback(scan_id, language, student_id, s3_code_key)
            if not ecs_result['success']:
                try:
                    update_scan_status(table, student_id, scan_id, 'FAILED',
                                       error_message=ecs_result['error'])
                except Exception as db_err:
                    logger.error(f"Failed to update FAILED status after ECS launch error - scan_id: {scan_id}, error: {str(db_err)}")
            return ecs_result

        # Step 1: Fetch source code from S3
        logger.info(f"Fetching code from S3 - scan_id: {scan_id}, key: {s3_code_key}")
        code = _fetch_code_from_s3(s3_bucket_name, s3_code_key)

        # Route large Python submissions to ECS to avoid Lambda timeout/OOM.
        code_bytes = len(code.encode('utf-8'))
        semgrep_languages = {'java', 'javascript', 'typescript', 'go', 'ruby', 'c', 'cpp'}
        needs_ecs = (code_bytes > LAMBDA_CODE_SIZE_LIMIT) or (language.lower() in semgrep_languages)
        if needs_ecs:
            reason = (
                f"code size {code_bytes} bytes exceeds {LAMBDA_CODE_SIZE_LIMIT} limit"
                if code_bytes > LAMBDA_CODE_SIZE_LIMIT
                else f"language '{language}' requires Semgrep (not available in Lambda)"
            )
            logger.info(f"Routing to ECS Fargate ({reason}) - scan_id: {scan_id}")
            update_scan_status(table, student_id, scan_id, 'ECS_QUEUED')
            ecs_result = handle_ecs_fallback(scan_id, language, student_id, s3_code_key)
            if not ecs_result['success']:
                try:
                    update_scan_status(table, student_id, scan_id, 'FAILED',
                                       error_message=ecs_result['error'])
                except Exception as db_err:
                    logger.error(f"Failed to update FAILED status after ECS launch error - scan_id: {scan_id}, error: {str(db_err)}")
            return ecs_result

        # Step 2: Execute security scan (inline for small submissions)
        logger.info(f"Starting scan - scan_id: {scan_id}")
        raw_scan_result = scan_code_with_timeout(code, language, scan_id, timeout=300)

        # Step 3: Parse scan results
        logger.info(f"Parsing scan results - scan_id: {scan_id}")
        if 'error' in raw_scan_result:
            raise RuntimeError(f"Scanner error: {raw_scan_result['error']}")
        parsed_result = normalize_result(
            tool=raw_scan_result['tool'],
            raw_output=raw_scan_result.get('raw_output', {}),
            scan_id=scan_id,
            language=language,
        )
        vuln_count = parsed_result['vuln_count']

        # Step 4: Write report to S3
        logger.info(f"Writing scan report to S3 - scan_id: {scan_id}")
        s3_key, presigned_url = write_scan_result_to_s3(
            bucket_name=s3_bucket_name,
            scan_id=scan_id,
            student_id=student_id,
            report_data=parsed_result
        )

        # Step 5: Update DynamoDB status
        logger.info(f"Updating DynamoDB status - scan_id: {scan_id}")
        update_scan_status(
            table=table,
            student_id=student_id,
            scan_id=scan_id,
            status='DONE',
            vuln_count=vuln_count,
            s3_report_key=s3_key
        )

        # Step 6: Delete uploaded source code — data privacy cleanup
        _delete_uploaded_code(s3_bucket_name, s3_code_key)

        logger.info(f"Scan task completed - scan_id: {scan_id}, found {vuln_count} vulnerabilities")

        return {
            'success': True,
            'scan_id': scan_id,
            'vuln_count': vuln_count,
            's3_key': s3_key,
            'presigned_url': presigned_url
        }

    except S3WriteError as e:
        logger.error(f"S3 write failed - scan_id: {scan_id}, error: {str(e)}")
        try:
            update_scan_status(table, student_id, scan_id, 'FAILED', error_message=str(e))
        except Exception as db_error:
            logger.error(f"Failed to update failure status to DynamoDB - scan_id: {scan_id}, error: {str(db_error)}")
        _delete_uploaded_code(s3_bucket_name, s3_code_key)
        return {'success': False, 'error': f"S3 write failed: {str(e)}"}

    except Exception as e:
        logger.error(f"Scan processing failed - scan_id: {scan_id}, error: {str(e)}")
        try:
            update_scan_status(table, student_id, scan_id, 'FAILED', error_message=str(e))
        except Exception as db_error:
            logger.error(f"Failed to update failure status to DynamoDB - scan_id: {scan_id}, error: {str(db_error)}")
        _delete_uploaded_code(s3_bucket_name, s3_code_key)
        return {'success': False, 'error': str(e)}


def _fetch_code_from_s3(bucket_name: str, s3_code_key: str) -> str:
    """Download source code from the S3 uploads prefix."""
    response = s3.get_object(Bucket=bucket_name, Key=s3_code_key)
    return response['Body'].read().decode('utf-8')


def _delete_uploaded_code(bucket_name: str, s3_code_key: str) -> None:
    """
    Delete uploaded source code from S3 after scanning — data privacy cleanup.
    Non-fatal: a failure here logs a warning but does not affect the scan result.
    """
    if not s3_code_key:
        return
    try:
        s3.delete_object(Bucket=bucket_name, Key=s3_code_key)
        logger.info(f"Deleted uploaded code - key: {s3_code_key}")
    except Exception as e:
        logger.warning(f"Failed to delete uploaded code - key: {s3_code_key}, error: {str(e)}")


def update_scan_status(table: Any, student_id: str, scan_id: str, status: str,
                      vuln_count: int = 0, s3_report_key: str = None,
                      error_message: str = None) -> None:
    """
    Update scan status in DynamoDB
    
    Args:
        table: DynamoDB table object
        student_id: Student ID
        scan_id: Scan ID
        status: New status (DONE, FAILED)
        vuln_count: Vulnerability count
        s3_report_key: S3 report key
        error_message: Error message (only used in FAILED status)
    """
    try:
        from datetime import datetime, timezone

        # Build update expression
        update_expression = "SET #status = :status, completed_at = :completed_at"
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {
            ":status": status,
            ":completed_at": datetime.now(timezone.utc).isoformat()
        }
        
        if status == 'DONE':
            update_expression += ", vuln_count = :vuln_count"
            expression_attribute_values[":vuln_count"] = vuln_count
            
            if s3_report_key:
                update_expression += ", s3_report_key = :s3_key"
                expression_attribute_values[":s3_key"] = s3_report_key
                
        elif status == 'FAILED' and error_message:
            update_expression += ", error_message = :error_msg"
            expression_attribute_values[":error_msg"] = error_message
        
        # Execute update
        table.update_item(
            Key={
                'student_id': student_id,
                'scan_id': scan_id
            },
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values
        )
        
        logger.info(f"DynamoDB status updated - scan_id: {scan_id}, status: {status}")
        
    except ClientError as e:
        logger.error(f"DynamoDB update failed - scan_id: {scan_id}, error: {e.response['Error']['Message']}")
        raise
    except Exception as e:
        logger.error(f"DynamoDB update exception - scan_id: {scan_id}, error: {str(e)}")
        raise


def handle_ecs_fallback(scan_id: str, language: str, student_id: str,
                       s3_code_key: str) -> Dict[str, Any]:
    """
    Launch an ECS Fargate task to handle a scan that is too large for Lambda.

    The source code is referenced via s3_code_key (an S3 object key) rather
    than being passed inline.  ECS container env var overrides are capped at
    ~8 KB per entry, so passing raw code strings for large files would cause
    the ECS API call to fail.  ecs_handler.py reads S3_CODE_KEY and fetches
    the code from S3 directly.

    Args:
        scan_id:     Scan task ID.
        language:    Programming language of the submission.
        student_id:  Student who submitted the scan.
        s3_code_key: S3 object key for the uploaded source code (set by
                     Lambda A via S3-staging, PR #48).

    Returns:
        Dict with 'success' bool and 'task_arn' or 'error'.
    """
    if not s3_code_key:
        logger.error(f"ECS fallback requires s3_code_key but none provided - scan_id: {scan_id}")
        return {'success': False, 'error': 'ECS fallback requires s3_code_key (S3-staging not active)'}

    try:
        ecs_client      = boto3.client('ecs')
        cluster_name    = os.environ.get('ECS_CLUSTER_NAME', 'sast-platform-cluster')
        task_definition = os.environ.get('ECS_TASK_DEFINITION', 'sast-scanner-task')

        response = ecs_client.run_task(
            cluster=cluster_name,
            taskDefinition=task_definition,
            launchType='FARGATE',
            networkConfiguration={
                'awsvpcConfiguration': {
                    'subnets':          [s for s in os.environ.get('ECS_SUBNETS', '').split(',') if s],
                    'securityGroups':   [s for s in os.environ.get('ECS_SECURITY_GROUPS', '').split(',') if s],
                    'assignPublicIp':   'ENABLED',
                }
            },
            overrides={
                'containerOverrides': [
                    {
                        'name': 'scanner-container',
                        'environment': [
                            {'name': 'SCAN_ID',             'value': scan_id},
                            {'name': 'STUDENT_ID',          'value': student_id},
                            {'name': 'LANGUAGE',            'value': language},
                            # Pass the S3 key, not the raw code.
                            # ecs_handler._fetch_code() downloads it from S3.
                            {'name': 'S3_CODE_KEY',         'value': s3_code_key},
                            # Forward current bucket/table names so the ECS container
                            # stays in sync with Lambda B even when the bucket name
                            # changes (e.g. PR #113 appends account ID to bucket names).
                            # Without these overrides the container falls back to the
                            # stale values baked into the ECS task definition.
                            {'name': 'S3_BUCKET_NAME',      'value': os.environ.get('S3_BUCKET_NAME', '')},
                            {'name': 'DYNAMODB_TABLE_NAME', 'value': os.environ.get('DYNAMODB_TABLE_NAME', 'ScanResults')},
                        ]
                    }
                ]
            }
        )

        task_arn = response['tasks'][0]['taskArn']
        logger.info(f"ECS task launched - scan_id: {scan_id}, task_arn: {task_arn}")

        return {
            'success': True,
            'task_arn': task_arn,
            'message': 'ECS task launched, scan will complete asynchronously',
        }

    except Exception as e:
        logger.error(f"ECS task launch failed - scan_id: {scan_id}, error: {str(e)}")
        return {
            'success': False,
            'error': f"ECS task launch failed: {str(e)}",
        }