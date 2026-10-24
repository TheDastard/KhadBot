"""
tests/unit/auth/test_auth_routes.py

The sign-in routes end to end through FastAPI's TestClient, with WCL faked by respx: the login flow, every
rejection path (state mismatch, replay, another browser, expiry, WCL denial), sessions and their expiry, logout's
CSRF check, cookie attributes, and that no token, code or cookie value reaches a log or a response body.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import replace
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from helpers.auth.fake_wcl import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

from khadbot.auth import routes
from khadbot.auth.routes import CSRF_HEADER, LOGIN_COOKIE, SESSION_COOKIE, CurrentUser, OptionalUser, get_auth
from khadbot.auth.service import AuthService
from khadbot.auth.store import User
from khadbot.wcl.oauth import AUTHORIZE_URL

CSRF = {CSRF_HEADER: "1"}


def make_app(service: AuthService) -> FastAPI:
    app = FastAPI()
    routes.mount(app, service)

    @app.get("/v1/reports/{code}/probe")
    async def probe(code: str, user: OptionalUser, auth: Annotated[AuthService, Depends(get_auth)]) -> dict:
        await auth.access.require_readable(user.id if user else None, code)
        return {"code": code}

    @app.get("/v1/whoami")
    async def whoami(user: CurrentUser) -> dict:
        return {"id": user.id}

    return app


@pytest.fixture
def client(service):
    with TestClient(make_app(service), base_url="http://localhost:8000") as c:
        yield c


def start_login(client, next_path=None):
    params = {"next": next_path} if next_path else {}
    resp = client.get("/v1/auth/login", params=params, follow_redirects=False)
    assert resp.status_code == 303
    query = parse_qs(urlsplit(resp.headers["location"]).query)
    return resp, {k: v[0] for k, v in query.items()}


def sign_in(client, code="fake-auth-code"):
    _, query = start_login(client)
    return client.get("/v1/auth/callback", params={"code": code, "state": query["state"]}, follow_redirects=False)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


class TestLoginFlow:
    def test_login_redirects_to_wcl_with_pkce(self, client, store):
        resp, query = start_login(client)
        assert resp.headers["location"].startswith(AUTHORIZE_URL + "?")
        assert query["client_id"] == FAKE_CLIENT_ID
        assert query["redirect_uri"] == "http://localhost:8000/v1/auth/callback"
        assert query["code_challenge_method"] == "S256" and query["response_type"] == "code"
        assert "scope" not in query
        assert LOGIN_COOKIE in resp.cookies
        assert resp.headers["cache-control"] == "no-store"
        # stored server-side as digests and ciphertext only
        (state,) = store.login_states.values()
        assert state.state_hash == hashlib.sha256(query["state"].encode()).hexdigest()
        assert query["state"] not in repr(store.login_states)

    def test_full_flow(self, client, wcl, store):
        _, query = start_login(client, "/reports/ABC")
        resp = client.get("/v1/auth/callback", params={"code": "fake-auth-code", "state": query["state"]},
                          follow_redirects=False)  # fmt: skip
        assert resp.status_code == 303
        assert resp.headers["location"] == "http://localhost:3000/reports/ABC"
        assert SESSION_COOKIE in resp.cookies
        # the exchange carried the verifier matching the challenge, and the client secret
        exchange = next(r for r in wcl.token_requests if r["grant_type"] == ["authorization_code"])
        verifier = exchange["code_verifier"][0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == query["code_challenge"]
        assert exchange["client_secret"] == [FAKE_CLIENT_SECRET]
        assert exchange["code"] == ["fake-auth-code"]

        me = client.get("/v1/me")
        assert me.status_code == 200
        body = me.json()
        assert body["name"] == "Khadgar" and body["wcl_user_id"] == 777
        assert set(store.users) == {body["id"]}
        assert body["id"] in store.tokens

    def test_second_sign_in_reuses_the_user_and_replaces_the_session(self, client, wcl, store):
        sign_in(client)
        first = client.cookies.get(SESSION_COOKIE)
        user_id = client.get("/v1/me").json()["id"]
        wcl.wcl_user = (777, "Khadgar the Renamed")
        sign_in(client)
        assert client.cookies.get(SESSION_COOKIE) != first
        assert len(store.sessions) == 1  # the earlier session was revoked
        me = client.get("/v1/me").json()
        assert me["id"] == user_id and me["name"] == "Khadgar the Renamed"

    def test_unsafe_next_is_dropped(self, client):
        _, query = start_login(client, "//evil.example/x")
        resp = client.get("/v1/auth/callback", params={"code": "c", "state": query["state"]}, follow_redirects=False)
        assert resp.headers["location"] == "http://localhost:3000"


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


class TestCallbackRejections:
    def test_unknown_state(self, client):
        start_login(client)
        resp = client.get("/v1/auth/callback", params={"code": "c", "state": "not-the-state"})
        assert resp.status_code == 400 and resp.json()["code"] == "invalid_state"
        assert SESSION_COOKIE not in resp.cookies

    def test_missing_state(self, client):
        start_login(client)
        resp = client.get("/v1/auth/callback", params={"code": "c"})
        assert resp.status_code == 400 and resp.json()["code"] == "invalid_state"

    def test_replayed_callback(self, client):
        _, query = start_login(client)
        params = {"code": "fake-auth-code", "state": query["state"]}
        assert client.get("/v1/auth/callback", params=params, follow_redirects=False).status_code == 303
        client.cookies.set(LOGIN_COOKIE, "whatever", path="/v1/auth")
        replay = client.get("/v1/auth/callback", params=params, follow_redirects=False)
        assert replay.status_code == 400 and replay.json()["code"] == "invalid_state"

    def test_callback_in_another_browser(self, service, client):
        """An attacker's own callback link, opened by a victim whose browser didn't start that sign-in."""
        _, query = start_login(client)
        with TestClient(make_app(service), base_url="http://localhost:8000") as victim:
            resp = victim.get("/v1/auth/callback", params={"code": "attacker-code", "state": query["state"]})
        assert resp.status_code == 400 and resp.json()["code"] == "invalid_state"
        # and the state is spent: the attacker can't use it afterwards either
        resp = client.get("/v1/auth/callback", params={"code": "c", "state": query["state"]})
        assert resp.json()["code"] == "invalid_state"

    def test_wrong_browser_nonce(self, client):
        _, query = start_login(client)
        client.cookies.set(LOGIN_COOKIE, "forged-nonce", path="/v1/auth")
        resp = client.get("/v1/auth/callback", params={"code": "c", "state": query["state"]})
        assert resp.json()["code"] == "invalid_state"

    def test_expired_state(self, client, clock):
        _, query = start_login(client)
        clock.advance(seconds=601)
        resp = client.get("/v1/auth/callback", params={"code": "c", "state": query["state"]})
        assert resp.status_code == 400 and resp.json()["code"] == "invalid_state"

    def test_wcl_denied(self, client):
        _, query = start_login(client)
        resp = client.get("/v1/auth/callback", params={"error": "access_denied", "state": query["state"]})
        assert resp.status_code == 400 and resp.json()["code"] == "wcl_denied"

    def test_exchange_refused(self, client, wcl, store):
        wcl.exchange_status = 400
        resp = sign_in(client)
        assert resp.status_code == 400 and resp.json()["code"] == "sign_in_failed"
        assert not store.users and not store.sessions and not store.tokens

    def test_identity_lookup_fails(self, client, wcl, store):
        wcl.revoked.add("fake-access-1")
        resp = sign_in(client)
        assert resp.status_code == 400 and resp.json()["code"] == "sign_in_failed"
        assert not store.sessions


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class TestSessions:
    def test_me_signed_out(self, client):
        resp = client.get("/v1/me")
        assert resp.status_code == 401 and resp.json()["code"] == "not_signed_in"

    def test_forged_cookie(self, client):
        client.cookies.set(SESSION_COOKIE, "made-up-session")
        assert client.get("/v1/me").status_code == 401

    def test_cookie_value_is_not_stored(self, client, store):
        sign_in(client)
        session_id = client.cookies.get(SESSION_COOKIE)
        (record,) = store.sessions.values()
        assert record.id_hash == hashlib.sha256(session_id.encode()).hexdigest()
        assert session_id not in repr(store.sessions)

    def test_idle_session_expires(self, client, clock):
        sign_in(client)
        clock.advance(hours=337)
        assert client.get("/v1/me").status_code == 401

    def test_sliding_expiry(self, client, clock):
        sign_in(client)
        for _ in range(3):  # 600 h in all, past the 336 h idle limit, but never 336 h idle
            clock.advance(hours=200)
            assert client.get("/v1/me").status_code == 200
        clock.advance(hours=337)
        assert client.get("/v1/me").status_code == 401

    def test_absolute_limit(self, client, clock):
        sign_in(client)
        for _ in range(3):
            clock.advance(days=9)
            assert client.get("/v1/me").status_code == 200
        clock.advance(days=4)  # 31 days after sign-in
        assert client.get("/v1/me").status_code == 401

    def test_cookie_attributes_on_localhost(self, client):
        resp = sign_in(client)
        cookie = next(h for h in resp.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE + "="))
        lowered = cookie.lower()
        assert "httponly" in lowered and "samesite=lax" in lowered and "path=/" in lowered
        assert "secure" not in lowered  # plain-HTTP localhost only

    def test_cookies_are_secure_off_localhost(self, store, auth_config, clock, wcl):
        cfg = replace(auth_config, redirect_uri="https://api.example.test/v1/auth/callback")
        svc = AuthService(store, cfg, client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET, clock=clock)
        with TestClient(make_app(svc), base_url="https://api.example.test") as c:
            resp = c.get("/v1/auth/login", follow_redirects=False)
            assert "secure" in resp.headers["set-cookie"].lower()
            _, query = resp, parse_qs(urlsplit(resp.headers["location"]).query)
            done = c.get("/v1/auth/callback", params={"code": "c", "state": query["state"][0]},
                         follow_redirects=False)  # fmt: skip
        session_cookie = next(h for h in done.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE))
        assert "secure" in session_cookie.lower()


class TestLogout:
    def test_logout_needs_the_csrf_header(self, client):
        sign_in(client)
        resp = client.post("/v1/auth/logout")
        assert resp.status_code == 403 and resp.json()["code"] == "csrf_failed"
        assert client.get("/v1/me").status_code == 200

    def test_logout_refuses_a_foreign_origin(self, client):
        sign_in(client)
        resp = client.post("/v1/auth/logout", headers={**CSRF, "Origin": "https://evil.example"})
        assert resp.status_code == 403
        assert client.get("/v1/me").status_code == 200

    @pytest.mark.parametrize("origin", ["http://localhost:3000", "http://localhost:8000", None])
    def test_logout(self, client, store, origin):
        sign_in(client)
        headers = {**CSRF, **({"Origin": origin} if origin else {})}
        session_id = client.cookies.get(SESSION_COOKIE)
        resp = client.post("/v1/auth/logout", headers=headers)
        assert resp.status_code == 204
        assert not store.sessions
        client.cookies.set(SESSION_COOKIE, session_id)  # an old copy of the cookie no longer works
        assert client.get("/v1/me").status_code == 401

    def test_logout_signed_out_is_fine(self, client):
        assert client.post("/v1/auth/logout", headers=CSRF).status_code == 204


# ---------------------------------------------------------------------------
# Report access through a route
# ---------------------------------------------------------------------------


class TestReportRoute:
    def test_private_report_is_404_for_others_and_readable_by_its_owner(self, client, wcl):
        sign_in(client)
        wcl.readable["fake-access-1"] = {"PRIV1"}
        assert client.get("/v1/reports/PRIV1/probe").status_code == 200
        with TestClient(client.app, base_url="http://localhost:8000") as stranger:
            resp = stranger.get("/v1/reports/PRIV1/probe")
        assert resp.status_code == 404 and resp.json()["code"] == "report_not_found"

    def test_missing_and_private_reports_answer_alike(self, client, wcl):
        sign_in(client)
        private = client.get("/v1/reports/PRIV2/probe")
        missing = client.get("/v1/reports/NOPE0/probe")
        assert private.status_code == missing.status_code == 404
        assert private.json()["message"] == missing.json()["message"]

    def test_public_report_for_a_visitor(self, client, wcl):
        wcl.readable["app"] = {"PUB1"}
        assert client.get("/v1/reports/PUB1/probe").status_code == 200

    def test_wcl_down_is_503_not_404(self, client, wcl):
        import httpx

        from khadbot.wcl.client import GRAPHQL_CLIENT_URL

        wcl.router.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(502))
        resp = client.get("/v1/reports/ANY1/probe")
        assert resp.status_code == 503 and resp.json()["code"] == "wcl_unavailable"

    def test_current_user_dependency(self, client):
        assert client.get("/v1/whoami").status_code == 401
        sign_in(client)
        assert client.get("/v1/whoami").status_code == 200


# ---------------------------------------------------------------------------
# Secrets stay secret
# ---------------------------------------------------------------------------


def test_no_secret_reaches_logs_or_bodies(client, wcl, caplog, clock):
    caplog.set_level(logging.DEBUG)
    bodies: list[str] = []
    headers: list[str] = []

    def record(resp):
        bodies.append(resp.text)
        headers.extend(f"{k}: {v}" for k, v in resp.headers.items() if k.lower() != "set-cookie")
        return resp

    _, query = start_login(client)
    record(client.get("/v1/auth/callback", params={"code": "fake-auth-code-xyz", "state": query["state"]},
                      follow_redirects=False))  # fmt: skip
    session_id = client.cookies.get(SESSION_COOKIE)
    record(client.get("/v1/me"))
    wcl.readable["fake-access-1"] = {"PRIV1"}
    record(client.get("/v1/reports/PRIV1/probe"))
    clock.advance(hours=2)  # the access token expires and is refreshed during the next check
    wcl.readable["fake-access-1"].add("PRIV3")
    record(client.get("/v1/reports/PRIV3/probe"))
    wcl.exchange_status = 400
    record(sign_in(client, code="fake-auth-code-refused"))
    record(client.post("/v1/auth/logout", headers=CSRF))

    secrets = [
        "fake-auth-code-xyz",
        "fake-auth-code-refused",
        "fake-access-1",
        "fake-refresh-1",
        "fake-access-2",
        "fake-refresh-2",
        "fake-app-token",
        FAKE_CLIENT_SECRET,
        query["state"],
        session_id,
    ]
    logged = "\n".join(r.getMessage() + repr(r.args) for r in caplog.records)
    for secret in secrets:
        assert secret not in logged, secret
        for text in bodies + headers:
            assert secret not in text, secret
    assert any(r.name.startswith("khadbot.auth") for r in caplog.records)  # the flow did log, just not secrets


def test_user_record_has_no_secrets():
    assert set(User.__dataclass_fields__) == {"id", "wcl_user_id", "name"}


def test_access_log_redacts_the_callback_query():
    """uvicorn's access log passes the request path as an argument: the code and state must not reach it."""
    routes.install_log_redaction()
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/v1/auth/callback?code=fake-auth-code&state=fake-state", "1.1", 303), None,
    )  # fmt: skip
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(record)
    message = record.getMessage()
    assert "fake-auth-code" not in message and "fake-state" not in message
    assert "/v1/auth/callback?<redacted>" in message
