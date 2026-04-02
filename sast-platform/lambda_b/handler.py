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
import logging
import boto3
from typing import Dict, Any, List
from botocore.exceptions import ClientError

from scanner import scan_code_with_timeout
from result_parser import ResultParser
from s3_writer import write_scan_result_to_s3, get_s3_bucket_from_env, S3WriteError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
sqs = boto3.client('sqs')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda B main entry point
    
    Args:
        event: SQS event data
        context: Lambda runtime context
        
    Returns:
        Processing result
    """
    logger.info(f"Lambda B started processing SQS event: {json.dumps(event)}")
    
    # Processing result statistics
    successful_count = 0
    failed_count = 0
    failed_messages = []
    
    try:
        # Get environment variables
        table_name = os.environ.get('DYNAMODB_TABLE_NAME')
        if not table_name:
            raise ValueError("Environment variable DYNAMODB_TABLE_NAME not set")
        
        s3_bucket_name = get_s3_bucket_from_env()
        table = dynamodb.Table(table_name)
        
        # Process SQS messages
        records = event.get('Records', [])
        logger.info(f"Received {len(records)} SQS messages")
        
        for record in records:
            try:
                # Parse message
                message_body = json.loads(record['body'])
                scan_id = message_body['scan_id']
                code = message_body['code']
                language = message_body['language']
                student_id = message_body['student_id']
                
                logger.info(f"Started processing scan task - scan_id: {scan_id}, language: {language}")
                
                # Execute scanning
                result = process_scan_request(
                    scan_id=scan_id,
                    code=code,
                    language=language,
                    student_id=student_id,
                    table=table,
                    s3_bucket_name=s3_bucket_name
                )
                
                if result['success']:
                    successful_count += 1
                    logger.info(f"Scan task completed - scan_id: {scan_id}")
                else:
                    failed_count += 1
                    failed_messages.append({
                        'scan_id': scan_id,
                        'error': result['error']
                    })
                    logger.error(f"Scan task failed - scan_id: {scan_id}, error: {result['error']}")
                    
            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to process SQS message: {str(e)}"
                logger.error(error_msg)
                failed_messages.append({
                    'record_id': record.get('messageId', 'unknown'),
                    'error': error_msg
                })
        
        # Return processing result
        result = {
            'statusCode': 200,
            'body': {
                'total_messages': len(records),
                'successful': successful_count,
                'failed': failed_count,
                'failed_messages': failed_messages
            }
        }
        
        logger.info(f"Lambda B processing completed - successful: {successful_count}, failed: {failed_count}")
        return result
        
    except Exception as e:
        logger.error(f"Lambda B processing exception: {str(e)}")
        return {
            'statusCode': 500,
            'body': {
                'error': str(e),
                'successful': successful_count,
                'failed': failed_count + 1
            }
        }


def process_scan_request(scan_id: str, code: str, language: str, student_id: str,
                        table: Any, s3_bucket_name: str) -> Dict[str, Any]:
    """
    Process single scan request
    
    Args:
        scan_id: Scan task ID
        code: Code to be scanned
        language: Code language
        student_id: Student ID
        table: DynamoDB table object
        s3_bucket_name: S3 bucket name
        
    Returns:
        Processing result
    """
    try:
        # Step 1: Execute security scan
        logger.info(f"Starting scan - scan_id: {scan_id}")
        raw_scan_result = scan_code_with_timeout(code, language, scan_id, timeout=300)
        
        # Step 2: Parse scan results
        logger.info(f"Parsing scan results - scan_id: {scan_id}")
        parsed_result = ResultParser.parse_scan_result(raw_scan_result)
        vuln_count = ResultParser.calculate_vuln_count(parsed_result)
        
        # Step 3: Write to S3
        logger.info(f"Writing scan report to S3 - scan_id: {scan_id}")
        s3_key, presigned_url = write_scan_result_to_s3(
            bucket_name=s3_bucket_name,
            scan_id=scan_id,
            student_id=student_id,
            report_data=parsed_result
        )
        
        # Step 4: Update DynamoDB status
        logger.info(f"Updating DynamoDB status - scan_id: {scan_id}")
        update_scan_status(
            table=table,
            student_id=student_id,
            scan_id=scan_id,
            status='DONE',
            vuln_count=vuln_count,
            s3_report_key=s3_key
        )
        
        logger.info(f"Scan task completed - scan_id: {scan_id}, found {vuln_count} vulnerabilities")
        
        return {
            'success': True,
            'scan_id': scan_id,
            'vuln_count': vuln_count,
            's3_key': s3_key,
            'presigned_url': presigned_url
        }
        
    except S3WriteError as e:
        # S3 write failed, update DynamoDB to FAILED status
        logger.error(f"S3 write failed - scan_id: {scan_id}, error: {str(e)}")
        try:
            update_scan_status(table, student_id, scan_id, 'FAILED', error_message=str(e))
        except Exception as db_error:
            logger.error(f"Failed to update failure status to DynamoDB - scan_id: {scan_id}, error: {str(db_error)}")
        
        return {'success': False, 'error': f"S3 write failed: {str(e)}"}
        
    except Exception as e:
        # Other errors, also update DynamoDB to FAILED status
        logger.error(f"Scan processing failed - scan_id: {scan_id}, error: {str(e)}")
        try:
            update_scan_status(table, student_id, scan_id, 'FAILED', error_message=str(e))
        except Exception as db_error:
            logger.error(f"Failed to update failure status to DynamoDB - scan_id: {scan_id}, error: {str(db_error)}")
        
        return {'success': False, 'error': str(e)}


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
        from datetime import datetime
        
        # Build update expression
        update_expression = "SET #status = :status, completed_at = :completed_at"
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {
            ":status": status,
            ":completed_at": datetime.utcnow().isoformat() + 'Z'
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


def handle_ecs_fallback(scan_id: str, code: str, language: str, student_id: str) -> Dict[str, Any]:
    """
    Handle ECS Fargate fallback logic for large files or complex scans
    Used when Lambda memory is insufficient or execution time exceeds limit
    
    Args:
        scan_id: Scan ID
        code: Code content
        language: Code language
        student_id: Student ID
        
    Returns:
        ECS task launch result
    """
    try:
        ecs_client = boto3.client('ecs')
        cluster_name = os.environ.get('ECS_CLUSTER_NAME', 'sast-platform-cluster')
        task_definition = os.environ.get('ECS_TASK_DEFINITION', 'sast-scanner-task')
        
        # Launch ECS task
        response = ecs_client.run_task(
            cluster=cluster_name,
            taskDefinition=task_definition,
            launchType='FARGATE',
            networkConfiguration={
                'awsvpcConfiguration': {
                    'subnets': os.environ.get('ECS_SUBNETS', '').split(','),
                    'securityGroups': os.environ.get('ECS_SECURITY_GROUPS', '').split(','),
                    'assignPublicIp': 'ENABLED'
                }
            },
            overrides={
                'containerOverrides': [
                    {
                        'name': 'scanner-container',
                        'environment': [
                            {'name': 'SCAN_ID', 'value': scan_id},
                            {'name': 'STUDENT_ID', 'value': student_id},
                            {'name': 'LANGUAGE', 'value': language},
                            {'name': 'CODE_CONTENT', 'value': code}
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
            'message': 'ECS task launched, will complete scan asynchronously'
        }
        
    except Exception as e:
        logger.error(f"ECS task launch failed - scan_id: {scan_id}, error: {str(e)}")
        return {
            'success': False,
            'error': f"ECS task launch failed: {str(e)}"
        }