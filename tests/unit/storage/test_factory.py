"""
tests/unit/storage/test_factory.py

``default_blob_store`` and the default factories built on it. With nothing set the store is local and nothing is
compressed (existing data directories are untouched); with ``KHADBOT_STORAGE=s3`` pointed at a moto bucket, an
offline ingest of a recorded report and its digest run through the default factories, the raw pages are stored
compressed under ``raw/`` as ``.zst``, and nothing else is.
"""

from __future__ import annotations

import asyncio
import io

import polars as pl
import pytest
import zstandard
from helpers.encounter.real_fixtures import REAL_REPORTS, WCL_FIXTURES, fixture_fights
from helpers.storage.s3 import BUCKET, REGION, moto_s3_store, stored_keys

from khadbot import wiring
from khadbot.config import StorageBackend, StorageConfig
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.config_files import load_cooldowns
from khadbot.encounter.digest import build_digest
from khadbot.encounter.ingest import RECORDED_BUFF_IDS, ingest_report
from khadbot.encounter.store import ParquetEventStore
from khadbot.storage import CompressedBlobStore, LocalBlobStore, default_blob_store
from khadbot.storage.s3 import S3BlobStore

STORAGE_ENV = ("KHADBOT_STORAGE", "KHADBOT_S3_BUCKET", "KHADBOT_S3_PREFIX", "KHADBOT_S3_ENDPOINT", "KHADBOT_S3_REGION")


@pytest.fixture(autouse=True)
def _clean_storage_env(monkeypatch):
    for name in STORAGE_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setenv("KHADBOT_STORAGE", "s3")
    monkeypatch.setenv("KHADBOT_S3_BUCKET", BUCKET)
    monkeypatch.setenv("KHADBOT_S3_REGION", REGION)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_storage_config_defaults_to_local():
    config = StorageConfig()
    assert config.backend is StorageBackend.LOCAL
    assert (config.s3_bucket, config.s3_prefix, config.s3_endpoint, config.s3_region) == ("", "", "", "")


def test_storage_config_reads_the_environment(monkeypatch):
    monkeypatch.setenv("KHADBOT_STORAGE", "s3")
    monkeypatch.setenv("KHADBOT_S3_BUCKET", "b")
    monkeypatch.setenv("KHADBOT_S3_PREFIX", "p")
    monkeypatch.setenv("KHADBOT_S3_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("KHADBOT_S3_REGION", "auto")
    config = StorageConfig()
    assert config.backend is StorageBackend.S3
    assert (config.s3_bucket, config.s3_prefix, config.s3_endpoint, config.s3_region) == (
        "b",
        "p",
        "http://localhost:9000",
        "auto",
    )


def test_an_unknown_backend_is_an_error(monkeypatch):
    monkeypatch.setenv("KHADBOT_STORAGE", "gcs")
    with pytest.raises(ValueError):
        StorageConfig()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_default_is_the_local_store_and_nothing_is_compressed(monkeypatch, tmp_path):
    monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path))
    blobs = default_blob_store()
    assert isinstance(blobs, LocalBlobStore)
    assert blobs.root == tmp_path
    blobs.put("raw/ABC/report.json", b"{}")
    assert (tmp_path / "raw" / "ABC" / "report.json").read_bytes() == b"{}"


def test_explicit_local_is_the_same(monkeypatch, tmp_path):
    monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KHADBOT_STORAGE", "local")
    monkeypatch.setenv("KHADBOT_S3_BUCKET", "ignored-in-local-mode")
    assert isinstance(default_blob_store(), LocalBlobStore)


def test_s3_wraps_the_bucket_with_compression_for_raw_only(moto_s3, s3_env, monkeypatch):
    monkeypatch.setenv("KHADBOT_S3_PREFIX", "khadbot/dev")
    blobs = default_blob_store()
    assert isinstance(blobs, CompressedBlobStore)
    assert blobs.prefixes == ("raw/",)
    assert isinstance(blobs.inner, S3BlobStore)
    assert (blobs.inner.bucket, blobs.inner.prefix) == (BUCKET, "khadbot/dev")


def test_s3_needs_a_bucket(monkeypatch):
    monkeypatch.setenv("KHADBOT_STORAGE", "s3")
    with pytest.raises(ValueError, match="KHADBOT_S3_BUCKET"):
        default_blob_store()


def test_the_default_factories_follow_the_setting(moto_s3, s3_env):
    assert isinstance(wiring.raw_cache().blobs, CompressedBlobStore)
    assert isinstance(wiring.event_store().blobs, CompressedBlobStore)
    assert isinstance(wiring.annotation_cache().blobs, CompressedBlobStore)


# ---------------------------------------------------------------------------
# Ingest and digest on S3
# ---------------------------------------------------------------------------

# The smallest recorded report; the pages live in Git LFS, so the test skips without them.
SMALL_REPORT = "QkVa6GHRK8yfrTbw"


@pytest.mark.skipif(SMALL_REPORT not in REAL_REPORTS, reason="real WCL fixtures not pulled from Git LFS")
def test_offline_ingest_and_digest_through_s3(moto_s3, s3_env):
    # Put the recorded raw pages where a hosted ingest would have cached them: raw/<code>/... in the bucket.
    source = LocalBlobStore(WCL_FIXTURES)
    raw_keys = source.list(f"{SMALL_REPORT}/")
    assert raw_keys
    dest = default_blob_store()
    for key in raw_keys:
        data = source.get(key)
        assert data is not None
        dest.put(f"raw/{key}", data)

    fights = fixture_fights(SMALL_REPORT)
    result = asyncio.run(
        ingest_report(
            SMALL_REPORT,
            fights,
            store=wiring.event_store(),
            cache=wiring.raw_cache(),
            offline=True,
            defensive_spell_ids=RECORDED_BUFF_IDS,
        )
    )
    assert not result.failed, result.failed

    store, cache = wiring.event_store(), wiring.annotation_cache()
    assert isinstance(store, ParquetEventStore) and isinstance(cache, AnnotationCache)
    registry = load_cooldowns("defensive")
    digests = [build_digest(f"{SMALL_REPORT}:{fight}", store, defensive=registry, cache=cache) for fight in fights]
    assert digests
    assert all(d.pull_key.startswith(SMALL_REPORT) for d in digests)

    # What S3 holds: every raw object compressed with the .zst suffix, nothing outside raw/ compressed.
    keys = stored_keys()
    raw = [k for k in keys if k.startswith("raw/")]
    other = [k for k in keys if not k.startswith("raw/")]
    assert len(raw) == len(raw_keys)
    assert all(k.endswith(".zst") for k in raw)
    assert any(k.startswith("events/") for k in other) and any(k.startswith("annotated/") for k in other)
    assert not any(k.endswith(".zst") for k in other), other
    # events/ objects are parquet as written, readable without decompression.
    parquet = next(k for k in other if k.startswith("events/") and k.endswith(".parquet"))
    assert pl.read_parquet(io.BytesIO(moto_s3_store().get(parquet))).height > 0
    # A raw page decompresses to the bytes that were put there.
    inner = moto_s3_store()
    first = raw[0]
    expected = source.get(first.removeprefix("raw/").removesuffix(".zst"))
    assert zstandard.ZstdDecompressor().decompress(inner.get(first)) == expected
