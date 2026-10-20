"""
tests/unit/storage/test_blob_contract.py

Runs the ``BlobStore`` contract (``tests/helpers/storage/contract.py``) against every implementation that needs no
network or service. Another implementation joins the suite by adding an entry to ``BLOB_STORES``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from helpers.storage.contract import BlobStoreContract

from khadbot.storage import BlobStore, LocalBlobStore

# name -> factory taking a fresh tmp_path and returning an empty store
BLOB_STORES: dict[str, Callable[[Path], BlobStore]] = {
    "local": lambda tmp_path: LocalBlobStore(tmp_path / "store"),
}


@pytest.fixture(params=sorted(BLOB_STORES))
def blobs(request, tmp_path) -> BlobStore:
    return BLOB_STORES[request.param](tmp_path)


class TestBlobStoreContract(BlobStoreContract):
    pass
