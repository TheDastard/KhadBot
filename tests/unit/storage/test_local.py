"""
tests/unit/storage/test_local.py

What ``LocalBlobStore`` adds to the contract: keys are files under the root, temporary files from an interrupted
write stay invisible, a failed write keeps the old object, and the factories build stores over the data directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from khadbot.storage import LocalBlobStore, atomic_write, default_blob_store, resolve_blobs

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_key_is_the_file_under_root(tmp_path):
    blobs = LocalBlobStore(tmp_path)
    blobs.put("raw/ABC/1/Deaths/none/page_000.json", b"{}")
    path = tmp_path / "raw" / "ABC" / "1" / "Deaths" / "none" / "page_000.json"
    assert path.read_bytes() == b"{}"
    assert blobs.path("raw/ABC/1/Deaths/none/page_000.json") == path


def test_reads_files_written_without_the_store(tmp_path):
    (tmp_path / "events" / "ABC").mkdir(parents=True)
    (tmp_path / "events" / "ABC" / "pulls.json").write_bytes(b"[]")
    blobs = LocalBlobStore(tmp_path)
    assert blobs.get("events/ABC/pulls.json") == b"[]"
    assert blobs.list("events/") == ["events/ABC/pulls.json"]


def test_root_is_created_only_on_first_put(tmp_path):
    root = tmp_path / "data"
    blobs = LocalBlobStore(root)
    assert blobs.get("raw/ABC/report.json") is None
    assert blobs.list() == []
    blobs.delete("raw/ABC/report.json")
    assert not root.exists()
    blobs.put("raw/ABC/report.json", b"{}")
    assert root.is_dir()


def test_list_hides_temporary_files(tmp_path):
    blobs = LocalBlobStore(tmp_path)
    blobs.put("events/ABC/pulls.json", b"[]")
    (tmp_path / "events" / "ABC" / "1.parquet.0123456789ab.tmp").write_bytes(b"partial")
    (tmp_path / "events" / "ABC" / "pulls.json.tmp").write_bytes(b"partial")  # left by the path-based store
    assert blobs.list() == ["events/ABC/pulls.json"]


def test_list_skips_a_file_where_a_directory_is_expected(tmp_path):
    blobs = LocalBlobStore(tmp_path)
    blobs.put("raw/ABC", b"not a directory")
    assert blobs.list("raw/ABC/") == []


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_put_leaves_no_temporary_files(tmp_path):
    blobs = LocalBlobStore(tmp_path)
    for i in range(3):
        blobs.put("annotated/ABC/1__h.json", str(i).encode())
    assert sorted(p.name for p in (tmp_path / "annotated" / "ABC").iterdir()) == ["1__h.json"]


def test_failed_replace_keeps_the_old_object_and_cleans_up(tmp_path, monkeypatch):
    blobs = LocalBlobStore(tmp_path)
    blobs.put("events/ABC/pulls.json", b"old")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        blobs.put("events/ABC/pulls.json", b"new")
    assert blobs.get("events/ABC/pulls.json") == b"old"
    assert sorted(p.name for p in (tmp_path / "events" / "ABC").iterdir()) == ["pulls.json"]


def test_atomic_write_creates_parents(tmp_path):
    path = tmp_path / "a" / "b" / "c.bin"
    atomic_write(path, b"x")
    assert path.read_bytes() == b"x"


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def test_default_blob_store_is_the_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("KHADBOT_DATA_DIR", str(tmp_path / "data"))
    blobs = default_blob_store()
    assert isinstance(blobs, LocalBlobStore)
    assert blobs.root == tmp_path / "data"


def test_resolve_blobs_keeps_a_store_and_its_default_prefix(tmp_path):
    store = LocalBlobStore(tmp_path)
    assert resolve_blobs(store, None, "raw") == (store, "raw")
    assert resolve_blobs(store, "", "raw") == (store, "")
    assert resolve_blobs(store, "fixtures/raw", "raw") == (store, "fixtures/raw")


@pytest.mark.parametrize("target", ["str", "path"])
def test_resolve_blobs_treats_a_path_as_the_stage_directory(tmp_path, target):
    root = str(tmp_path) if target == "str" else Path(tmp_path)
    blobs, prefix = resolve_blobs(root, None, "raw")
    assert isinstance(blobs, LocalBlobStore)
    assert blobs.root == tmp_path
    assert prefix == ""
