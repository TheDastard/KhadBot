"""
storage/blob.py

The ``BlobStore`` protocol: whole objects under ``/``-separated keys, with an atomic put. Keys mirror the paths under
``KHADBOT_DATA_DIR`` (``raw/<report>/...``, ``events/<report>/...``, ``annotated/<report>/...``), so a filesystem
store over an existing data directory reads it unchanged and an object store holds the same layout.

``validate_key`` and ``validate_prefix`` are the one definition of a legal key; every implementation calls them.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

# Characters a Windows path can't hold, so a key that works in an object store also works on local disk.
_FORBIDDEN = frozenset('\\:*?"<>|')

# Suffix of the temporary files a local put writes before its rename. Keys can't end with it, so listings skip them.
TMP_SUFFIX = ".tmp"


class InvalidKeyError(ValueError):
    """A key or prefix that is absolute, empty, escapes the store, or holds characters a filesystem can't."""


def _check_segments(text: str, what: str) -> None:
    for segment in text.split("/"):
        if segment in ("", ".", ".."):
            raise InvalidKeyError(f"{what} {text!r} has an empty, '.' or '..' segment")
    if bad := sorted({c for c in text if c in _FORBIDDEN or ord(c) < 32}):
        raise InvalidKeyError(f"{what} {text!r} holds characters not allowed in a key: {bad!r}")


def validate_key(key: str) -> str:
    """
    Return ``key`` if it names an object: relative, ``/``-separated, no empty, ``.`` or ``..`` segments, no
    backslashes, colons or other characters a Windows path can't hold, and not ending in ``.tmp``.

    Raises:
        InvalidKeyError: The key breaks one of those rules.
    """
    if not isinstance(key, str) or not key:
        raise InvalidKeyError(f"key must be a non-empty string, got {key!r}")
    _check_segments(key, "key")
    if key.endswith(TMP_SUFFIX):
        raise InvalidKeyError(f"key {key!r} ends with {TMP_SUFFIX!r}, which is reserved for in-progress writes")
    return key


def validate_prefix(prefix: str) -> str:
    """
    Return ``prefix`` if it can start a listing: ``""`` (everything), a directory ending in ``/``, or the start
    of a key. The same rules as ``validate_key`` apply to every segment before the last.

    Raises:
        InvalidKeyError: The prefix breaks one of those rules.
    """
    if not isinstance(prefix, str):
        raise InvalidKeyError(f"prefix must be a string, got {prefix!r}")
    if prefix:
        _check_segments(prefix.removesuffix("/"), "prefix")
    return prefix


def join_key(*parts: str) -> str:
    """Join key parts with ``/``, dropping empty ones, so a store's key prefix can be ``""``."""
    return "/".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlobInfo:
    """One stored object: its key, when it was last written (timezone-aware, UTC) and its stored size in bytes."""

    key: str
    modified: datetime
    size: int


@runtime_checkable
class BlobStore(Protocol):
    """
    Whole-object storage under validated keys.

    ``put`` is atomic: a concurrent ``get`` sees the old object or the new one, never part of one. ``list``
    returns every key under a prefix, recursively and sorted. Deleting a missing key is not an error.
    """

    def get(self, key: str) -> bytes | None:
        """The object's bytes, or None if there is no object at ``key``."""
        ...

    def put(self, key: str, data: bytes) -> None:
        """Write ``data`` at ``key``, replacing any object there."""
        ...

    def exists(self, key: str) -> bool:
        """True if an object is stored at ``key``."""
        ...

    def list(self, prefix: str = "") -> builtins.list[str]:
        """Sorted keys starting with ``prefix``. A prefix ending in ``/`` lists that directory, recursively."""
        ...

    def list_info(self, prefix: str = "") -> builtins.list[BlobInfo]:
        """``list`` with each object's last-write time and size (retention decides what to remove by age)."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object at ``key`` if there is one."""
        ...


__all__ = ["TMP_SUFFIX", "BlobInfo", "BlobStore", "InvalidKeyError", "join_key", "validate_key", "validate_prefix"]
