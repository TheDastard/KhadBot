"""
tests/unit/storage/test_blob_contract.py

Runs the ``BlobStore`` contract (``tests/helpers/storage/contract.py``) against every implementation that needs no
network or service: the local store, the S3 store over moto's in-memory S3, and the compressing wrapper over each.
Another implementation joins the suite by adding an entry to ``BLOB_STORES``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from helpers.storage.contract import BlobStoreContract
from helpers.storage.s3 import BUCKET, moto_s3_store

from khadbot.storage import BlobStore, CompressedBlobStore, LocalBlobStore

# Prefixes the compressing entries compress: both ``raw/`` and ``events/`` keys appear in the contract's tests, and
# ``annotated/`` keys stay uncompressed, so one run covers both paths.
COMPRESSED = ("raw/", "events/")

# name -> factory taking a fresh tmp_path and returning an empty store. The S3 entries need the ``moto_s3`` fixture.
BLOB_STORES: dict[str, Callable[[Path], BlobStore]] = {
    "local": lambda tmp_path: LocalBlobStore(tmp_path / "store"),
    "s3": lambda tmp_path: moto_s3_store(),
    "s3-with-prefix": lambda tmp_path: moto_s3_store(prefix="khadbot/dev"),
    "compressed-local": lambda tmp_path: CompressedBlobStore(LocalBlobStore(tmp_path / "store"), COMPRESSED),
    "compressed-s3": lambda tmp_path: CompressedBlobStore(moto_s3_store(), COMPRESSED),
}


# Entries backed by moto's in-memory S3. moto is not safe for a read that overlaps an overwrite of the same key: it
# closes the old object's buffer while the read is still using it (ValueError: I/O operation on closed file). Real
# S3 PUTs are atomic, and the hosted suite runs that test against a real S3-compatible service.
MOTO_BACKED = {"s3", "s3-with-prefix", "compressed-s3"}


@pytest.fixture(params=sorted(BLOB_STORES))
def blobs(request, tmp_path, moto_s3) -> BlobStore:
    return BLOB_STORES[request.param](tmp_path)


class TestBlobStoreContract(BlobStoreContract):
    def test_readers_see_whole_objects_during_overwrites(self, blobs: BlobStore, request: pytest.FixtureRequest):
        if request.node.callspec.params["blobs"] in MOTO_BACKED:
            pytest.skip("moto's in-memory S3 is not safe for concurrent overwrites of one key")
        super().test_readers_see_whole_objects_during_overwrites(blobs)


def test_the_s3_entries_share_one_bucket_name():
    assert BUCKET == "khadbot-test"
