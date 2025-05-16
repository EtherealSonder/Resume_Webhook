# s3_utils.py
import boto3
import os
from botocore.exceptions import NoCredentialsError
from datetime import datetime

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_BUCKET = os.getenv("AWS_S3_BUCKET_NAME")
AWS_REGION = os.getenv("AWS_REGION")

s3 = boto3.client(
    's3',
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY
)

def upload_to_s3(file_path: str, job_id: str, original_name: str) -> str:
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"resume_{job_id}_{timestamp}_{original_name}".replace(" ", "_")
        s3.upload_file(
            Filename=file_path,
            Bucket=AWS_BUCKET,
            Key=filename,
            ExtraArgs={"ACL": "public-read", "ContentType": "application/pdf"}
        )
        url = f"https://{AWS_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{filename}"
        return url
    except NoCredentialsError:
        raise Exception("AWS credentials not found.")
    except Exception as e:
        raise e
