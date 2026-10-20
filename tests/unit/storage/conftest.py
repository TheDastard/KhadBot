"""
tests/unit/storage/conftest.py

``moto_s3``: an in-memory S3 service with one empty bucket (``helpers.storage.s3.BUCKET``). boto3 clients created
while it is active talk to moto, never to a network. The service is started once per test module (starting it per
test costs a quarter of a second each); every test gets the bucket emptied.
"""

from __future__ import annotations

import pytest
from helpers.storage.s3 import BUCKET, REGION, create_bucket
from moto import mock_aws


@pytest.fixture(scope="module")
def _moto_service():
    mp = pytest.MonkeyPatch()
    # An endpoint override in the developer's environment would bypass moto.
    mp.delenv("AWS_ENDPOINT_URL", raising=False)
    mp.delenv("AWS_ENDPOINT_URL_S3", raising=False)
    with mock_aws():
        create_bucket()
        yield
    mp.undo()


@pytest.fixture
def moto_s3(_moto_service):
    import boto3

    yield
    boto3.resource("s3", region_name=REGION).Bucket(BUCKET).objects.delete()
