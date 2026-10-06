#The pipeline uploads data.csv to S3.
#downloads it from S3 and processes that copy
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from etl.config import S3Settings

log = logging.getLogger("s3")


class S3Storage:
    def __init__(self, settings: S3Settings | None = None):
        self.settings = settings or S3Settings()
        if not self.settings.bucket:
            raise ValueError("S3_BUCKET_NAME is not set in .env")
        self.bucket = self.settings.bucket
        self.prefix = self.settings.prefix
        # Automatic retries with backoff for transient network / throttling errors
        self.client = boto3.client(
            "s3",
            region_name=self.settings.region,
            config=Config(retries={"max_attempts": 5, "mode": "standard"}),
        )

    def key_for(self, zone: str, filename: str, ingest_date: date | None = None) -> str:
        ingest_date = ingest_date or date.today()
        return f"{self.prefix}/{zone}/ingest_date={ingest_date.isoformat()}/{filename}"

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def check_access(self) -> None:
        """Fail fast with a clear message if credentials or permissions are wrong."""
        try:
            self.client.list_objects_v2(Bucket=self.bucket, Prefix=f"{self.prefix}/", MaxKeys=1)
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(
                f"Cannot access s3://{self.bucket}/{self.prefix}/ - check the AWS keys in .env "
                f"and the IAM policy. Details: {exc}"
            ) from exc
        log.info("S3 access OK: s3://%s/%s/", self.bucket, self.prefix)

    def upload_file(self, local_path: Path, zone: str, filename: str | None = None) -> str:
        key = self.key_for(zone, filename or local_path.name)
        size_mb = local_path.stat().st_size / 1_048_576
        self.client.upload_file(str(local_path), self.bucket, key)  # multipart upload for large files
        log.info("Uploaded %s (%.1f MB) -> %s", local_path.name, size_mb, self.uri(key))
        return key

    def download_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        payload = response["Body"].read()
        log.info("Downloaded %s (%.1f MB)", self.uri(key), len(payload) / 1_048_576)
        return payload
