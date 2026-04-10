# This module is used to save scan results to S3.
import json
import os
import boto3
import logging
from botocore.exceptions import ClientError, NoCredentialsError

logger = logging.getLogger(__name__)


class S3WriteError(Exception):
    """Raised when writing a scan report to S3 fails."""


class S3Writer:
    """Save scan reports to S3."""

    def __init__(self, bucket_name, region=None):
        self.bucket_name = bucket_name
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self.s3_client = boto3.client("s3", region_name=self.region)

    def write_scan_report(self, scan_id, report_data, student_id):
        """Upload a scan report and return the S3 key."""
        try:
            # store reports under reports/{student_id}/{scan_id}.json
            # matches the path convention documented in infrastructure/s3.yaml
            s3_key = f"reports/{student_id}/{scan_id}.json"

            # convert dict to JSON string
            json_content = json.dumps(report_data, indent=2, ensure_ascii=False)

            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=json_content.encode("utf-8"),
                ContentType="application/json",
                Metadata={
                    "scan_id": scan_id,
                    "language": report_data.get("language", "unknown"),
                    "tool": report_data.get("tool", "unknown"),
                    "vuln_count": str(self._calculate_total_vulns(report_data)),
                },
            )

            logger.info(f"uploaded scan report to S3: {s3_key}")
            return s3_key

        except NoCredentialsError:
            logger.error("AWS credentials were not found")
            raise S3WriteError("AWS credentials were not found")

        except ClientError as e:
            error_msg = e.response["Error"]["Message"]
            logger.error(f"failed to upload report: {error_msg}")
            raise S3WriteError(f"failed to upload report: {error_msg}")

        except Exception as e:
            logger.error(f"unexpected error: {str(e)}")
            raise S3WriteError(f"unexpected error: {str(e)}")

    def generate_presigned_url(self, s3_key, expiration=3600):
        """Create a temporary download link."""
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": s3_key},
                ExpiresIn=expiration,
            )

            logger.info(f"generated URL for {s3_key}")
            return url

        except ClientError as e:
            error_msg = e.response["Error"]["Message"]
            logger.error(f"failed to generate URL: {error_msg}")
            raise S3WriteError(f"failed to generate URL: {error_msg}")

        except Exception as e:
            logger.error(f"unexpected error: {str(e)}")
            raise S3WriteError(f"unexpected error: {str(e)}")

    def check_object_exists(self, s3_key):
        """Check if a report exists in S3."""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise Exception(f"error checking object: {e.response['Error']['Message']}")

    def delete_report(self, s3_key):
        """Delete a report from S3."""
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            logger.info(f"deleted report: {s3_key}")
            return True
        except ClientError as e:
            logger.error(f"failed to delete report: {e.response['Error']['Message']}")
            return False

    @staticmethod
    def _calculate_total_vulns(report_data):
        """Count total vulnerabilities."""
        summary = report_data.get("summary", {})
        return (
            summary.get("HIGH", 0)
            + summary.get("MEDIUM", 0)
            + summary.get("LOW", 0)
        )


def write_scan_result_to_s3(bucket_name, scan_id, student_id, report_data, region=None):
    """Save result to S3 and return key + URL."""
    writer = S3Writer(bucket_name, region)
    s3_key = writer.write_scan_report(scan_id, report_data, student_id)
    url = writer.generate_presigned_url(s3_key)
    return s3_key, url


def get_s3_bucket_from_env():
    """Get bucket name from environment variable."""
    bucket_name = os.environ.get("S3_BUCKET_NAME")
    if not bucket_name:
        raise ValueError("S3_BUCKET_NAME is not set")
    return bucket_name