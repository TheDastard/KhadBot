"""
tests/unit/auth/conftest.py

Shared pieces for the sign-in tests: a movable clock, a fake WCL (respx) that hands out obviously fake tokens and
answers the current-user and report-readability queries, and an ``AuthService`` over the in-memory store. Every test
here runs with connections off this machine blocked.
"""

from __future__ import annotations

import socket
from dataclasses import replace

import pytest
import respx
from cryptography.fernet import Fernet
from helpers.auth.fake_wcl import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, Clock, FakeWcl

from khadbot.auth.service import AuthService
from khadbot.auth.store import MemoryAuthStore
from khadbot.config import AuthConfig

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise AssertionError(f"unit test tried to connect to {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def wcl():
    with respx.mock(assert_all_called=False) as router:
        fake = FakeWcl(router)
        fake.install()
        yield fake


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def auth_config(fernet_key) -> AuthConfig:
    return replace(
        AuthConfig(),
        redirect_uri="http://localhost:8000/v1/auth/callback",
        post_login_url="http://localhost:3000",
        web_origins=("http://localhost:3000",),
        token_keys=(fernet_key,),
        wcl_scope="",
    )


@pytest.fixture
def store() -> MemoryAuthStore:
    return MemoryAuthStore()


@pytest.fixture
def service(store, auth_config, clock, wcl) -> AuthService:
    return AuthService(store, auth_config, client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET, clock=clock)
