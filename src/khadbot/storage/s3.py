"""
storage/s3.py

``S3BlobStore``: a ``BlobStore`` over one bucket of an S3-compatible service (Cloudflare R2 in production, SeaweedFS
locally) through ``boto3``. Keys are the same ``/``-separated keys the local store uses, placed under an optional
key prefix in the bucket. Credentials are not part of the configuration: boto3 finds them the standard way
(``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY`` in the environment, a profile, or an instance role).

A failed operation logs one ``blob.error`` record at WARNING with the operation and the S3 error code, then raises as
before. The record names only the key's top-level prefix (``records/``), since deeper parts can hold user IDs, and
never a credential. A missing object is an answer, not a failure, and successful operations log nothing.
"""

from __future__ import annotations

import builtins
import contextlib
import logging
from collections.abc import Iterator
from datetime import UTC
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from khadbot.storage.blob import BlobInfo, join_key, validate_key, validate_prefix

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

# Error codes S3-compatible services return for "there is no such object".
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


def make_client(*, endpoint_url: str | None = None, region: str | None = None) -> Any:
    """
    A boto3 S3 client. With a custom endpoint (SeaweedFS, R2) it uses path-style addressing, which both accept and
    which needs no DNS entry per bucket; retries are botocore's ``standard`` mode.
    """
    config = Config(
        retries={"mode": "standard", "max_attempts": 5},
        s3={"addressing_style": "path"} if endpoint_url else {},
    )
    return boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or None, config=config)


def _is_missing(exc: ClientError) -> bool:
    return str(exc.response.get("Error", {}).get("Code", "")) in _MISSING_CODES


def _error_status(exc: Exception) -> str:
    """The S3 error code of a failure (``AccessDenied``), else its HTTP status, else the botocore error's class name."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        if code:
            return str(code)
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status:
            return str(status)
    return type(exc).__name__


def _top_prefix(key: str) -> str:
    """A key's first segment with its slash (``records/``), the only part of a key a log record may carry."""
    head, sep, _ = key.partition("/")
    return f"{head}{sep}" if sep else "(root)"


@contextlib.contextmanager
def _logged_failure(op: str, key: str) -> Iterator[None]:
    """Log a ``blob.error`` record for an S3 failure inside the block, then let it propagate unchanged."""
    try:
        yield
    except (ClientError, BotoCoreError) as exc:
        status = _error_status(exc)
        try:
            logger.warning(
                "S3 %s failed under %s: %s",
                op,
                _top_prefix(key),
                status,
                extra={"event": "blob.error", "op": op, "status": status},
            )
        except Exception:  # noqa: BLE001, S110 - logging must never replace the store's own error
            pass
        raise


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
        full = self._full(key)
        with _logged_failure("get", key):
            try:
                response = self._client.get_object(Bucket=self.bucket, Key=full)
            except ClientError as exc:
                if _is_missing(exc):
                    return None
                raise
            data: bytes = response["Body"].read()
        return data

    def put(self, key: str, data: bytes) -> None:
        full = self._full(key)
        with _logged_failure("put", key):
            self._client.put_object(Bucket=self.bucket, Key=full, Body=data)

    def exists(self, key: str) -> bool:
        full = self._full(key)
        with _logged_failure("exists", key):
            try:
                self._client.head_object(Bucket=self.bucket, Key=full)
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
        with _logged_failure("list", prefix):
            for page in paginator.paginate(Bucket=self.bucket, Prefix=base + prefix):
                keys.extend(obj["Key"][len(base) :] for obj in page.get("Contents", ()))
        return sorted(keys)

    def list_info(self, prefix: str = "") -> builtins.list[BlobInfo]:
        validate_prefix(prefix)
        base = f"{self.prefix}/" if self.prefix else ""
        paginator = self._client.get_paginator("list_objects_v2")
        infos: builtins.list[BlobInfo] = []
        with _logged_failure("list_info", prefix):
            for page in paginator.paginate(Bucket=self.bucket, Prefix=base + prefix):
                infos.extend(
                    BlobInfo(obj["Key"][len(base) :], obj["LastModified"].astimezone(UTC), obj["Size"])
                    for obj in page.get("Contents", ())
                )
        return sorted(infos, key=lambda info: info.key)

    def delete(self, key: str) -> None:
        full = self._full(key)
        with _logged_failure("delete", key):
            self._client.delete_object(Bucket=self.bucket, Key=full)


__all__ = ["S3BlobStore", "make_client"]
