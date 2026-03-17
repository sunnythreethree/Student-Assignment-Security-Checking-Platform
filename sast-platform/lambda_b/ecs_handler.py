"""
ECS Fargate Task Handler
Used to handle large files or complex scanning tasks when Lambda resources are insufficient
Gets scanning parameters from environment variables, executes scan and writes to S3 and updates DynamoDB
"""
import os
import json
import logging
import boto3
import sys
from typing import Dict, Any

from scanner import scan_code_with_timeout
from result_parser import ResultParser
from s3_writer import write_scan_result_to_s3, S3WriteError
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')


def main():
    """ECS task main entry point"""
    try:
        # Get task parameters from environment variables
        scan_id = os.environ.get('SCAN_ID')
        student_id = os.environ.get('STUDENT_ID') 
        language = os.environ.get('LANGUAGE')
        code_content = os.environ.get('CODE_CONTENT')
        
        # Validate required parameters
        if not all([scan_id, student_id, language, code_content]):
            missing = [name for name, value in [
                ('SCAN_ID', scan_id),
                ('STUDENT_ID', student_id), 
                ('LANGUAGE', language),
                ('CODE_CONTENT', code_content)
            ] if not value]
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        # Get other configurations
        table_name = os.environ.get('DYNAMODB_TABLE_NAME')
        s3_bucket_name = os.environ.get('S3_BUCKET_NAME')
        
        if not table_name:
            raise ValueError("Environment variable DYNAMODB_TABLE_NAME not set")
        if not s3_bucket_name:
            raise ValueError("Environment variable S3_BUCKET_NAME not set")
        
        logger.info(f"Starting ECS scan task - scan_id: {scan_id}, language: {language}")
        
        # Execute scan processing
        table = dynamodb.Table(table_name)
        result = process_ecs_scan(
            scan_id=scan_id,
            code=code_content,
            language=language,
            student_id=student_id,
            table=table,
            s3_bucket_name=s3_bucket_name
        )
        
        if result['success']:
            logger.info(f"ECS scan task completed - scan_id: {scan_id}")
            sys.exit(0)
        else:
            logger.error(f"ECS scan task failed - scan_id: {scan_id}, error: {result['error']}")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"ECS task exited with exception: {str(e)}")
        sys.exit(1)


def process_ecs_scan(scan_id: str, code: str, language: str, student_id: str,
                     table: Any, s3_bucket_name: str) -> Dict[str, Any]:
    """
    Process ECS scan task
    
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
        # Step 1: Execute security scan (ECS has more resources, can set longer timeout)
        logger.info(f"Starting scan - scan_id: {scan_id}")
        raw_scan_result = scan_code_with_timeout(code, language, scan_id, timeout=1800)  # 30 minute timeout
        
        # Step 2: Parse scan results
        logger.info(f"Parsing scan results - scan_id: {scan_id}")
        parsed_result = ResultParser.parse_scan_result(raw_scan_result)
        vuln_count = ResultParser.calculate_vuln_count(parsed_result)
        
        # Step 3: Write to S3
        logger.info(f"Writing scan report to S3 - scan_id: {scan_id}")
        s3_key, presigned_url = write_scan_result_to_s3(
            bucket_name=s3_bucket_name,
            scan_id=scan_id,
            report_data=parsed_result
        )
        
        # Step 4: Update DynamoDB status
        logger.info(f"Updating DynamoDB status - scan_id: {scan_id}")
        update_scan_status_ecs(
            table=table,
            student_id=student_id,
            scan_id=scan_id,
            status='DONE',
            vuln_count=vuln_count,
            s3_report_key=s3_key
        )
        
        logger.info(f"ECS scan task completed - scan_id: {scan_id}, found {vuln_count} vulnerabilities")
        
        return {
            'success': True,
            'scan_id': scan_id,
            'vuln_count': vuln_count,
            's3_key': s3_key
        }
        
    except S3WriteError as e:
        # S3 write failed, update DynamoDB to FAILED status
        logger.error(f"S3 write failed - scan_id: {scan_id}, error: {str(e)}")
        try:
            update_scan_status_ecs(table, student_id, scan_id, 'FAILED', error_message=str(e))
        except Exception as db_error:
            logger.error(f"Failed to update failure status to DynamoDB - scan_id: {scan_id}, error: {str(db_error)}")
        
        return {'success': False, 'error': f"S3 write failed: {str(e)}"}
        
    except Exception as e:
        # Other errors, also update DynamoDB to FAILED status
        logger.error(f"ECS scan processing failed - scan_id: {scan_id}, error: {str(e)}")
        try:
            update_scan_status_ecs(table, student_id, scan_id, 'FAILED', error_message=str(e))
        except Exception as db_error:
            logger.error(f"Failed to update failure status to DynamoDB - scan_id: {scan_id}, error: {str(db_error)}")
        
        return {'success': False, 'error': str(e)}


def update_scan_status_ecs(table: Any, student_id: str, scan_id: str, status: str,
                          vuln_count: int = 0, s3_report_key: str = None,
                          error_message: str = None) -> None:
    """
    Update scan status in DynamoDB (ECS version)
    
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
        update_expression = "SET #status = :status, completed_at = :completed_at, processing_method = :method"
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {
            ":status": status,
            ":completed_at": datetime.utcnow().isoformat() + 'Z',
            ":method": "ECS_FARGATE"  # Mark as ECS processing
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
        
        logger.info(f"DynamoDB status updated (ECS) - scan_id: {scan_id}, status: {status}")
        
    except ClientError as e:
        logger.error(f"DynamoDB update failed - scan_id: {scan_id}, error: {e.response['Error']['Message']}")
        raise
    except Exception as e:
        logger.error(f"DynamoDB update exception - scan_id: {scan_id}, error: {str(e)}")
        raise


if __name__ == "__main__":
    main()
