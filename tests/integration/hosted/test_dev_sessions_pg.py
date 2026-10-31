"""
tests/integration/hosted/test_dev_sessions_pg.py

Test sessions on the Postgres auth tables: a negative WCL user ID fits the ``users`` column and stays unique per
name, the session answers ``/v1/me`` through the API, a marked report is readable for its user and 404 for another
test user without a WCL call, and only a digest of the cookie is stored. Skipped without ``DATABASE_URL``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated

import pytest
import respx
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from helpers.auth.fake_wcl import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, Clock, FakeWcl

from khadbot.auth import routes
from khadbot.auth.dev_sessions import is_test_user_id, issue_session, reserved_wcl_user_id
from khadbot.auth.pg import PostgresAuthStore
from khadbot.auth.routes import SESSION_COOKIE, OptionalUser, get_auth
from khadbot.auth.service import AuthService
from khadbot.config import AuthConfig, DevSessionsConfig
from khadbot.db import migrate

ON = DevSessionsConfig(enabled=True)


@pytest.fixture
def wcl():
    with respx.mock(assert_all_called=False) as router:
        fake = FakeWcl(router)
        fake.install()
        yield fake


@pytest.fixture
def store(pool) -> PostgresAuthStore:
    migrate(pool)
    return PostgresAuthStore(pool)


@pytest.fixture
def app(store, wcl) -> FastAPI:
    config = replace(
        AuthConfig(),
        redirect_uri="http://localhost:8000/v1/auth/callback",
        post_login_url="http://localhost:3000",
        web_origins=("http://localhost:3000",),
        token_keys=(Fernet.generate_key().decode(),),
    )
    service = AuthService(store, config, client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET, clock=Clock())
    app = FastAPI()
    routes.mount(app, service)

    @app.get("/v1/reports/{code}/probe")
    async def probe(code: str, user: OptionalUser, auth: Annotated[AuthService, Depends(get_auth)]) -> dict:
        await auth.access.require_readable(user.id if user else None, code)
        return {"code": code}

    return app


def browser(app: FastAPI, cookie: str) -> TestClient:
    client = TestClient(app, base_url="http://localhost:8000")
    client.cookies.set(SESSION_COOKIE, cookie)
    return client


async def test_session_answers_me_with_a_negative_id(app, store, pool):
    issued = await issue_session(store, ON, "alice")
    body = browser(app, issued.cookie).get("/v1/me").json()
    assert body["id"] == issued.user.id and body["wcl_user_id"] == reserved_wcl_user_id("alice")
    assert is_test_user_id(body["wcl_user_id"])
    with pool.connection() as conn:
        (stored,) = conn.execute("SELECT wcl_user_id FROM users WHERE id = %s", (issued.user.id,)).fetchone()
        (id_hash,) = conn.execute("SELECT id_hash FROM sessions WHERE user_id = %s", (issued.user.id,)).fetchone()
    assert stored == body["wcl_user_id"] < 0 and id_hash != issued.cookie


async def test_same_name_reuses_the_user_and_two_names_are_two_users(store, pool):
    first = await issue_session(store, ON, "alice")
    again = await issue_session(store, ON, "alice")
    bob = await issue_session(store, ON, "bob")
    assert first.user.id == again.user.id != bob.user.id
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone() == (2,)
        assert conn.execute("SELECT count(*) FROM sessions").fetchone() == (3,)


async def test_marked_report_is_readable_for_its_user_only(app, store, wcl):
    alice = await issue_session(store, ON, "alice", reports=["AbC123"])
    bob = await issue_session(store, ON, "bob")
    assert browser(app, alice.cookie).get("/v1/reports/AbC123/probe").status_code == 200
    assert browser(app, bob.cookie).get("/v1/reports/AbC123/probe").status_code == 404
    assert wcl.report_checks == []
