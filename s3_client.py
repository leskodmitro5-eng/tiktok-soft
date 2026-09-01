import os
import logging
from pathlib import Path
try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False
    boto3 = None
    Config = None
    ClientError = Exception

import config

logger = logging.getLogger("S3Client")

_s3_client = None

def get_s3_client():
    """Initializes and returns the boto3 S3 client for R2/S3 compatible storage."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    if not _BOTO3_AVAILABLE or not is_s3_configured():
        if not _BOTO3_AVAILABLE:
            logger.debug("boto3 is not installed. S3 upload will be skipped.")
        else:
            logger.debug("S3/R2 cloud storage is not fully configured. Missing environment variables.")
        return None

    try:
        # Cloudflare R2 requires custom endpoint_url. We also configure signature_version='v4' for security.
        _s3_client = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY_ID,
            aws_secret_access_key=config.S3_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4")
        )
        logger.info("S3/R2 client initialized successfully.")
        return _s3_client
    except Exception as e:
        logger.exception(f"Failed to initialize S3/R2 client: {e}")
        return None


def is_s3_configured() -> bool:
    """Checks if S3 environment variables are provided."""
    return bool(
        config.S3_ENDPOINT_URL and
        config.S3_ACCESS_KEY_ID and
        config.S3_SECRET_ACCESS_KEY and
        config.S3_BUCKET_NAME
    )


def upload_file_to_s3(local_file_path: str | Path, s3_key: str) -> str | None:
    """
    Uploads a file to the S3/R2 bucket.
    If successful, returns a presigned GET URL valid for 7 days (604800 seconds).
    If S3 is not configured or upload fails, returns None.
    """
    local_path = Path(local_file_path)
    if not local_path.exists():
        logger.error(f"Local file does not exist: {local_file_path}")
        return None

    s3 = get_s3_client()
    if not s3:
        logger.warning(f"S3 client not available, skipping upload for {local_path.name}")
        return None

    logger.info(f"Uploading {local_path.name} ({local_path.stat().st_size / 1024 / 1024:.2f} MB) to S3 key '{s3_key}'...")
    try:
        s3.upload_file(
            Filename=str(local_path),
            Bucket=config.S3_BUCKET_NAME,
            Key=s3_key
        )
        logger.info(f"Successfully uploaded {local_path.name} to key '{s3_key}'")
        
        # Generate a presigned URL valid for 7 days (max limit for signature version v4 in standard setups)
        presigned_url = get_presigned_url(s3_key, expiration=604800)
        return presigned_url
    except ClientError as ce:
        logger.error(f"S3 upload client error for key '{s3_key}': {ce}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error during S3 upload for key '{s3_key}': {e}")
        return None


def get_presigned_url(s3_key: str, expiration: int = 604800) -> str | None:
    """
    Generates a presigned GET URL for an S3/R2 object.
    Defaults to 7 days expiration.
    """
    s3 = get_s3_client()
    if not s3:
        return None

    try:
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": config.S3_BUCKET_NAME,
                "Key": s3_key
            },
            ExpiresIn=expiration
        )
        return url
    except Exception as e:
        logger.exception(f"Error generating presigned URL for S3 key '{s3_key}': {e}")
        return None
