"""
tests/helpers/storage/contract.py

The behaviour every ``BlobStore`` implementation must have, as a test class. A test module subclasses
``BlobStoreContract`` and provides a ``blobs`` fixture returning an empty store; the unit suite parametrises that
fixture over every implementation it can build without a network (``tests/unit/storage/test_blob_contract.py``), and
an integration suite can run the same class against a real service.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from khadbot.storage import BlobStore, InvalidKeyError

INVALID_KEYS = [
    "",
    "/abs/key",
    "trailing/",
    "a//b",
    "./a",
    "a/./b",
    "../escape",
    "a/../../escape",
    "back\\slash",
    "C:/drive",
    "colon:name",
    "star*",
    "quest?ion",
    'quo"te',
    "angle<bracket>",
    "pi|pe",
    "control\x01char",
    "page_000.json.tmp",
]

INVALID_PREFIXES = ["/abs", "a//b", "../up", "a/../b", "back\\slash", "colon:"]


class BlobStoreContract:
    """Subclass with a ``blobs`` fixture that returns an empty ``BlobStore``."""

    # --- get and put ---------------------------------------------------------

    def test_is_a_blob_store(self, blobs: BlobStore):
        assert isinstance(blobs, BlobStore)

    def test_get_missing_is_none(self, blobs: BlobStore):
        assert blobs.get("raw/ABC/report.json") is None
        assert not blobs.exists("raw/ABC/report.json")

    def test_put_then_get(self, blobs: BlobStore):
        blobs.put("raw/ABC/report.json", b'{"title": "x"}')
        assert blobs.get("raw/ABC/report.json") == b'{"title": "x"}'
        assert blobs.exists("raw/ABC/report.json")

    def test_empty_object(self, blobs: BlobStore):
        # The raw cache's _complete marker is an empty object; it must exist, not read as missing.
        blobs.put("raw/ABC/1/Deaths/none/_complete", b"")
        assert blobs.get("raw/ABC/1/Deaths/none/_complete") == b""
        assert blobs.exists("raw/ABC/1/Deaths/none/_complete")

    def test_binary_round_trip(self, blobs: BlobStore):
        data = bytes(range(256)) * 4096  # 1 MiB with every byte value
        blobs.put("events/ABC/1.parquet", data)
        assert blobs.get("events/ABC/1.parquet") == data

    def test_overwrite_replaces(self, blobs: BlobStore):
        blobs.put("events/ABC/pulls.json", b"a much longer first version")
        blobs.put("events/ABC/pulls.json", b"short")
        assert blobs.get("events/ABC/pulls.json") == b"short"
        assert blobs.list("events/") == ["events/ABC/pulls.json"]

    def test_a_directory_of_keys_is_not_an_object(self, blobs: BlobStore):
        blobs.put("events/ABC/pulls.json", b"[]")
        assert blobs.get("events/ABC") is None
        assert not blobs.exists("events/ABC")

    def test_readers_see_whole_objects_during_overwrites(self, blobs: BlobStore):
        old, new = b"a" * 1_000_000, b"b" * 2_000_000
        blobs.put("annotated/ABC/1__h.parquet", old)
        seen: set[bytes] = set()
        errors: list[BaseException] = []

        def write() -> None:
            try:
                for i in range(20):
                    blobs.put("annotated/ABC/1__h.parquet", new if i % 2 == 0 else old)
            except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(exc)

        writer = threading.Thread(target=write)
        writer.start()
        while writer.is_alive():
            data = blobs.get("annotated/ABC/1__h.parquet")
            assert data is not None
            seen.add(data)
        writer.join()
        assert not errors, errors
        assert seen <= {old, new}

    # --- list ----------------------------------------------------------------

    def test_list_is_recursive_and_sorted(self, blobs: BlobStore):
        keys = [
            "raw/ABC/report.json",
            "raw/ABC/1/Deaths/none/page_001.json",
            "raw/ABC/1/Deaths/none/page_000.json",
            "raw/ABC/1/Deaths/none/_complete",
            "events/ABC/1.parquet",
        ]
        for key in keys:
            blobs.put(key, b"x")
        assert blobs.list("raw/") == sorted(k for k in keys if k.startswith("raw/"))
        assert blobs.list("raw/ABC/1/Deaths/none/") == [
            "raw/ABC/1/Deaths/none/_complete",
            "raw/ABC/1/Deaths/none/page_000.json",
            "raw/ABC/1/Deaths/none/page_001.json",
        ]
        assert blobs.list() == sorted(keys)
        assert blobs.list("") == sorted(keys)

    def test_list_prefix_is_a_string_prefix(self, blobs: BlobStore):
        for key in ("raw/AB/report.json", "raw/ABC/report.json", "raw/XYZ/report.json"):
            blobs.put(key, b"{}")
        assert blobs.list("raw/AB") == ["raw/AB/report.json", "raw/ABC/report.json"]
        assert blobs.list("raw/AB/") == ["raw/AB/report.json"]
        assert blobs.list("raw/ABC/report") == ["raw/ABC/report.json"]
        assert blobs.list("raw/ABC/report.json") == ["raw/ABC/report.json"]

    def test_list_missing_prefix_is_empty(self, blobs: BlobStore):
        assert blobs.list() == []
        assert blobs.list("raw/") == []
        blobs.put("raw/ABC/report.json", b"{}")
        assert blobs.list("events/") == []
        assert blobs.list("raw/ABC/report.json/") == []

    def test_list_info_matches_list_and_dates_each_object(self, blobs: BlobStore):
        before = datetime.now(UTC) - timedelta(minutes=5)
        keys = ["raw/ABC/report.json", "records/ABC/1__abc.json", "events/ABC/1.parquet"]
        for key in keys:
            blobs.put(key, b"payload")
        after = datetime.now(UTC) + timedelta(minutes=5)

        infos = blobs.list_info()
        assert [i.key for i in infos] == blobs.list() == sorted(keys)
        for info in infos:
            assert info.modified.tzinfo is not None
            assert before <= info.modified <= after
            assert info.size > 0
        assert [i.key for i in blobs.list_info("records/")] == ["records/ABC/1__abc.json"]
        assert blobs.list_info("missing/") == []

    def test_list_info_sees_an_overwrite_as_newer(self, blobs: BlobStore):
        blobs.put("records/ABC/1__a.json", b"one")
        first = blobs.list_info("records/")[0].modified
        time.sleep(1.1)  # S3 timestamps have one-second resolution
        blobs.put("records/ABC/1__a.json", b"two")
        assert blobs.list_info("records/")[0].modified > first

    # --- delete --------------------------------------------------------------

    def test_delete(self, blobs: BlobStore):
        blobs.put("events/ABC/positions/1.parquet", b"x")
        blobs.put("events/ABC/positions/2.parquet", b"y")
        blobs.delete("events/ABC/positions/1.parquet")
        assert blobs.get("events/ABC/positions/1.parquet") is None
        assert not blobs.exists("events/ABC/positions/1.parquet")
        assert blobs.list("events/") == ["events/ABC/positions/2.parquet"]

    def test_delete_missing_is_not_an_error(self, blobs: BlobStore):
        blobs.delete("events/ABC/positions/1.parquet")
        assert blobs.list() == []

    # --- keys ----------------------------------------------------------------

    @pytest.mark.parametrize("key", INVALID_KEYS)
    def test_invalid_keys_are_rejected_everywhere(self, blobs: BlobStore, key: str):
        with pytest.raises(InvalidKeyError):
            blobs.get(key)
        with pytest.raises(InvalidKeyError):
            blobs.put(key, b"x")
        with pytest.raises(InvalidKeyError):
            blobs.exists(key)
        with pytest.raises(InvalidKeyError):
            blobs.delete(key)
        assert blobs.list() == []

    @pytest.mark.parametrize("prefix", INVALID_PREFIXES)
    def test_invalid_prefixes_are_rejected(self, blobs: BlobStore, prefix: str):
        with pytest.raises(InvalidKeyError):
            blobs.list(prefix)

    def test_keys_with_dots_dashes_and_underscores(self, blobs: BlobStore):
        key = "annotated/H7z1fqDXjRTFnbtg/10__3c12a331a193.parquet"
        blobs.put(key, b"x")
        blobs.put("raw/ABC/1/Buffs-offensive/none/page_000.json", b"y")
        assert blobs.list("annotated/") == [key]
        assert blobs.get("raw/ABC/1/Buffs-offensive/none/page_000.json") == b"y"
