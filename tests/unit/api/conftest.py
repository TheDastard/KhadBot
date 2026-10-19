"""
tests/unit/api/conftest.py

Session-scoped API datasets (``tests/helpers/api/datasets.py``): ``synth`` always, ``real`` when the recorded
fixtures' Git LFS pages are pulled. Every test here runs with outbound network connections blocked.
"""

from __future__ import annotations

import socket

import pytest
from helpers.api.datasets import Dataset, build_real, build_synth
from helpers.encounter.real_fixtures import REAL_REPORTS

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any connection off this machine (loopback stays open: asyncio's socketpair uses it on Windows)."""
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise AssertionError(f"unit test tried to connect to {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(scope="session")
def synth_dataset(tmp_path_factory) -> Dataset:
    return build_synth(tmp_path_factory.mktemp("api_synth"))


@pytest.fixture(scope="session")
def real_dataset(tmp_path_factory) -> Dataset:
    if not REAL_REPORTS:
        pytest.skip("real WCL fixtures not pulled from Git LFS")
    return build_real(tmp_path_factory.mktemp("api_real"))


@pytest.fixture(params=["synth", "real"])
def dataset(request) -> Dataset:
    """Each test that takes ``dataset`` runs once per dataset."""
    return request.getfixturevalue(f"{request.param}_dataset")


@pytest.fixture
def client(dataset):
    return dataset.client
