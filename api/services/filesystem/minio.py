import asyncio
from datetime import timedelta
import json
from typing import Any, BinaryIO, Dict, Optional

from loguru import logger
from minio import Minio
from minio.error import S3Error

from .base import BaseFileSystem


class MinioFileSystem(BaseFileSystem):
    """MinIO implementation of the filesystem interface for OSS users.

    Two endpoints, two different purposes:
    - endpoint (host:port) + secure (bool): used by the MinIO SDK for
      container-to-container calls. The SDK requires these split.
    - public_endpoint (full URL, e.g. "https://example.com"): used verbatim
      when building URLs that browsers will fetch. Required.
    """

    def __init__(
        self,
        endpoint: str = "localhost:9000",
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin",
        bucket_name: str = "voice-audio",
        secure: bool = False,
        public_endpoint: Optional[str] = None,
        auto_enforce_private: bool = False,
    ):
        if not public_endpoint:
            raise ValueError(
                "MinioFileSystem requires public_endpoint (set MINIO_PUBLIC_ENDPOINT). "
                "Expected a full URL with scheme, e.g. 'http://localhost:9000' or 'https://example.com'."
            )
        if not (
            public_endpoint.startswith("http://")
            or public_endpoint.startswith("https://")
        ):
            raise ValueError(
                f"MINIO_PUBLIC_ENDPOINT must include a scheme (http:// or https://), got: {public_endpoint!r}"
            )

        self.bucket_name = bucket_name
        self.endpoint = endpoint
        self.public_endpoint = public_endpoint.rstrip("/")
        self.secure = secure
        self.access_key = access_key
        self.secret_key = secret_key

        # Client for internal operations (uploads, downloads, stat, etc.)
        self.client = Minio(
            endpoint, access_key=access_key, secret_key=secret_key, secure=secure
        )

        # Client for generating browser-facing presigned URLs using public_endpoint
        self.public_client = None
        if self.public_endpoint:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(self.public_endpoint)
                pub_host = parsed.netloc or parsed.path
                pub_secure = parsed.scheme == "https"
                if pub_host:
                    self.public_client = Minio(
                        pub_host,
                        access_key=access_key,
                        secret_key=secret_key,
                        secure=pub_secure,
                    )
                    # Pre-cache region so Minio SDK performs offline HMAC signing without network calls
                    self.public_client._region_map[bucket_name] = "us-east-1"
            except Exception as e:
                logger.warning(f"Could not initialize public MinIO client: {e}")

        # In Stage 1A, we DO NOT automatically execute policy changes against running MinIO
        # unless explicitly requested via auto_enforce_private=True.
        if auto_enforce_private:
            self.enforce_private_bucket_policy()

    def enforce_private_bucket_policy(self) -> bool:
        """Enforces private bucket policy by revoking any public/anonymous bucket policy.

        - Treats NoSuchBucketPolicy as already private (success).
        - Fails closed on genuine S3/MinIO errors by raising RuntimeError.
        """
        try:
            try:
                policy_str = self.client.get_bucket_policy(self.bucket_name)
            except S3Error as e:
                if e.code in ("NoSuchBucketPolicy", "NoSuchPolicy", "NoSuchBucket"):
                    logger.debug(
                        f"Bucket '{self.bucket_name}' has no policy or does not exist yet; already private."
                    )
                    return True
                raise

            if policy_str:
                logger.info(
                    f"Revoking public/existing bucket policy on '{self.bucket_name}'..."
                )
                self.client.delete_bucket_policy(self.bucket_name)
                logger.info(f"Bucket '{self.bucket_name}' policy revoked; now strictly private.")
            return True
        except S3Error as e:
            if e.code in ("NoSuchBucketPolicy", "NoSuchPolicy"):
                return True
            logger.error(
                f"Failed to enforce private bucket policy on '{self.bucket_name}': {e}"
            )
            raise RuntimeError(
                f"Storage security check failed: unable to enforce private policy: {e}"
            ) from e
        except Exception as e:
            logger.error(f"Unexpected error enforcing private bucket policy: {e}")
            raise RuntimeError(f"Storage security check failed: {e}") from e

    async def acreate_file(self, file_path: str, content: BinaryIO) -> bool:
        try:
            data = await content.read()

            def _put():
                self.client.put_object(
                    self.bucket_name,
                    file_path,
                    data=bytes(data),
                    length=len(data),
                )

            await asyncio.to_thread(_put)
            return True
        except S3Error:
            return False

    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        try:

            def _fput():
                self.client.fput_object(self.bucket_name, destination_path, local_path)

            await asyncio.to_thread(_fput)
            return True
        except S3Error:
            return False

    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> Optional[str]:
        """Generate an authentic HMAC-signed presigned GET URL for temporary access."""
        try:
            headers = {}
            if force_inline:
                ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
                content_types = {
                    "txt": "text/plain",
                    "wav": "audio/wav",
                    "mp3": "audio/mpeg",
                    "m4a": "audio/mp4",
                    "webm": "audio/webm",
                    "ogg": "audio/ogg",
                }
                if ext in content_types:
                    headers["response-content-type"] = content_types[ext]
                    headers["response-content-disposition"] = "inline"

            signing_client = self.client if use_internal_endpoint else (self.public_client or self.client)

            def _sign():
                return signing_client.presigned_get_object(
                    self.bucket_name,
                    file_path,
                    expires=timedelta(seconds=expiration),
                    response_headers=headers if headers else None,
                )

            url = await asyncio.to_thread(_sign)
            return url
        except Exception as e:
            logger.error(f"Error generating MinIO signed GET URL: {e}")
            return None

    async def aget_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get MinIO object metadata."""
        try:

            def _stat():
                return self.client.stat_object(self.bucket_name, file_path)

            stat = await asyncio.to_thread(_stat)
            return {
                "size": stat.size,
                "created_at": stat.last_modified,
                "modified_at": stat.last_modified,
                "etag": stat.etag.strip('"') if stat.etag else None,
                "content_type": stat.content_type,
                "storage_class": None,
            }
        except S3Error:
            return None

    async def aget_presigned_put_url(
        self,
        file_path: str,
        expiration: int = 900,
        content_type: str = "text/csv",
        max_size: int = 10_485_760,
    ) -> Optional[str]:
        """Generate an authentic HMAC-signed presigned PUT URL for direct file upload."""
        try:
            signing_client = self.public_client or self.client

            def _sign_put():
                return signing_client.presigned_put_object(
                    self.bucket_name,
                    file_path,
                    expires=timedelta(seconds=expiration),
                )

            url = await asyncio.to_thread(_sign_put)
            return url
        except Exception as e:
            logger.error(f"Error generating MinIO presigned PUT URL: {e}")
            return None

    async def adownload_file(self, source_path: str, local_path: str) -> bool:
        """Download a file from MinIO to local path."""
        try:

            def _fget():
                self.client.fget_object(self.bucket_name, source_path, local_path)

            await asyncio.to_thread(_fget)
            return True
        except S3Error:
            return False

    async def acopy_file(self, source_path: str, destination_path: str) -> bool:
        """Copy a file within MinIO (server-side copy)."""
        try:
            from minio.commonconfig import CopySource

            def _copy():
                self.client.copy_object(
                    self.bucket_name,
                    destination_path,
                    CopySource(self.bucket_name, source_path),
                )

            await asyncio.to_thread(_copy)
            return True
        except S3Error:
            return False
    async def adelete_file(self, file_path: str) -> bool:
        """Delete a file from MinIO."""
        try:
            def _remove():
                self.client.remove_object(self.bucket_name, file_path)

            await asyncio.to_thread(_remove)
            return True
        except S3Error as e:
            logger.error(f"Error deleting MinIO object {file_path}: {e}")
            return False

    async def adelete(self, file_path: str) -> bool:
        return await self.adelete_file(file_path)

