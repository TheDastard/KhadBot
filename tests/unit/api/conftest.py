"""
tests/unit/api/conftest.py

Every API test runs with outbound network connections blocked. The datasets (``synth_dataset``, ``real_dataset``
and the parametrised ``dataset``) are session fixtures in ``tests/conftest.py``.
"""

from __future__ import annotations

import socket

import pytest

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


@pytest.fixture
def client(dataset):
    return dataset.client
