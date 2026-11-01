"""
tests/unit/api/raids/test_lookup.py

``GET /v1/lookup/report`` on the hosted app with WCL answered by replay (the recorded fixture reports): the pulls match
each recording's ``report.json``, a second lookup makes no WCL request, a stale live log is fetched again, access is
checked before anything cached is served, the budget gates the fetch and answers 429 with a wait, and ``ready``
follows the read model. Nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from helpers.encounter.real_fixtures import REAL_REPORTS
from helpers.jobs.budget import FakeReader, MovableClock, budget_config
from helpers.wcl.replay import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, WCL_FIXTURES, guard_network, replay_client
from pydantic import SecretStr

from khadbot.api import create_app
from khadbot.api.raids.lookup import best_fight, is_fresh
from khadbot.auth.routes import SESSION_COOKIE
from khadbot.auth.service import AuthService
from khadbot.auth.store import MemoryAuthStore
from khadbot.config import AuthConfig, LookupConfig
from khadbot.encounter.ingest.fetch import FETCHED_AT_KEY, FETCHED_WITH_KEY
from khadbot.encounter.models import Pull
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend
from khadbot.wcl.oauth import TokenSet
from khadbot.wcl.replay import ReplayTransport

URL = "/v1/lookup/report"
NIGHT = "H7z1fqDXjRTFnbtg"
SYNTH = "SYNTH01"
RECORDED = ["H7z1fqDXjRTFnbtg", "trMgApZCVYkv3jdq", "QkVa6GHRK8yfrTbw"]  # in Git LFS: absent without `git lfs pull`
needs_night = pytest.mark.skipif(NIGHT not in REAL_REPORTS, reason="real WCL fixtures not pulled from Git LFS")
WEB_ORIGIN = "http://localhost:3000"
NOT_FOUND = "Report not found or not shared with your Warcraft Logs account."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = guard_network(monkeypatch)
    yield
    assert attempts == []


class CountingTransport(ReplayTransport):
    """A replay transport that notes which GraphQL operations it was asked."""

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

    def count(self, operation: str) -> int:
        return self.operations.count(operation)


class Env:
    def __init__(self, tmp_path, *, limit: float = 3600.0) -> None:
        self.moment = MovableClock(datetime(2026, 10, 31, 12, 0, tzinfo=UTC))
        self.transport = CountingTransport(WCL_FIXTURES)
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
        self.app = create_app(mode="hosted", data_dir=tmp_path, auth=self.service)
        self.app.state.budget = self.budget
        self.app.state.lookup_clock = self.moment
        self.cache = self.app.state.ctx.raw

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


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


def lookup(client: TestClient, source: str):
    return client.get(URL, params={"source": source})


def recorded(code: str) -> dict:
    return json.loads((WCL_FIXTURES / code / "report.json").read_text(encoding="utf-8"))


def test_the_flag_is_on_where_the_route_is_mounted(env, tmp_path):
    assert env.browser().get("/v1/health").json()["features"]["report_lookup"] is True
    local = TestClient(create_app(mode="local", data_dir=tmp_path))
    assert local.get("/v1/health").json()["features"]["report_lookup"] is False
    assert local.get(URL, params={"source": SYNTH}).status_code == 404


@pytest.mark.parametrize(
    "code",
    [
        *(
            pytest.param(code, marks=pytest.mark.skipif(code not in REAL_REPORTS, reason="not pulled from Git LFS"))
            for code in RECORDED
        ),
        SYNTH,
    ],
)
def test_a_lookup_matches_the_recorded_report(env, code):
    body = lookup(env.browser(), code).json()
    meta = recorded(code)
    fights = [f for f in meta["fights"] if f.get("encounterID")]
    got = [(p["fight"], g["encounter_id"], g["difficulty"], p["outcome"]) for g in body["groups"] for p in g["pulls"]]
    assert sorted(got) == sorted(
        (f["id"], f["encounterID"], f.get("difficulty"), "kill" if f.get("kill") else "wipe") for f in fights
    )
    assert (body["code"], body["title"], body["focus_fight"]) == (code, meta.get("title"), None)
    assert body["started_at"].startswith(
        datetime.fromtimestamp(meta["startTime"] / 1000, UTC).strftime("%Y-%m-%dT%H:%M")
    )
    by_fight = {f["id"]: f for f in fights}
    for g in body["groups"]:
        assert [p["ordinal"] for p in g["pulls"]] == list(range(1, len(g["pulls"]) + 1))
        assert g["pulls"][0]["label"] == "P1"
        starts = [by_fight[p["fight"]]["startTime"] for p in g["pulls"]]
        assert starts == sorted(starts)
        for p in g["pulls"]:
            f = by_fight[p["fight"]]
            assert (p["boss_pct"], p["boss_hp_pct"], p["duration_ms"]) == (
                f.get("fightPercentage"),
                f.get("bossPercentage"),
                f["endTime"] - f["startTime"],
            )
            assert p["ready"] is False
        assert g["best_fight"] in {p["fight"] for p in g["pulls"]}


@needs_night
def test_groups_come_in_the_order_first_pulled(env):
    body = lookup(env.browser(), NIGHT).json()
    firsts = [(g["encounter_id"], g["difficulty"]) for g in body["groups"]]
    seen = []
    for f in sorted(recorded(NIGHT)["fights"], key=lambda f: (f["startTime"], f["id"])):
        key = (f.get("encounterID"), f.get("difficulty"))
        if f.get("encounterID") and key not in seen:
            seen.append(key)
    assert firsts == seen


@needs_night
def test_a_second_lookup_makes_no_wcl_request(env):
    visitor = env.browser()
    assert lookup(visitor, NIGHT).status_code == 200
    spent = env.transport.counter.reading()["pointsSpentThisHour"]
    assert env.transport.count("GetReportMeta") == 1
    again = lookup(env.browser("Jaina"), NIGHT)  # another caller; the access check is cached for visitors only
    assert again.status_code == 200
    assert env.transport.count("GetReportMeta") == 1
    assert lookup(visitor, NIGHT).status_code == 200
    assert env.transport.counter.reading()["pointsSpentThisHour"] - spent <= 1  # at most Jaina's access check


def test_one_lookup_costs_one_metadata_request(env):
    lookup(env.browser(), SYNTH)
    assert env.transport.count("GetReportMeta") == 1
    assert env.transport.operations.count("ReportReadable") == 1  # the access check, answered once
    state = asyncio.run(env.budget.state())  # the budget settled the estimate for exactly the one fetch
    assert [(r.ask.points, r.settled) for r in state.reservations] == [(LookupConfig().points, True)]


@needs_night
def test_the_fetch_is_stamped_with_the_token_kind_and_time(env):
    lookup(env.browser(), SYNTH)
    assert env.cache.report_meta(SYNTH)[FETCHED_WITH_KEY] == "app"
    lookup(env.browser("Thrall"), NIGHT)
    meta = env.cache.report_meta(NIGHT)
    assert meta[FETCHED_WITH_KEY] == "user"
    assert datetime.fromisoformat(meta[FETCHED_AT_KEY]) == env.moment()


@needs_night
def test_a_finished_log_stays_cached(env):
    lookup(env.browser(), NIGHT)
    env.moment.advance(days=3)
    lookup(env.browser(), NIGHT)
    assert env.transport.count("GetReportMeta") == 1


def test_a_log_that_may_still_be_live_is_fetched_again_after_the_age(env):
    # Fetched while the night's last fight was recent: a live log, however old the recording really is.
    start = recorded(SYNTH)["startTime"]
    last_end = max(f["endTime"] for f in recorded(SYNTH)["fights"])
    env.moment.now = datetime.fromtimestamp((start + last_end) / 1000, UTC) + timedelta(minutes=10)
    lookup(env.browser(), SYNTH)
    env.moment.advance(seconds=60)
    lookup(env.browser(), SYNTH)
    assert env.transport.count("GetReportMeta") == 1
    env.moment.advance(seconds=120)
    lookup(env.browser(), SYNTH)
    assert env.transport.count("GetReportMeta") == 2


def test_is_fresh_rules():
    cfg = LookupConfig(meta_max_age_s=120, settled_after_s=21600, points=1)
    now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    start_ms = int((now - timedelta(hours=10)).timestamp() * 1000)
    meta = {"startTime": start_ms, "fights": [{"endTime": 3_600_000}]}  # last fight ended 9 h ago
    assert not is_fresh(meta, now, cfg)  # no fetch time: unknown age
    assert is_fresh({**meta, FETCHED_AT_KEY: now.isoformat()}, now + timedelta(days=9), cfg)  # fetched settled
    early = (now - timedelta(hours=5)).isoformat()  # fetched 4 h after the last fight: it could have grown since
    assert not is_fresh({**meta, FETCHED_AT_KEY: early}, now, cfg)
    assert is_fresh({**meta, FETCHED_AT_KEY: early}, now - timedelta(hours=5) + timedelta(seconds=119), cfg)
    assert not is_fresh({**meta, FETCHED_AT_KEY: "not a time"}, now, cfg)


@needs_night
def test_a_url_names_the_focus_fight(env):
    fight = recorded(NIGHT)["fights"][-1]["id"]
    body = lookup(env.browser(), f"https://www.warcraftlogs.com/reports/{NIGHT}#fight={fight}").json()
    assert body["focus_fight"] == fight
    unknown = lookup(env.browser(), f"https://www.warcraftlogs.com/reports/{NIGHT}#fight=9999").json()
    assert unknown["focus_fight"] is None


def test_a_bad_source_is_400(env):
    for source in ("not a report!", "https://example.com/reports/abc", "https://warcraftlogs.com.evil.io/reports/abc"):
        resp = lookup(env.browser(), source)
        assert (resp.status_code, resp.json()["code"]) == (400, "bad_source")


def test_a_missing_report_is_404(env):
    resp = lookup(env.browser(), "NoSuchReport1")
    assert (resp.status_code, resp.json()) == (404, {"code": "report_not_found", "message": NOT_FOUND})


def test_cached_metadata_is_not_served_to_a_caller_who_cannot_read_the_report(env):
    # Metadata of a log only its member could read, cached by a user-token fetch: replay has no such report.
    env.cache.write_report_meta(
        "Private00001",
        {**recorded(SYNTH), FETCHED_WITH_KEY: "user", FETCHED_AT_KEY: env.moment().isoformat()},
    )
    for browser in (env.browser(), env.browser("Jaina")):
        resp = lookup(browser, "Private00001")
        assert (resp.status_code, resp.json()) == (404, {"code": "report_not_found", "message": NOT_FOUND})
    assert env.transport.count("GetReportMeta") == 0


def test_ready_follows_the_read_model(env):
    fights = [f["id"] for f in recorded(SYNTH)["fights"] if f.get("encounterID")]
    env.app.state.ctx.read_source = SimpleNamespace(pulls=lambda code: [SimpleNamespace(fight_id=fights[0])])
    body = lookup(env.browser(), SYNTH).json()
    ready = {p["fight"]: p["ready"] for g in body["groups"] for p in g["pulls"]}
    assert ready[fights[0]] is True and not any(v for k, v in ready.items() if k != fights[0])


def test_no_points_free_answers_429_with_the_wait(tmp_path):
    env = Env(tmp_path, limit=10.0)
    env.budget._reader = FakeReader(limit=10.0, spent=10.0)
    asyncio.run(env.budget.sync())
    resp = lookup(env.browser(), SYNTH)
    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == "budget_exhausted" and body["wait_s"] >= 0
    assert int(resp.headers["retry-after"]) >= 1
    assert env.transport.count("GetReportMeta") == 0
    assert env.cache.report_meta(SYNTH) is None
    assert asyncio.run(env.budget.state()).reservations == ()  # the lookup does not wait in line or hold points


def test_best_fight_is_the_first_kill_else_the_lowest_fight_percent():
    def pull(fight, kill, pct, hp=None, start=0):
        return Pull(
            report_code="X", fight_id=fight, encounter_id=1, encounter_name="B", difficulty=5, kill=kill,
            start_ms=start, end_ms=start + 1, duration_ms=1, last_phase=None, boss_pct=pct, boss_hp_pct=hp,
            friendly_player_ids=[],
        )  # fmt: skip

    wipes = [pull(1, False, 40.0), pull(2, False, 12.5, hp=20.0), pull(3, False, 12.5, hp=30.0), pull(4, False, None)]
    assert best_fight(wipes) == 2
    assert best_fight([*wipes, pull(5, True, 0.01), pull(6, True, 0.01)]) == 5
    assert best_fight([pull(7, False, None, start=5), pull(8, False, None, start=2)]) == 8
