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


# ---------------------------------------------------------------------------
# The hosted API: sign-in over the in-memory store, a faked WCL, the queue on procrastinate's in-memory connector
# ---------------------------------------------------------------------------

WEB_ORIGIN = "http://localhost:3000"


@pytest.fixture
def clock():
    from helpers.auth.fake_wcl import Clock

    return Clock()


@pytest.fixture
def wcl():
    import respx
    from helpers.auth.fake_wcl import FakeWcl

    with respx.mock(assert_all_called=False) as router:
        fake = FakeWcl(router)
        fake.install()
        yield fake


@pytest.fixture
def auth_store():
    from khadbot.auth.store import MemoryAuthStore

    return MemoryAuthStore()


@pytest.fixture
def service(auth_store, clock, wcl):
    from dataclasses import replace

    from cryptography.fernet import Fernet
    from helpers.auth.fake_wcl import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

    from khadbot.auth.service import AuthService
    from khadbot.config import AuthConfig

    config = replace(
        AuthConfig(),
        redirect_uri="http://localhost:8000/v1/auth/callback",
        post_login_url=WEB_ORIGIN,
        web_origins=(WEB_ORIGIN,),
        token_keys=(Fernet.generate_key().decode(),),
        wcl_scope="",
    )
    return AuthService(auth_store, config, client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET, clock=clock)
