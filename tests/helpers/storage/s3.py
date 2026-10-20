"""
tests/helpers/storage/s3.py

Helpers for tests of the S3 blob store over moto's in-memory S3: a ``moto_s3`` fixture (in ``tests/conftest.py``)
creates the bucket, and ``moto_s3_store`` builds an ``S3BlobStore`` over it.
"""

from __future__ import annotations

import boto3

from khadbot.storage.s3 import S3BlobStore

BUCKET = "khadbot-test"
REGION = "us-east-1"


def create_bucket(name: str = BUCKET) -> None:
    """Create an empty bucket in the active moto mock."""
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=name)


def moto_s3_store(prefix: str = "", bucket: str = BUCKET) -> S3BlobStore:
    """An ``S3BlobStore`` on a bucket of the active moto mock."""
    return S3BlobStore(bucket, prefix, region=REGION)


def stored_keys(bucket: str = BUCKET) -> list[str]:
    """Every object key in the bucket, as S3 holds them (``.zst`` suffixes and store prefixes included)."""
    client = boto3.client("s3", region_name=REGION)
    return sorted(
        o["Key"]
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for o in page.get("Contents", ())
    )
