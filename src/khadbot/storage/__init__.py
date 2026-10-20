"""
storage/__init__.py

Blob storage for everything the encounter pipeline keeps under ``KHADBOT_DATA_DIR``: the raw page cache, the event
store and the annotation cache read and write objects through a ``BlobStore`` under keys that mirror the old paths.
``default_blob_store`` is the one place that decides where those objects live; today that is always the local data
directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from khadbot.config import EncounterConfig
from khadbot.storage.blob import TMP_SUFFIX, BlobStore, InvalidKeyError, join_key, validate_key, validate_prefix
from khadbot.storage.local import LocalBlobStore, atomic_write

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def default_blob_store() -> BlobStore:
    """The data directory (``KHADBOT_DATA_DIR``) as a ``LocalBlobStore``."""
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
    "TMP_SUFFIX",
    "BlobStore",
    "InvalidKeyError",
    "LocalBlobStore",
    "atomic_write",
    "default_blob_store",
    "join_key",
    "resolve_blobs",
    "validate_key",
    "validate_prefix",
]
