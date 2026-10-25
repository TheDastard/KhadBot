"""
storage/__init__.py

Blob storage for everything the encounter pipeline keeps under ``KHADBOT_DATA_DIR``: the raw page cache, the event
store and the annotation cache read and write objects through a ``BlobStore`` under keys that mirror the old paths.
``default_blob_store`` is the one place that decides where those objects live: the local data directory, or with
``KHADBOT_STORAGE=s3`` a bucket in an S3-compatible service, where the raw pages (and only those) are zstd-compressed.
"""

from __future__ import annotations

import os
from pathlib import Path

from khadbot.config import EncounterConfig, StorageBackend, StorageConfig
from khadbot.errors import ConfigurationError, missing_group_message
from khadbot.storage.blob import (
    TMP_SUFFIX,
    BlobInfo,
    BlobStore,
    InvalidKeyError,
    join_key,
    validate_key,
    validate_prefix,
)
from khadbot.storage.compressed import ZST_SUFFIX, CompressedBlobStore
from khadbot.storage.local import LocalBlobStore, atomic_write

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


# Key prefixes stored zstd-compressed in hosted (S3) mode. Everything else is stored as it is: parquet is already
# compressed and the remaining JSON objects are small.
COMPRESSED_PREFIXES = ("raw/",)


def s3_blob_store(config: StorageConfig) -> BlobStore:
    """The configured bucket as an ``S3BlobStore``, with ``raw/`` compressed."""
    if not config.s3_bucket:
        raise ValueError("KHADBOT_STORAGE=s3 needs KHADBOT_S3_BUCKET")
    try:
        from khadbot.storage.s3 import S3BlobStore  # boto3 is only imported when object storage is used
    except ImportError as exc:
        raise ConfigurationError(missing_group_message("hosted", "KHADBOT_STORAGE=s3")) from exc

    inner = S3BlobStore(
        config.s3_bucket,
        config.s3_prefix,
        endpoint_url=config.s3_endpoint or None,
        region=config.s3_region or None,
    )
    return CompressedBlobStore(inner, COMPRESSED_PREFIXES)


def default_blob_store() -> BlobStore:
    """
    The pipeline's blob store: ``LocalBlobStore`` over the data directory (``KHADBOT_DATA_DIR``, the default, with
    nothing compressed), or with ``KHADBOT_STORAGE=s3`` the S3 bucket named by the ``KHADBOT_S3_*`` settings.

    Raises:
        ValueError: ``KHADBOT_STORAGE=s3`` without ``KHADBOT_S3_BUCKET``.
        ConfigurationError: ``KHADBOT_STORAGE=s3`` without the ``hosted`` dependency group installed.
    """
    config = StorageConfig()
    if config.backend is StorageBackend.S3:
        return s3_blob_store(config)
    return LocalBlobStore(Path(EncounterConfig().data_dir))


def resolve_blobs(
    target: BlobStore | str | os.PathLike[str], prefix: str | None, default_prefix: str
) -> tuple[BlobStore, str]:
    """
    The store and key prefix a persistence class reads through.

    A ``BlobStore`` is used with ``prefix`` (``default_prefix`` when None), the stage's directory name under the
    data directory. A filesystem path is the older form: the directory that holds the stage's layout directly, such
    as a recorded fixture or ``<data_dir>/raw``, so it becomes a ``LocalBlobStore`` there with no prefix unless one
    is given.
    """
    if isinstance(target, (str, os.PathLike)):
        return LocalBlobStore(Path(target)), prefix or ""
    return target, default_prefix if prefix is None else prefix


__all__ = [
    "COMPRESSED_PREFIXES",
    "TMP_SUFFIX",
    "ZST_SUFFIX",
    "BlobInfo",
    "BlobStore",
    "CompressedBlobStore",
    "InvalidKeyError",
    "LocalBlobStore",
    "atomic_write",
    "default_blob_store",
    "join_key",
    "resolve_blobs",
    "s3_blob_store",
    "validate_key",
    "validate_prefix",
]
