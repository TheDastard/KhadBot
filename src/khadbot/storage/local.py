"""
storage/local.py

``LocalBlobStore``: a ``BlobStore`` over a directory, where key ``a/b/c.json`` is the file ``<root>/a/b/c.json``.
Over ``KHADBOT_DATA_DIR`` it reads and writes exactly the files the path-based caches did, so an existing data
directory works unchanged. ``atomic_write`` is the write-then-rename every put goes through.
"""

from __future__ import annotations

import builtins
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from khadbot.storage.blob import TMP_SUFFIX, BlobInfo, validate_key, validate_prefix

# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


# On Windows, replacing a file that a reader has open, or opening one while it is being replaced, fails with
# PermissionError (a sharing violation). Both sides hold a file for one read or one rename, so each retries for up
# to five seconds, starting at 2 ms and doubling to at most 50 ms between tries. A fixed count of doubling retries
# gave up after about two seconds, which a reader re-reading the same file in a loop could outlast.
_SHARING_DEADLINE_S = 5.0
_SHARING_FIRST_DELAY_S = 0.002
_SHARING_MAX_DELAY_S = 0.05


def _retry_sharing[T](op: Callable[[], T], *, give_up: Callable[[], bool] = lambda: False) -> T:
    """Run ``op``, retrying on PermissionError unless ``give_up()`` says the error is permanent."""
    deadline = time.monotonic() + _SHARING_DEADLINE_S
    delay = _SHARING_FIRST_DELAY_S
    while True:
        try:
            return op()
        except PermissionError:
            if time.monotonic() >= deadline or give_up():
                raise
            time.sleep(delay)
            delay = min(delay * 2, _SHARING_MAX_DELAY_S)


def atomic_write(path: Path, data: bytes) -> None:
    """
    Write ``data`` to ``path`` so readers never see a partial file: write a uniquely named sibling ending in
    ``.tmp``, then ``os.replace`` it onto ``path``. The temporary file is removed if anything fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:12]}{TMP_SUFFIX}")
    try:
        tmp.write_bytes(data)
        _retry_sharing(lambda: os.replace(tmp, path))
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class LocalBlobStore:
    """``BlobStore`` over the files under ``root``. The directory is created on the first put."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def __repr__(self) -> str:
        return f"LocalBlobStore({str(self.root)!r})"

    def path(self, key: str) -> Path:
        """The file that holds ``key``."""
        return self.root.joinpath(*validate_key(key).split("/"))

    def get(self, key: str) -> bytes | None:
        path = self.path(key)
        try:
            # Windows also reports reading a directory as PermissionError; that one is not retried.
            return _retry_sharing(path.read_bytes, give_up=path.is_dir)
        except FileNotFoundError:
            return None
        except (IsADirectoryError, PermissionError):
            if path.is_dir():  # a directory of keys is not an object
                return None
            raise

    def put(self, key: str, data: bytes) -> None:
        atomic_write(self.path(key), data)

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def list(self, prefix: str = "") -> builtins.list[str]:
        validate_prefix(prefix)
        # Walk only the deepest directory the prefix names; the rest of the prefix filters names in it.
        directory, _, _ = prefix.rpartition("/")
        base = self.root.joinpath(*directory.split("/")) if directory else self.root
        keys: builtins.list[str] = []
        stack = [(base, f"{directory}/" if directory else "")]
        while stack:
            current, key_prefix = stack.pop()
            try:
                entries = list(os.scandir(current))
            except (FileNotFoundError, NotADirectoryError):
                continue
            for entry in entries:
                key = key_prefix + entry.name
                if entry.is_dir():
                    if key.startswith(prefix):
                        stack.append((Path(entry.path), key + "/"))
                elif key.startswith(prefix) and not entry.name.endswith(TMP_SUFFIX):
                    keys.append(key)
        return sorted(keys)

    def list_info(self, prefix: str = "") -> builtins.list[BlobInfo]:
        infos: builtins.list[BlobInfo] = []
        for key in self.list(prefix):
            try:
                stat = self.path(key).stat()
            except FileNotFoundError:  # deleted since the listing
                continue
            infos.append(BlobInfo(key, datetime.fromtimestamp(stat.st_mtime, tz=UTC), stat.st_size))
        return infos

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)


__all__ = ["LocalBlobStore", "atomic_write"]
