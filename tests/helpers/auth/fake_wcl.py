"""
tests/helpers/auth/fake_wcl.py

A fake Warcraft Logs for the sign-in tests (respx): it issues obviously fake tokens, answers the current-user and
report-readability queries, and records what it was asked. Also a clock the tests move by hand.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import respx

from khadbot.wcl.client import GRAPHQL_CLIENT_URL, GRAPHQL_USER_URL, TOKEN_URL

FAKE_CLIENT_ID = "fake-client-id"
FAKE_CLIENT_SECRET = "fake-client-secret-do-not-use"


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class FakeWcl:
    """
    The WCL endpoints sign-in talks to. Each issued token is ``fake-access-<n>`` / ``fake-refresh-<n>``; a token in
    ``revoked`` is answered with 401; ``readable`` maps a token (or ``"app"``) to the report codes it can read.
    """

    router: respx.MockRouter
    expires_in: int = 3600
    users: dict[str, tuple[int, str]] = field(default_factory=dict)  # access token -> (wcl id, name)
    readable: dict[str, set[str]] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)
    issued: int = 0
    token_requests: list[dict[str, list[str]]] = field(default_factory=list)
    report_checks: list[str] = field(default_factory=list)  # "<token or app>:<code>"
    exchange_status: int = 200
    refresh_status: int = 200
    refresh_delay_s: float = 0.0
    wcl_user: tuple[int, str] = (777, "Khadgar")

    def install(self) -> None:
        self.router.post(TOKEN_URL).mock(side_effect=self._token)
        self.router.post(GRAPHQL_USER_URL).mock(side_effect=self._user_graphql)
        self.router.post(GRAPHQL_CLIENT_URL).mock(side_effect=self._app_graphql)

    async def _token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.token_requests.append(form)
        grant = form.get("grant_type", [""])[0]
        if grant == "client_credentials":
            return httpx.Response(200, json={"access_token": "fake-app-token", "expires_in": 3600})
        if grant == "authorization_code" and self.exchange_status != 200:
            return httpx.Response(self.exchange_status, json={"error": "invalid_grant"})
        if grant == "refresh_token":
            if self.refresh_delay_s:
                await asyncio.sleep(self.refresh_delay_s)
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
        self.issued += 1
        access = f"fake-access-{self.issued}"
        self.users[access] = self.wcl_user
        if grant == "refresh_token":
            old = form["refresh_token"][0].replace("fake-refresh-", "fake-access-")
            self.readable.setdefault(access, set()).update(self.readable.get(old, set()))
        return httpx.Response(
            200,
            json={
                "access_token": access,
                "refresh_token": f"fake-refresh-{self.issued}",
                "expires_in": self.expires_in,
            },
        )

    def _user_graphql(self, request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        if token in self.revoked or token not in self.users:
            return httpx.Response(401)
        body = json.loads(request.content)
        if "currentUser" in body["query"]:
            wid, name = self.users[token]
            return httpx.Response(200, json={"data": {"userData": {"currentUser": {"id": wid, "name": name}}}})
        return self._report(token, body)

    def _app_graphql(self, request: httpx.Request) -> httpx.Response:
        return self._report("app", json.loads(request.content))

    def _report(self, who: str, body: dict) -> httpx.Response:
        code = body["variables"]["code"]
        self.report_checks.append(f"{who}:{code}")
        if code in self.readable.get(who, set()):
            return httpx.Response(200, json={"data": {"reportData": {"report": {"startTime": 1}}}})
        return httpx.Response(200, json={"errors": [{"message": "You do not have permission to view this report."}]})
