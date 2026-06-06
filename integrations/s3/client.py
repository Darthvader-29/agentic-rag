"""Async S3 client wrapping boto3 — works against any S3-compatible store.

`endpoint_url` selects the backend:
- production: set S3_ENDPOINT_URL to the provider, e.g. Backblaze B2 at
  https://s3.<region>.backblazeb2.com. Leave it unset only to target real AWS S3.
- development: defaults to MinIO at http://localhost:9000 unless S3_ENDPOINT_URL is set.

Checksum note: botocore >= 1.36 emits `x-amz-checksum-crc32` request trailers by
default, which Backblaze B2 / Cloudflare R2 / MinIO reject (HTTP 400/501). We pin
request/response checksums to "when_required" so they are only sent when an
operation truly needs them — harmless on real AWS, required for S3-compatible stores.
"""

import asyncio
import os
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from tenacity import retry

from integrations._retry import RETRY_KW


class S3Client:
    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint_url: str | None = None,
    ):
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint_url,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                # botocore >= 1.36 sends x-amz-checksum-crc32 by default; B2/R2/MinIO
                # reject it (400/501). Only send checksums when an op requires them.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    @classmethod
    def from_settings(cls, settings) -> "S3Client":
        endpoint = settings.S3_ENDPOINT_URL
        if endpoint is None and settings.ENVIRONMENT == "development":
            endpoint = "http://localhost:9000"
        return cls(
            bucket=settings.S3_BUCKET_NAME,
            region=settings.AWS_REGION,
            access_key=settings.AWS_ACCESS_KEY_ID,
            secret_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=endpoint,
        )

    @staticmethod
    def _make_key(filename: str) -> str:
        return f"uploads/{uuid.uuid4()}_{filename}"

    @staticmethod
    def make_user_key(user_id: object, filename: str) -> str:
        """Phase 5 presigned-upload key, scoped under the owning user."""
        # Same `uploads/{uuid}_{filename}` tail as _make_key, nested under the user_id.
        return f"uploads/{user_id}/{S3Client._make_key(filename).removeprefix('uploads/')}"

    @retry(**RETRY_KW)
    def _upload_sync(self, file_obj, key: str) -> None:
        self._client.upload_fileobj(file_obj, self._bucket, key)

    async def upload_fileobj(self, file_obj, filename: str) -> str:
        key = self._make_key(filename)
        await asyncio.to_thread(self._upload_sync, file_obj, key)
        return key

    @retry(**RETRY_KW)
    def _download_sync(self, key: str) -> str:
        tmp_dir = "tmp_uploads"
        os.makedirs(tmp_dir, exist_ok=True)
        local_path = os.path.join(tmp_dir, key.replace("/", "_"))
        with open(local_path, "wb") as f:
            self._client.download_fileobj(self._bucket, key, f)
        return local_path

    async def download_to_temp(self, key: str) -> str:
        return await asyncio.to_thread(self._download_sync, key)

    @retry(**RETRY_KW)
    def _delete_sync(self, keys: list[str]) -> None:
        if not keys:
            return
        self._client.delete_objects(
            Bucket=self._bucket,
            Delete={"Objects": [{"Key": k} for k in keys]},
        )

    async def delete_objects(self, keys: list[str]) -> None:
        await asyncio.to_thread(self._delete_sync, keys)

    # ── Phase 5: presigned PUT + existence check (client uploads direct to storage) ──

    @retry(**RETRY_KW)
    def _presign_put_sync(self, key: str, expires_in: int) -> str:
        url: str = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return url

    async def generate_presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        """A presigned PUT URL the client uploads to directly (bytes never touch the API)."""
        return await asyncio.to_thread(self._presign_put_sync, key, expires_in)

    def _head_sync(self, key: str) -> bool:
        # Not retried: a 404 is a valid "not uploaded yet" answer, not a transient error.
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    async def object_exists(self, key: str) -> bool:
        """True if the object landed in storage — used by /api/upload/confirm to close the race."""
        return await asyncio.to_thread(self._head_sync, key)
