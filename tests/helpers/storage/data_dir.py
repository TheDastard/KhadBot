"""
tests/helpers/storage/data_dir.py

A data directory built from the recorded WCL fixtures, and a manifest of its bytes, for the data directory
compatibility tests (``tests/unit/storage/test_data_dir_compat.py``).

``build_fixture_data_dir`` ingests every recorded fixture pull offline and builds its digest through the annotation
cache, so the directory holds ``events/`` and ``annotated/`` exactly as ingest and digest leave them. It uses only
constructors and functions that the path-based persistence classes also had (``form="path"``), so the golden
manifest can be produced by code from before the blob store; ``form="blobs"`` builds the same directory through a
``LocalBlobStore`` with the default key prefixes, the way the default factories do.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Literal

from helpers.encounter.real_fixtures import REAL_PULLS, REAL_REPORTS, ingest_fixture
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.config_files import load_cooldowns
from khadbot.encounter.digest import build_digest
from khadbot.encounter.models import PullDigest
from khadbot.encounter.store import ParquetEventStore

# Stage directories the manifest covers. raw/ is not written by an offline ingest.
MANIFEST_DIRS = ("events", "annotated")


def stores(root: Path, form: Literal["path", "blobs"]) -> tuple[ParquetEventStore, AnnotationCache]:
    """The event store and annotation cache over ``root``, built the old way or through a blob store."""
    if form == "path":
        return ParquetEventStore(root / "events"), AnnotationCache(root / "annotated")
    from khadbot.storage import LocalBlobStore

    blobs = LocalBlobStore(root)
    return ParquetEventStore(blobs), AnnotationCache(blobs)


def build_fixture_data_dir(root: Path, form: Literal["path", "blobs"] = "blobs") -> dict[str, PullDigest]:
    """Ingest every recorded fixture pull offline into ``root`` and digest it through the annotation cache."""
    store, cache = stores(root, form)
    for code in REAL_REPORTS:
        result = asyncio.run(ingest_fixture(code, store))
        assert not result.failed, result.failed
    registry = load_cooldowns("defensive")
    return {key: build_digest(key, store, defensive=registry, cache=cache) for key in REAL_PULLS}


def manifest(root: Path) -> dict[str, str]:
    """SHA-256 of every file under the manifest's stage directories, by ``/``-separated path relative to ``root``."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for stage in MANIFEST_DIRS
        for path in sorted((root / stage).rglob("*"))
        if path.is_file()
    }
