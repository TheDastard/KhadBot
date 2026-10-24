"""
tests/integration/hosted/test_auth_pg.py

Sign-in and report access on the Postgres auth tables: the full login flow through the API with WCL faked by respx,
replay and expiry rejections, tokens encrypted in their columns, the advisory refresh lock holding across two
workers with their own pools, and cached access rows. Skipped without ``DATABASE_URL``; every value is fake.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
import respx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers.auth.fake_wcl import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, Clock, FakeWcl
from pydantic import SecretStr

from khadbot.auth import routes
from khadbot.auth.pg import PostgresAuthStore
from khadbot.auth.routes import SESSION_COOKIE
from khadbot.auth.service import AuthService
from khadbot.config import AuthConfig
from khadbot.db import migrate, open_pool
from khadbot.wcl.oauth import TokenSet


@pytest.fixture
def migrated(pool):
    migrate(pool)
    return pool


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
def config() -> AuthConfig:
    return replace(
        AuthConfig(),
        redirect_uri="http://localhost:8000/v1/auth/callback",
        post_login_url="http://localhost:3000",
        web_origins=("http://localhost:3000",),
        token_keys=(Fernet.generate_key().decode(),),
    )


def make_service(pool, config, clock) -> AuthService:
    return AuthService(PostgresAuthStore(pool), config, client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET,
                       clock=clock)  # fmt: skip


@pytest.fixture
def service(migrated, config, clock, wcl) -> AuthService:
    return make_service(migrated, config, clock)


@pytest.fixture
def client(service):
    app = FastAPI()
    routes.mount(app, service)
    with TestClient(app, base_url="http://localhost:8000") as c:
        yield c


def _login(client) -> str:
    resp = client.get("/v1/auth/login", follow_redirects=False)
    return parse_qs(urlsplit(resp.headers["location"]).query)["state"][0]


def _one(pool, sql, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()


def test_full_flow_and_stored_shapes(client, migrated):
    state = _login(client)
    resp = client.get("/v1/auth/callback", params={"code": "fake-auth-code", "state": state}, follow_redirects=False)
    assert resp.status_code == 303
    me = client.get("/v1/me").json()
    assert me["wcl_user_id"] == 777

    refresh_enc, access_enc = _one(migrated, "SELECT refresh_enc, access_enc FROM wcl_tokens WHERE user_id = %s",
                                   (me["id"],))  # fmt: skip
    assert "fake-refresh-1" not in refresh_enc and "fake-access-1" not in access_enc
    (id_hash,) = _one(migrated, "SELECT id_hash FROM sessions WHERE user_id = %s", (me["id"],))
    assert id_hash != client.cookies.get(SESSION_COOKIE)
    assert _one(migrated, "SELECT count(*) FROM oauth_states") == (0,)  # consumed

    assert client.post("/v1/auth/logout", headers={"X-KhadBot-CSRF": "1"}).status_code == 204
    assert _one(migrated, "SELECT count(*) FROM sessions") == (0,)


def test_replayed_and_expired_states(client, clock):
    state = _login(client)
    params = {"code": "fake-auth-code", "state": state}
    assert client.get("/v1/auth/callback", params=params, follow_redirects=False).status_code == 303
    assert client.get("/v1/auth/callback", params=params).json()["code"] == "invalid_state"
    late = _login(client)
    clock.advance(seconds=601)
    assert client.get("/v1/auth/callback", params={"code": "c", "state": late}).json()["code"] == "invalid_state"


def test_sign_in_again_keeps_one_user(client, migrated):
    for _ in range(2):
        state = _login(client)
        client.get("/v1/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert _one(migrated, "SELECT count(*) FROM users") == (1,)
    assert _one(migrated, "SELECT count(*) FROM sessions") == (1,)


def test_session_expiry(client, clock):
    state = _login(client)
    client.get("/v1/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    clock.advance(hours=337)
    assert client.get("/v1/me").status_code == 401


async def test_refresh_lock_holds_across_workers(database_url, schema_name, migrated, config, clock, wcl):
    """Two workers, each with its own pool, find the token expired at once: WCL sees one refresh."""
    first = make_service(migrated, config, clock)
    other_pool = open_pool(database_url, min_size=1, max_size=4, kwargs={"options": f"-c search_path={schema_name}"})
    try:
        second = make_service(other_pool, config, clock)
        user = first.store.upsert_user(777, "Khadgar", clock.now)
        await first.tokens.save(user.id, TokenSet(SecretStr("fake-access-0"), SecretStr("fake-refresh-0"), 3600))
        clock.advance(hours=2)
        wcl.refresh_delay_s = 0.2
        a, b = await asyncio.gather(first.tokens.access_token(user.id), second.tokens.access_token(user.id))
    finally:
        other_pool.close()
    assert a == b == "fake-access-1"
    assert [r["grant_type"] for r in wcl.token_requests] == [["refresh_token"]]


async def test_access_rows(service, migrated, wcl, clock):
    user = service.store.upsert_user(777, "Khadgar", clock.now)
    await service.tokens.save(user.id, TokenSet(SecretStr("fake-access-5"), SecretStr("fake-refresh-5"), 3600))
    wcl.users["fake-access-5"] = (777, "Khadgar")
    wcl.readable["fake-access-5"] = {"PRIV1"}
    wcl.readable["app"] = {"PUB1"}
    assert await service.access.can_read(user.id, "PRIV1") is True
    assert await service.access.can_read(user.id, "PUB1") is True
    assert await service.access.can_read(user.id, "NOPE1") is False
    checks = len(wcl.report_checks)
    assert await service.access.can_read(user.id, "PRIV1") is True  # from the table
    assert len(wcl.report_checks) == checks
    rows = {r[0]: r[1:] for r in _all(migrated, "SELECT report_code, readable, via FROM report_access")}
    assert rows == {"PRIV1": (True, "user"), "PUB1": (True, "app"), "NOPE1": (False, None)}
    assert _one(migrated, "SELECT readable FROM report_public_access WHERE report_code = 'PUB1'") == (True,)


def _all(pool, sql):
    with pool.connection() as conn:
        return conn.execute(sql).fetchall()
