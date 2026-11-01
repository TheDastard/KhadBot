"""
tests/helpers/api/search.py

The hosted app for guild, character and server search tests, with WCL answered by replay. ``build_root`` writes a
replay directory from synthetic data: search answers written to WCL's documented schema, and four nights derived from
``SYNTH01``'s metadata (start times and fight rosters changed). ``Env`` builds the app over it with an in-memory
sign-in store and budget, and counts the GraphQL operations replay is asked. Nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from helpers.jobs.budget import FakeReader, MovableClock, budget_config
from helpers.wcl.replay import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, WCL_FIXTURES, replay_client
from khadbot.api import create_app
from khadbot.auth.routes import SESSION_COOKIE
from khadbot.auth.service import AuthService
from khadbot.auth.store import MemoryAuthStore
from khadbot.config import AuthConfig, SearchConfig
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend
from khadbot.wcl.oauth import TokenSet
from khadbot.wcl.replay import (
    ReplayTransport,
    search_character_path,
    search_guild_path,
    search_guild_reports_path,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
WEB_ORIGIN = "http://localhost:3000"
GUILD_ID = 777
ENCOUNTER = 90001  # SYNTH01's encounter


# ---------------------------------------------------------------------------
# A synthetic replay directory
# ---------------------------------------------------------------------------


def _ms(when: datetime) -> float:
    return when.timestamp() * 1000


def _night(root, code: str, start: datetime, rosters: dict[int, list[int]]) -> None:
    """A report like SYNTH01 that started at ``start``, with each fight's friendly players set from ``rosters``."""
    meta = copy.deepcopy(json.loads((WCL_FIXTURES / "SYNTH01" / "report.json").read_text(encoding="utf-8")))
    meta["startTime"] = _ms(start)
    meta["title"] = f"Synthetic {code}"
    for fight in meta["fights"]:
        fight["friendlyPlayers"] = rosters.get(fight["id"], fight["friendlyPlayers"])
    (root / code).mkdir(parents=True)
    (root / code / "report.json").write_text(json.dumps(meta), encoding="utf-8")


def _search(root, relative, body) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _listed(code: str, start: datetime) -> dict:
    return {"code": code, "title": code, "startTime": _ms(start), "endTime": _ms(start) + 1, "visibility": "public"}


GUILD = {
    "data": {
        "guildData": {
            "guild": {
                "id": GUILD_ID,
                "name": "Death and Destruction",
                "faction": {"id": 1, "name": "Horde"},
                "server": {
                    "name": "Skullcrusher",
                    "slug": "skullcrusher",
                    "region": {"compactName": "US", "slug": "us"},
                },
            }
        }
    }
}


def build_root(root) -> None:
    newest, older, missing, old = (
        NOW - timedelta(days=3),
        NOW - timedelta(days=11),
        NOW - timedelta(days=6),
        NOW - timedelta(days=40),
    )
    _night(root, "SYNTHA", newest, {})  # Alphadps (actor 3) in both pulls
    _night(root, "SYNTHB", older, {2: [1, 2, 4, 5]})  # Alphadps in fight 1 only
    _night(root, "SYNTHOLD", old, {})  # outside the 30-day window
    _search(root, search_guild_path("us", "skullcrusher", "death and destruction"), GUILD)
    _search(root, search_guild_path("us", "skullcrusher", "nobody"), {"data": {"guildData": {"guild": None}}})
    listing = [
        _listed("SYNTHB", older),
        _listed("SYNTHA", newest),
        _listed("GONE01", missing),
        _listed("SYNTHOLD", old),
    ]
    _search(root, search_guild_reports_path(GUILD_ID), {"data": {"reportData": {"reports": {"data": listing}}}})
    _search(
        root,
        search_character_path("us", "skullcrusher", "alphadps"),
        {
            "data": {
                "characterData": {
                    "character": {
                        "id": 5,
                        "name": "Alphadps",
                        "server": {"name": "Skullcrusher", "slug": "skullcrusher", "region": {"compactName": "US"}},
                        "recentReports": {"has_more_pages": True, "data": listing},
                    }
                }
            }
        },
    )
    _search(
        root,
        search_character_path("us", "skullcrusher", "nobody"),
        {"data": {"characterData": {"character": None}}, "errors": [{"message": "synthetic: no such character"}]},
    )


class CountingTransport(ReplayTransport):
    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw)
        self.operations: list[str] = []

    async def handle_async_request(self, request):
        await request.aread()
        try:
            query = json.loads(request.content).get("query", "")
        except ValueError:
            query = ""
        if query.lstrip().startswith("query"):
            self.operations.append(query.split()[1].split("(")[0])
        return await super().handle_async_request(request)


class Env:
    def __init__(
        self,
        tmp_path,
        *,
        limit: float = 3600.0,
        search: SearchConfig | None = None,
        root=None,
        now: datetime = NOW,
    ) -> None:
        if root is None:
            root = tmp_path / "wcl"
            build_root(root)
        self.root = root
        self.moment = MovableClock(now)
        self.transport = CountingTransport(root)
        self.store = MemoryAuthStore()
        config = replace(
            AuthConfig(),
            redirect_uri="http://localhost:8000/v1/auth/callback",
            post_login_url=WEB_ORIGIN,
            web_origins=(WEB_ORIGIN,),
            token_keys=(Fernet.generate_key().decode(),),
            wcl_scope="",
        )
        self.service = AuthService(
            self.store,
            config,
            client_id=FAKE_CLIENT_ID,
            client_secret=FAKE_CLIENT_SECRET,
            clock=self.moment,
            wcl_client=lambda: replay_client(self.transport),
        )
        self.budget = BudgetManager(
            MemoryBudgetBackend(), budget_config(), clock=self.moment, reader=FakeReader(limit=limit)
        )
        self.asks: list = []
        real_request = self.budget.request

        async def spy(ask):
            self.asks.append(ask)
            return await real_request(ask)

        self.budget.request = spy  # type: ignore[method-assign]
        self.app = create_app(mode="hosted", data_dir=tmp_path / "data", auth=self.service)
        self.app.state.budget = self.budget
        self.app.state.lookup_clock = self.moment
        if search is not None:
            self.app.state.search_config = search

    def browser(self, name: str | None = None) -> TestClient:
        client = TestClient(self.app, base_url="http://localhost:8000")
        if name is not None:
            user = self.store.upsert_user(1000 + len(name), name, self.moment())
            asyncio.run(
                self.service.tokens.save(
                    user.id, TokenSet(SecretStr(f"fake-access-{name}"), SecretStr(f"fake-refresh-{name}"), 3600)
                )
            )
            client.cookies.set(SESSION_COOKIE, asyncio.run(self.service.sessions.create(user.id)))
        return client

    def count(self, operation: str) -> int:
        return self.transport.operations.count(operation)


def guild(client, **params):
    return client.get("/v1/search/guild", params=params)


def character(client, **params):
    return client.get("/v1/search/character", params={"region": "us", "server": "skullcrusher", **params})


DND = {"region": "us", "server": "skullcrusher", "name": "Death and Destruction"}
