"""
tests/unit/storage/test_s3.py

``S3BlobStore`` and ``CompressedBlobStore`` behaviour beyond the shared contract (``test_blob_contract.py``), over
moto's in-memory S3: how keys map to objects, listing past one page, errors that are not "missing", and what ends
up compressed.
"""

from __future__ import annotations

import boto3
import pytest
import zstandard
from botocore.exceptions import ClientError
from helpers.storage.s3 import BUCKET, REGION, moto_s3_store, stored_keys

from khadbot.storage import CompressedBlobStore, InvalidKeyError, LocalBlobStore
from khadbot.storage.s3 import S3BlobStore, make_client

# ---------------------------------------------------------------------------
# S3BlobStore
# ---------------------------------------------------------------------------


def test_keys_map_to_objects_under_the_prefix(moto_s3):
    store = moto_s3_store(prefix="/khadbot/dev/")  # stray slashes in the setting are tolerated
    store.put("raw/ABC/report.json", b"{}")
    assert stored_keys() == ["khadbot/dev/raw/ABC/report.json"]
    assert store.list() == ["raw/ABC/report.json"]


def test_prefixed_stores_do_not_see_each_other(moto_s3):
    a, b = moto_s3_store(prefix="a"), moto_s3_store(prefix="b")
    a.put("events/ABC/pulls.json", b"a")
    b.put("events/ABC/pulls.json", b"b")
    assert a.get("events/ABC/pulls.json") == b"a"
    assert b.list() == ["events/ABC/pulls.json"]
    a.delete("events/ABC/pulls.json")
    assert b.get("events/ABC/pulls.json") == b"b"


def test_a_prefix_is_a_string_prefix_not_a_directory(moto_s3):
    # Prefix "ab" must not leak keys of the sibling prefix "abc" into a listing of "ab".
    ab, abc = moto_s3_store(prefix="ab"), moto_s3_store(prefix="abc")
    abc.put("x", b"1")
    assert ab.list() == []


def test_list_pages_past_one_thousand_keys(moto_s3):
    store = moto_s3_store()
    keys = [f"events/ABC/{i:04d}.parquet" for i in range(1105)]
    for key in keys:
        store.put(key, b"x")
    assert store.list("events/") == keys


def test_an_error_that_is_not_missing_propagates(moto_s3):
    store = S3BlobStore("no-such-bucket", region=REGION)
    with pytest.raises(ClientError):
        store.get("raw/ABC/report.json")
    with pytest.raises(ClientError):
        store.exists("raw/ABC/report.json")
    with pytest.raises(ClientError):
        store.put("raw/ABC/report.json", b"x")
    with pytest.raises(ClientError):
        store.list()


def test_a_bucket_is_required():
    with pytest.raises(ValueError, match="bucket"):
        S3BlobStore("", region=REGION)


def test_an_invalid_store_prefix_is_rejected():
    with pytest.raises(InvalidKeyError):
        S3BlobStore(BUCKET, "a/../b", region=REGION)


def test_make_client_uses_path_style_only_with_an_endpoint(moto_s3):
    assert make_client(region=REGION).meta.config.s3 == {}
    custom = make_client(endpoint_url="http://localhost:9000", region=REGION)
    assert custom.meta.config.s3 == {"addressing_style": "path"}
    assert custom.meta.endpoint_url == "http://localhost:9000"


def test_a_client_can_be_injected(moto_s3):
    client = boto3.client("s3", region_name=REGION)
    S3BlobStore(BUCKET, client=client).put("events/ABC/pulls.json", b"[]")
    assert stored_keys() == ["events/ABC/pulls.json"]


# ---------------------------------------------------------------------------
# CompressedBlobStore
# ---------------------------------------------------------------------------


def test_compressed_prefixes_are_stored_as_zst_and_the_rest_as_they_are(moto_s3):
    store = CompressedBlobStore(moto_s3_store(), ("raw/",))
    page = b'{"events": [1, 2, 3]}' * 1000
    store.put("raw/ABC/1/Deaths/none/page_000.json", page)
    store.put("raw/ABC/report.json", b"{}")
    store.put("events/ABC/1.parquet", b"PAR1 not compressed")
    store.put("annotated/ABC/1__h.json", b"{}")

    assert stored_keys() == [
        "annotated/ABC/1__h.json",
        "events/ABC/1.parquet",
        "raw/ABC/1/Deaths/none/page_000.json.zst",
        "raw/ABC/report.json.zst",
    ]
    inner = moto_s3_store()
    assert inner.get("events/ABC/1.parquet") == b"PAR1 not compressed"
    assert zstandard.ZstdDecompressor().decompress(inner.get("raw/ABC/1/Deaths/none/page_000.json.zst")) == page
    assert len(inner.get("raw/ABC/1/Deaths/none/page_000.json.zst")) < len(page) / 10


def test_listing_shows_plain_keys_and_hides_foreign_objects(moto_s3):
    inner = moto_s3_store()
    store = CompressedBlobStore(inner, ("raw/",))
    store.put("raw/ABC/report.json", b"{}")
    inner.put("raw/ABC/stray.json", b"{}")  # written without the wrapper: no .zst suffix, so not an object here
    assert store.list("raw/") == ["raw/ABC/report.json"]
    assert store.list("raw/ABC/report.json") == ["raw/ABC/report.json"]
    assert store.get("raw/ABC/stray.json") is None
    assert not store.exists("raw/ABC/stray.json")


def test_a_key_that_itself_ends_in_zst_round_trips(moto_s3):
    store = CompressedBlobStore(moto_s3_store(), ("raw/",))
    store.put("raw/ABC/odd.zst", b"x")
    assert store.get("raw/ABC/odd.zst") == b"x"
    assert store.list("raw/") == ["raw/ABC/odd.zst"]


def test_compress_level_is_applied(tmp_path):
    data = bytes(range(256)) * 400
    low = CompressedBlobStore(LocalBlobStore(tmp_path / "a"), ("raw/",), level=1)
    high = CompressedBlobStore(LocalBlobStore(tmp_path / "b"), ("raw/",), level=19)
    for store in (low, high):
        store.put("raw/x", data)
        assert store.get("raw/x") == data


@pytest.mark.parametrize("prefix", ["raw", "", "raw//", "../raw/"])
def test_compressed_prefixes_must_be_directories(tmp_path, prefix):
    with pytest.raises((ValueError, InvalidKeyError)):
        CompressedBlobStore(LocalBlobStore(tmp_path), (prefix,))


def test_corrupt_compressed_data_raises(tmp_path):
    inner = LocalBlobStore(tmp_path)
    inner.put("raw/x.zst", b"not a zstd frame")
    with pytest.raises(zstandard.ZstdError):
        CompressedBlobStore(inner, ("raw/",)).get("raw/x")
