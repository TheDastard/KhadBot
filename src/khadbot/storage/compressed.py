"""
storage/compressed.py

``CompressedBlobStore``: wraps a ``BlobStore`` and zstd-compresses the objects whose keys start with one of the given
prefixes. The compressed object is stored under the key plus ``.zst``; callers keep using the plain key, and listings
show the plain keys. Hosted mode compresses ``raw/`` only (the raw WCL pages, which are large and compress well);
parquet, which is already compressed, and the small JSON files are stored as they are.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterable
from typing import Any

from khadbot.errors import missing_group_message
from khadbot.storage.blob import BlobInfo, BlobStore, validate_key, validate_prefix

# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

ZST_SUFFIX = ".zst"


def _zstd() -> Any:
    """The ``zstandard`` module, imported when a compressed store is first used (it is in the hosted group)."""
    try:
        import zstandard
    except ImportError as exc:
        raise ImportError(missing_group_message("hosted", "Compressed object storage")) from exc
    return zstandard


# Level 9 gives 27% smaller objects than zstd's default level 3 on the recorded raw pages for about 4x the CPU
# (58 MB/s against 253 MB/s), which is still far faster than WCL delivers pages. Decompression speed does not depend
# on the level. docs/wcl-api.md ("Raw page compression") has the measurements.
DEFAULT_LEVEL = 9


class CompressedBlobStore:
    """
    ``BlobStore`` that stores keys under ``prefixes`` zstd-compressed, as ``<key>.zst`` in ``inner``.

    Each prefix is a directory prefix ending in ``/`` (``"raw/"``). Objects under those prefixes that are not
    ``.zst`` objects are not seen by this store, so ``list`` never returns a key ``get`` can't read.
    """

    def __init__(self, inner: BlobStore, prefixes: Iterable[str], *, level: int = DEFAULT_LEVEL) -> None:
        self.inner = inner
        self.prefixes = tuple(prefixes)
        for prefix in self.prefixes:
            if not prefix.endswith("/") or not validate_prefix(prefix):
                raise ValueError(f"compressed prefix {prefix!r} must be a directory prefix ending in '/'")
        self.level = level

    def __repr__(self) -> str:
        return f"CompressedBlobStore({self.inner!r}, prefixes={self.prefixes!r})"

    def _compressed(self, key: str) -> bool:
        return key.startswith(self.prefixes)

    def get(self, key: str) -> bytes | None:
        if not self._compressed(validate_key(key)):
            return self.inner.get(key)
        data = self.inner.get(key + ZST_SUFFIX)
        # A fresh decompressor per call: zstandard's objects are not safe to share between threads.
        return None if data is None else _zstd().ZstdDecompressor().decompress(data)

    def put(self, key: str, data: bytes) -> None:
        if not self._compressed(validate_key(key)):
            self.inner.put(key, data)
            return
        self.inner.put(key + ZST_SUFFIX, _zstd().ZstdCompressor(level=self.level).compress(data))

    def exists(self, key: str) -> bool:
        return self.inner.exists(key + ZST_SUFFIX if self._compressed(validate_key(key)) else key)

    def list(self, prefix: str = "") -> builtins.list[str]:
        validate_prefix(prefix)
        keys: builtins.list[str] = []
        for stored in self.inner.list(prefix):
            if self._compressed(stored):
                if not stored.endswith(ZST_SUFFIX):
                    continue
                stored = stored.removesuffix(ZST_SUFFIX)
            if stored.startswith(prefix):
                keys.append(stored)
        return sorted(keys)

    def list_info(self, prefix: str = "") -> builtins.list[BlobInfo]:
        validate_prefix(prefix)
        infos: builtins.list[BlobInfo] = []
        for info in self.inner.list_info(prefix):
            key = info.key
            if self._compressed(key):
                if not key.endswith(ZST_SUFFIX):
                    continue
                key = key.removesuffix(ZST_SUFFIX)
            if key.startswith(prefix):
                infos.append(BlobInfo(key, info.modified, info.size))
        return sorted(infos, key=lambda i: i.key)

    def delete(self, key: str) -> None:
        self.inner.delete(key + ZST_SUFFIX if self._compressed(validate_key(key)) else key)


__all__ = ["DEFAULT_LEVEL", "ZST_SUFFIX", "CompressedBlobStore"]
