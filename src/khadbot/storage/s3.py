"""
storage/s3.py

``S3BlobStore``: a ``BlobStore`` over one bucket of an S3-compatible service (Cloudflare R2 in production, MinIO
locally) through ``boto3``. Keys are the same ``/``-separated keys the local store uses, placed under an optional
key prefix in the bucket. Credentials are not part of the configuration: boto3 finds them the standard way
(``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY`` in the environment, a profile, or an instance role).
"""

from __future__ import annotations

import builtins
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from khadbot.storage.blob import join_key, validate_key, validate_prefix

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

# Error codes S3-compatible services return for "there is no such object".
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


def make_client(*, endpoint_url: str | None = None, region: str | None = None) -> Any:
    """
    A boto3 S3 client. With a custom endpoint (MinIO, R2) it uses path-style addressing, which both accept and
    which needs no DNS entry per bucket; retries are botocore's ``standard`` mode.
    """
    config = Config(
        retries={"mode": "standard", "max_attempts": 5},
        s3={"addressing_style": "path"} if endpoint_url else {},
    )
    return boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or None, config=config)


def _is_missing(exc: ClientError) -> bool:
    return str(exc.response.get("Error", {}).get("Code", "")) in _MISSING_CODES


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class S3BlobStore:
    """
    ``BlobStore`` over the objects in ``bucket`` whose names start with ``prefix``.

    S3 writes a whole object at once, so ``put`` is atomic: a reader sees the old object or the new one. A
    "directory" is only a key prefix, so ``get`` and ``exists`` on one report no object, as the local store does.
    The bucket must already exist.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3BlobStore needs a bucket name")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if self.prefix:
            validate_prefix(self.prefix)
        self._client = client if client is not None else make_client(endpoint_url=endpoint_url, region=region)

    def __repr__(self) -> str:
        return f"S3BlobStore(bucket={self.bucket!r}, prefix={self.prefix!r})"

    def _full(self, key: str) -> str:
        return join_key(self.prefix, validate_key(key))

    def get(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._full(key))
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise
        return response["Body"].read()

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=self._full(key), Body=data)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._full(key))
        except ClientError as exc:
            if _is_missing(exc):
                return False
            raise
        return True

    def list(self, prefix: str = "") -> builtins.list[str]:
        validate_prefix(prefix)
        base = f"{self.prefix}/" if self.prefix else ""
        paginator = self._client.get_paginator("list_objects_v2")
        keys: builtins.list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=base + prefix):
            keys.extend(obj["Key"][len(base) :] for obj in page.get("Contents", ()))
        return sorted(keys)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=self._full(key))


__all__ = ["S3BlobStore", "make_client"]
