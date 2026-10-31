"""
tests/unit/auth/test_dev_sessions.py

Test sessions (``auth.dev_sessions``) over the in-memory store: the gate, the reserved WCL user IDs, a session that
answers ``/v1/me``, reports readable as marked and 404 for everyone else, and that the cookie value never reaches a
log or a repr. The ``test-session`` command is covered in ``tests/unit/test_main_test_session.py``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from khadbot.auth import routes
from khadbot.auth.dev_sessions import (
    TEST_WCL_USER_ID_MAX,
    TEST_WCL_USER_ID_MIN,
    SessionsDisabledError,
    is_test_user_id,
    issue_session,
    reserved_wcl_user_id,
    storage_state,
)
from khadbot.auth.routes import SESSION_COOKIE, OptionalUser, get_auth
from khadbot.auth.service import AuthService
from khadbot.config import DevSessionsConfig
from khadbot.errors import ConfigurationError

ON = DevSessionsConfig(enabled=True)
OFF = DevSessionsConfig(enabled=False)
CODE = "AbC123xyz"


@pytest.fixture
def app(service) -> FastAPI:
    app = FastAPI()
    routes.mount(app, service)

    @app.get("/v1/reports/{code}/probe")
    async def probe(code: str, user: OptionalUser, auth: Annotated[AuthService, Depends(get_auth)]) -> dict:
        await auth.access.require_readable(user.id if user else None, code)
        return {"code": code}

    return app


def browser(app: FastAPI, cookie: str | None = None) -> TestClient:
    client = TestClient(app, base_url="http://localhost:8000")
    if cookie:
        client.cookies.set(SESSION_COOKIE, cookie)
    return client


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestGate:
    async def test_refused_when_off_and_nothing_is_written(self, store):
        with pytest.raises(SessionsDisabledError, match="KHADBOT_TEST_SESSIONS"):
            await issue_session(store, OFF, "alice", reports=[CODE])
        assert (store.users, store.sessions, store.access, store.public_access) == ({}, {}, {}, {})

    def test_the_refusal_is_a_configuration_error(self):
        assert issubclass(SessionsDisabledError, ConfigurationError)

    def test_off_unless_the_variable_says_otherwise(self, monkeypatch):
        monkeypatch.delenv("KHADBOT_TEST_SESSIONS", raising=False)
        assert DevSessionsConfig().enabled is False

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "2", "enabled"])
    def test_anything_but_an_explicit_yes_is_off(self, monkeypatch, value):
        monkeypatch.setenv("KHADBOT_TEST_SESSIONS", value)
        assert DevSessionsConfig().enabled is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
    def test_explicit_yes_turns_it_on(self, monkeypatch, value):
        monkeypatch.setenv("KHADBOT_TEST_SESSIONS", value)
        assert DevSessionsConfig().enabled is True


# ---------------------------------------------------------------------------
# Reserved IDs
# ---------------------------------------------------------------------------


class TestReservedIds:
    def test_stable_per_name_distinct_across_names_and_inside_the_range(self):
        ids = {name: reserved_wcl_user_id(name) for name in ("alice", "bob", "carol", "smoke", "dev")}
        assert len(set(ids.values())) == len(ids)
        assert all(TEST_WCL_USER_ID_MIN <= i <= TEST_WCL_USER_ID_MAX < 0 for i in ids.values())
        assert reserved_wcl_user_id("alice") == ids["alice"]

    def test_the_range_has_no_positive_id(self):
        assert TEST_WCL_USER_ID_MAX < 0
        assert not is_test_user_id(0) and not is_test_user_id(1) and not is_test_user_id(2**31)
        assert is_test_user_id(-1) and is_test_user_id(TEST_WCL_USER_ID_MIN)
        assert not is_test_user_id(TEST_WCL_USER_ID_MIN - 1)

    @pytest.mark.parametrize("name", ["", "Alice", "a b", "-x", "x" * 33, "a/b", "a_b"])
    def test_bad_names(self, name):
        with pytest.raises(ValueError, match="name"):
            reserved_wcl_user_id(name)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class TestSessions:
    async def test_the_session_answers_me_as_a_marked_test_user(self, app, store):
        issued = await issue_session(store, ON, "alice")
        me = browser(app, issued.cookie).get("/v1/me")
        assert me.status_code == 200
        body = me.json()
        assert body["id"] == issued.user.id and body["name"] == "Test alice"
        assert is_test_user_id(body["wcl_user_id"])
        assert body["wcl_user_id"] == reserved_wcl_user_id("alice")

    async def test_the_same_name_reuses_the_user_and_starts_a_new_session(self, store):
        first = await issue_session(store, ON, "alice")
        second = await issue_session(store, ON, "alice")
        assert first.user.id == second.user.id and first.cookie != second.cookie
        assert len(store.users) == 1 and len(store.sessions) == 2

    async def test_several_named_users_are_different_users(self, app, store):
        alice, bob = await issue_session(store, ON, "alice"), await issue_session(store, ON, "bob")
        assert alice.user.id != bob.user.id
        assert browser(app, alice.cookie).get("/v1/me").json()["id"] == alice.user.id
        assert browser(app, bob.cookie).get("/v1/me").json()["id"] == bob.user.id

    async def test_holds_no_wcl_tokens(self, store):
        issued = await issue_session(store, ON, "alice", reports=[CODE])
        assert store.tokens == {} and store.get_tokens(issued.user.id) is None

    async def test_only_a_digest_of_the_cookie_is_stored(self, store):
        issued = await issue_session(store, ON, "alice")
        assert issued.cookie not in repr(store.sessions) and issued.cookie not in repr(store.users)

    async def test_expires_after_the_ttl(self, app, store, clock):
        issued = await issue_session(store, ON, "alice", ttl=timedelta(hours=1), clock=clock)
        client = browser(app, issued.cookie)
        assert client.get("/v1/me").status_code == 200
        clock.advance(hours=1, minutes=1)
        assert client.get("/v1/me").status_code == 401

    async def test_no_cookie_no_session(self, app, store):
        await issue_session(store, ON, "alice")
        assert browser(app).get("/v1/me").status_code == 401

    async def test_the_cookie_value_is_in_no_log_and_no_repr(self, store, caplog):
        with caplog.at_level(logging.DEBUG):
            issued = await issue_session(store, ON, "alice", reports=[CODE])
        assert issued.cookie not in caplog.text and issued.cookie not in repr(issued)
        assert any(issued.user.id in r.getMessage() for r in caplog.records)  # the user ID is what gets logged


# ---------------------------------------------------------------------------
# Report access
# ---------------------------------------------------------------------------


class TestReports:
    async def test_marked_reports_are_readable_and_others_are_404(self, app, store, wcl):
        alice = await issue_session(store, ON, "alice", reports=[CODE])
        bob = await issue_session(store, ON, "bob")
        assert browser(app, alice.cookie).get(f"/v1/reports/{CODE}/probe").status_code == 200
        for client in (browser(app, bob.cookie), browser(app)):
            resp = client.get(f"/v1/reports/{CODE}/probe")
            assert (resp.status_code, resp.json()["code"]) == (404, "report_not_found")
        assert alice.readable == (CODE,)

    async def test_an_unmarked_report_is_404_for_the_user_who_has_other_marks(self, app, store, wcl):
        alice = await issue_session(store, ON, "alice", reports=[CODE])
        resp = browser(app, alice.cookie).get("/v1/reports/Other12345/probe")
        assert resp.status_code == 404

    async def test_marking_needs_no_warcraft_logs_call(self, app, store, wcl):
        alice = await issue_session(store, ON, "alice", reports=[CODE])
        bob = await issue_session(store, ON, "bob")
        for client in (browser(app, alice.cookie), browser(app, bob.cookie), browser(app)):
            client.get(f"/v1/reports/{CODE}/probe")
        assert wcl.report_checks == []

    async def test_marks_from_a_later_call_add_to_earlier_ones(self, app, store, wcl):
        await issue_session(store, ON, "alice", reports=[CODE])
        again = await issue_session(store, ON, "alice", reports=["Second12"])
        client = browser(app, again.cookie)
        assert client.get(f"/v1/reports/{CODE}/probe").status_code == 200
        assert client.get("/v1/reports/Second12/probe").status_code == 200

    @pytest.mark.parametrize("code", ["", "has space", "../x", "a" * 41, "x;y"])
    async def test_bad_report_codes_are_refused_before_anything_is_written(self, store, code):
        with pytest.raises(ValueError, match="report code"):
            await issue_session(store, ON, "alice", reports=[code])
        assert store.users == {} and store.sessions == {}


# ---------------------------------------------------------------------------
# Playwright storage state
# ---------------------------------------------------------------------------


async def test_storage_state_is_a_playwright_cookie_document(store, clock):
    issued = await issue_session(store, ON, "alice", clock=clock)
    state = storage_state(
        issued.cookie, domain="localhost", secure=False, expires_at=issued.expires_at, cookie_name=SESSION_COOKIE
    )
    assert state["origins"] == []
    (cookie,) = state["cookies"]
    assert cookie == {
        "name": SESSION_COOKIE,
        "value": issued.cookie,
        "domain": "localhost",
        "path": "/",
        "expires": issued.expires_at.timestamp(),
        "httpOnly": True,
        "secure": False,
        "sameSite": "Lax",
    }
