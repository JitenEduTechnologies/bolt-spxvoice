import re
from typing import Any, BinaryIO, Dict, Optional

import aioboto3
from botocore.exceptions import ClientError
from loguru import logger

from .base import BaseFileSystem


def _redact_path(path: str) -> str:
    """Mask any potential phone numbers or sensitive identifiers in storage paths."""
    if not path:
        return ""

    def _mask_digits(m: re.Match) -> str:
        val = m.group(0)
        prefix = ""
        if val.startswith("+"):
            prefix = val[:3]
            val = val[3:]
        if len(val) >= 8:
            return prefix + val[:2] + "****" + val[-2:]
        return m.group(0)

    # Mask 10+ digit sequences (e.g. phone numbers embedded in recording filenames)
    return re.sub(r"\+?\d{10,15}", _mask_digits, str(path))


def _log_s3_error(op: str, bucket: str, path: str, exc: Exception) -> None:
    """Log structured diagnostic failure event without exposing secrets or PII."""
    redacted_path = _redact_path(path)
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        msg = exc.response.get("Error", {}).get("Message", str(exc))
        http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        logger.error(
            f"[Storage/S3] {op} failed: code={code} status={http_status} "
            f"bucket={bucket} path='{redacted_path}' message='{msg}'"
        )
    else:
        logger.error(
            f"[Storage/S3] {op} failed: error_type={type(exc).__name__} "
            f"bucket={bucket} path='{redacted_path}' error='{exc}'"
        )


class S3FileSystem(BaseFileSystem):
    """S3 implementation of the filesystem interface."""

    def __init__(self, bucket_name: str, region_name: str = "us-east-1"):
        """Initialize S3 filesystem.

        Args:
            bucket_name: Name of the S3 bucket
            region_name: AWS region name
        """
        self.bucket_name = bucket_name
        self.region_name = region_name
        self.session = aioboto3.Session()

    async def acreate_file(self, file_path: str, content: BinaryIO) -> bool:
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                await s3_client.put_object(
                    Bucket=self.bucket_name, Key=file_path, Body=await content.read()
                )
            return True
        except Exception as e:
            _log_s3_error("acreate_file", self.bucket_name, file_path, e)
            return False

    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                await s3_client.upload_file(
                    local_path, self.bucket_name, destination_path
                )
            return True
        except Exception as e:
            _log_s3_error("aupload_file", self.bucket_name, destination_path, e)
            return False

    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> Optional[str]:
        """Generate a presigned GET url for the given object.

        For transcript text files we force the response headers so that the
        browser renders the content **inline** instead of triggering a file
        download.  We do this by asking S3 to override the content type &
        disposition on the response.
        """
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                params = {"Bucket": self.bucket_name, "Key": file_path}

                # Make artifacts viewable inline in the browser when requested
                if force_inline:
                    if file_path.endswith(".txt"):
                        params.update(
                            {
                                "ResponseContentType": "text/plain",
                                "ResponseContentDisposition": "inline",
                            }
                        )
                    elif file_path.endswith(".wav"):
                        params.update(
                            {
                                "ResponseContentType": "audio/wav",
                                "ResponseContentDisposition": "inline",
                            }
                        )
                    elif file_path.endswith(".mp3"):
                        params.update(
                            {
                                "ResponseContentType": "audio/mpeg",
                                "ResponseContentDisposition": "inline",
                            }
                        )

                url = await s3_client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=expiration,
                )
            return url
        except Exception as e:
            _log_s3_error("aget_signed_url", self.bucket_name, file_path, e)
            return None

    async def aget_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get S3 object metadata."""
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                response = await s3_client.head_object(
                    Bucket=self.bucket_name, Key=file_path
                )
                return {
                    "size": response.get("ContentLength"),
                    "created_at": response.get("LastModified"),
                    "modified_at": response.get("LastModified"),
                    "etag": response.get("ETag", "").strip('"'),
                    "content_type": response.get("ContentType"),
                    "storage_class": response.get("StorageClass"),
                }
        except Exception as e:
            _log_s3_error("aget_file_metadata", self.bucket_name, file_path, e)
            return None

    async def aget_presigned_put_url(
        self,
        file_path: str,
        expiration: int = 900,
        content_type: str = "text/csv",
        max_size: int = 10_485_760,
    ) -> Optional[str]:
        """Generate a presigned PUT URL for direct file upload."""
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                url = await s3_client.generate_presigned_url(
                    "put_object",
                    Params={
                        "Bucket": self.bucket_name,
                        "Key": file_path,
                        "ContentType": content_type,
                    },
                    ExpiresIn=expiration,
                )
            return url
        except Exception as e:
            _log_s3_error("aget_presigned_put_url", self.bucket_name, file_path, e)
            return None

    async def adownload_file(self, source_path: str, local_path: str) -> bool:
        """Download a file from S3 to local path."""
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                await s3_client.download_file(self.bucket_name, source_path, local_path)
            return True
        except Exception as e:
            _log_s3_error("adownload_file", self.bucket_name, source_path, e)
            return False

    async def acopy_file(self, source_path: str, destination_path: str) -> bool:
        """Copy a file within S3 (server-side copy)."""
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                await s3_client.copy_object(
                    Bucket=self.bucket_name,
                    Key=destination_path,
                    CopySource={"Bucket": self.bucket_name, "Key": source_path},
                )
            return True
        except Exception as e:
            _log_s3_error("acopy_file", self.bucket_name, destination_path, e)
            return False

    async def adelete_file(self, file_path: str) -> bool:
        """Delete a file from S3."""
        try:
            async with self.session.client(
                "s3", region_name=self.region_name
            ) as s3_client:
                await s3_client.delete_object(
                    Bucket=self.bucket_name,
                    Key=file_path,
                )
            return True
        except Exception as e:
            _log_s3_error("adelete_file", self.bucket_name, file_path, e)
            return False
