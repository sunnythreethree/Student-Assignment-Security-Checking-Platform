"""
Run scan job in ECS Fargate.
Used when the scan is too large for Lambda.
"""

import os
import sys
import logging
import boto3

from scanner import scan_code_with_timeout
from result_parser import ResultParser
from s3_writer import write_scan_result_to_s3, S3WriteError
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# connect to DynamoDB and S3
dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")


def _fetch_code(s3_bucket_name: str) -> str:
    """
    Resolve the source code to scan.

    Priority:
    1. S3_CODE_KEY env var — code was uploaded to S3 (S3-staging path, issue #14).
       Preferred for large submissions because env vars are limited to ~32 KB.
    2. CODE_CONTENT env var — code passed inline (legacy / small submissions).
    """
    s3_code_key = os.environ.get("S3_CODE_KEY")
    if s3_code_key:
        logger.info(f"Fetching code from S3: {s3_code_key}")
        response = s3_client.get_object(Bucket=s3_bucket_name, Key=s3_code_key)
        return response["Body"].read().decode("utf-8")

    code_content = os.environ.get("CODE_CONTENT")
    if code_content:
        return code_content

    raise ValueError("Neither S3_CODE_KEY nor CODE_CONTENT environment variable is set")


def main():
    try:
        # required info from env variables
        scan_id    = os.environ.get("SCAN_ID")
        student_id = os.environ.get("STUDENT_ID")
        language   = os.environ.get("LANGUAGE")

        missing = [name for name, val in [("SCAN_ID", scan_id), ("STUDENT_ID", student_id), ("LANGUAGE", language)] if not val]
        if missing:
            raise ValueError("Missing required environment variables: " + ", ".join(missing))

        # get table and bucket names
        table_name     = os.environ.get("DYNAMODB_TABLE_NAME")
        s3_bucket_name = os.environ.get("S3_BUCKET_NAME")

        if not table_name:
            raise ValueError("DYNAMODB_TABLE_NAME is not set")
        if not s3_bucket_name:
            raise ValueError("S3_BUCKET_NAME is not set")

        logger.info(f"Start scan task: {scan_id}")

        # fetch source code (S3 key preferred, inline env var as fallback)
        code_content = _fetch_code(s3_bucket_name)

        # connect to table
        table = dynamodb.Table(table_name)

        # run the main scan process
        result = process_ecs_scan(
            scan_id,
            code_content,
            language,
            student_id,
            table,
            s3_bucket_name
        )

        if result["success"]:
            logger.info(f"Scan finished: {scan_id}")
            sys.exit(0)
        else:
            logger.error(f"Scan failed: {scan_id}, error: {result['error']}")
            sys.exit(1)

    except Exception as e:
        logger.error(f"ECS task crashed: {str(e)}")
        sys.exit(1)


def process_ecs_scan(scan_id, code, language, student_id, table, s3_bucket_name):
    try:
        logger.info(f"Running scan for {scan_id}")

        # run the scanner (with timeout)
        raw_scan_result = scan_code_with_timeout(code, language, scan_id, timeout=1800)

        logger.info(f"Parsing result for {scan_id}")

        # format the scan result
        parsed_result = ResultParser.parse_scan_result(raw_scan_result)

        # count vulnerabilities
        vuln_count = ResultParser.calculate_vuln_count(parsed_result)

        logger.info(f"Saving to S3 for {scan_id}")

        # save report to S3
        s3_key, _ = write_scan_result_to_s3(
            bucket_name=s3_bucket_name,
            scan_id=scan_id,
            report_data=parsed_result
        )

        logger.info(f"Updating DynamoDB for {scan_id}")

        update_scan_status_ecs(
            table,
            student_id,
            scan_id,
            "DONE",
            vuln_count=vuln_count,
            s3_report_key=s3_key
        )

        logger.info(f"Done: {scan_id}, found {vuln_count} issues")

        return {
            "success": True,
            "scan_id": scan_id,
            "vuln_count": vuln_count,
            "s3_key": s3_key
        }

    except S3WriteError as e:
        logger.error(f"S3 error for {scan_id}: {str(e)}")

        # try to mark as FAILED in DB
        try:
            update_scan_status_ecs(table, student_id, scan_id, "FAILED", error_message=str(e))
        except Exception as db_error:
            logger.error(f"DB update also failed: {str(db_error)}")

        return {
            "success": False,
            "error": f"S3 write failed: {str(e)}"
        }

    except Exception as e:
        logger.error(f"Scan error for {scan_id}: {str(e)}")

        # try to mark as FAILED
        try:
            update_scan_status_ecs(table, student_id, scan_id, "FAILED", error_message=str(e))
        except Exception as db_error:
            logger.error(f"DB update also failed: {str(db_error)}")

        return {
            "success": False,
            "error": str(e)
        }


def update_scan_status_ecs(table, student_id, scan_id, status, vuln_count=0, s3_report_key=None, error_message=None):
    try:
        from datetime import datetime

        # base update fields
        update_expression = "SET #status = :status, completed_at = :completed_at, processing_method = :method"
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {
            ":status": status,
            ":completed_at": datetime.utcnow().isoformat() + "Z",
            ":method": "ECS_FARGATE"
        }

        # if success, save vuln count + s3 key
        if status == "DONE":
            update_expression += ", vuln_count = :vuln_count"
            expression_attribute_values[":vuln_count"] = vuln_count

            if s3_report_key:
                update_expression += ", s3_report_key = :s3_key"
                expression_attribute_values[":s3_key"] = s3_report_key

        # if failed, save error message
        elif status == "FAILED" and error_message:
            update_expression += ", error_message = :error_msg"
            expression_attribute_values[":error_msg"] = error_message

        # update DynamoDB
        table.update_item(
            Key={
                "student_id": student_id,
                "scan_id": scan_id
            },
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values
        )

        logger.info(f"DB updated for {scan_id}, status: {status}")

    except ClientError as e:
        logger.error(f"DynamoDB error for {scan_id}: {e.response['Error']['Message']}")
        raise
    except Exception as e:
        logger.error(f"DynamoDB failed for {scan_id}: {str(e)}")
        raise


if __name__ == "__main__":
    main()