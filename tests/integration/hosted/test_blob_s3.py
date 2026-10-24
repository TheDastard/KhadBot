"""
tests/integration/hosted/test_blob_s3.py

The ``BlobStore`` contract against a real S3-compatible service (SeaweedFS locally, R2 in production): the S3 store and
the compressing wrapper over it. Skipped unless ``KHADBOT_S3_BUCKET`` is set; credentials come from the standard
``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY`` variables, and ``KHADBOT_S3_ENDPOINT`` and ``KHADBOT_S3_REGION``
apply as they do in the app. The bucket must already exist. Each test works under its own key prefix and deletes
what it wrote, so the bucket can be shared. CI has no S3 service yet, so nothing runs this automatically.
"""

from __future__ import annotations

import os
import uuid

import pytest
from helpers.storage.contract import BlobStoreContract

from khadbot.config import StorageConfig
from khadbot.storage import BlobStore, CompressedBlobStore
from khadbot.storage.s3 import S3BlobStore


@pytest.fixture(scope="module")
def s3_config() -> StorageConfig:
    if not os.environ.get("KHADBOT_S3_BUCKET"):
        pytest.skip("KHADBOT_S3_BUCKET is not set; these tests need an S3-compatible service such as SeaweedFS")
    return StorageConfig()


@pytest.fixture
def s3_store(s3_config) -> S3BlobStore:
    store = S3BlobStore(
        s3_config.s3_bucket,
        f"{s3_config.s3_prefix}/tests/{uuid.uuid4().hex[:12]}".lstrip("/"),
        endpoint_url=s3_config.s3_endpoint or None,
        region=s3_config.s3_region or None,
    )
    yield store
    for key in store.list():
        store.delete(key)


class TestS3BlobStore(BlobStoreContract):
    @pytest.fixture
    def blobs(self, s3_store) -> BlobStore:
        return s3_store


class TestCompressedS3BlobStore(BlobStoreContract):
    @pytest.fixture
    def blobs(self, s3_store) -> BlobStore:
        return CompressedBlobStore(s3_store, ("raw/", "events/"))
