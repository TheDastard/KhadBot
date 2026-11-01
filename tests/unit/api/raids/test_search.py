"""
tests/unit/api/raids/test_search.py

``GET /v1/search/guild`` and ``/v1/search/character`` on the hosted app with WCL answered by replay: the synthetic
replay directory of ``helpers.api.search`` for the window, caching, sharing, database points, the cap and the typed
errors, then the recorded searches of ``tests/fixtures/encounter/wcl/_search/`` replayed offline.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from helpers.api.search import DND, ENCOUNTER, GUILD_ID, Env, character, guild
from helpers.jobs.budget import FakeReader
from helpers.wcl.replay import WCL_FIXTURES, guard_network

from khadbot.api import create_app
from khadbot.api.raids.nights import character_actor_ids
from khadbot.config import SearchConfig
from khadbot.encounter.ingest.recorded import is_recorded
from khadbot.wcl.replay import search_character_path, search_guild_reports_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = guard_network(monkeypatch)
    yield
    assert attempts == []


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


# ---------------------------------------------------------------------------
# Flag
# ---------------------------------------------------------------------------


def test_the_flag_is_on_where_the_routes_are_mounted(env, tmp_path):
    assert env.browser().get("/v1/health").json()["features"]["raid_search"] is True
    local = TestClient(create_app(mode="local", data_dir=tmp_path / "local"))
    assert local.get("/v1/health").json()["features"]["raid_search"] is False
    assert local.get("/v1/servers").status_code == 404
    assert local.get("/v1/search/guild", params=DND).status_code == 404


# ---------------------------------------------------------------------------
# Guild
# ---------------------------------------------------------------------------


def test_a_guild_search_lists_readable_nights_in_the_window_newest_first(env):
    body = guild(env.browser(), **DND).json()
    assert body["guild"] == {
        "id": GUILD_ID,
        "name": "Death and Destruction",
        "server_name": "Skullcrusher",
        "server_slug": "skullcrusher",
        "region": "US",
        "faction": "Horde",
    }
    assert [n["code"] for n in body["nights"]] == ["SYNTHA", "SYNTHB"]  # SYNTHOLD is outside the window
    assert body["left_out"] == 1  # GONE01: listed, but no report the caller can read
    assert body["capped"] is False
    night = body["nights"][0]
    assert [(g["encounter_id"], [p["fight"] for p in g["pulls"]]) for g in night["groups"]] == [(ENCOUNTER, [1, 2])]
    assert night["focus_fight"] is None
    assert body["window_start"].startswith("2026-10-01T12:00")


def test_the_guild_url_finds_the_same_guild(env):
    url = "https://www.warcraftlogs.com/guild/us/skullcrusher/death%20and%20destruction"
    assert guild(env.browser(), url=url).json()["guild"]["id"] == GUILD_ID


def test_the_listing_spends_database_points_and_the_header_does_not(env):
    guild(env.browser(), **DND)
    by_kind = {a.job_id.split(":")[0] + ":" + str(a.database_points) for a in env.asks}
    assert "search:1.0" in by_kind and "search:0.0" in by_kind
    assert env.count("GuildReports") == 1 and env.count("GuildHeader") == 1


def test_a_second_search_reuses_the_listing_and_the_nights(env):
    client = env.browser()
    guild(client, **DND)
    meta = env.count("GetReportMeta")
    guild(client, **DND)
    assert (env.count("GuildHeader"), env.count("GuildReports"), env.count("GetReportMeta")) == (1, 1, meta)


def test_after_the_listing_window_only_the_listing_is_asked_again(env):
    client = env.browser()
    guild(client, **DND)
    env.moment.advance(seconds=SearchConfig().listing_max_age_s - 1)
    guild(client, **DND)
    assert (env.count("GuildHeader"), env.count("GuildReports")) == (1, 1)
    env.moment.advance(seconds=2)
    assert guild(client, **DND).json()["guild"]["faction"] == "Horde"
    assert (env.count("GuildHeader"), env.count("GuildReports")) == (1, 2)  # the identity is kept for good


def test_the_identity_and_the_public_listing_survive_a_restart(tmp_path):
    first = Env(tmp_path)
    guild(first.browser(), **DND)
    second = Env(tmp_path, root=first.root)  # a new app on the same data directory (blob store)
    second.moment.now = first.moment.now
    assert guild(second.browser(), **DND).status_code == 200
    assert (second.count("GuildHeader"), second.count("GuildReports")) == (0, 0)


def test_two_visitors_share_one_public_listing(env):
    assert guild(env.browser(), **DND).status_code == 200
    assert guild(env.browser(), **DND).status_code == 200
    assert env.count("GuildReports") == 1


def test_listings_are_not_shared_between_users(env):
    guild(env.browser("Thrall"), **DND)
    guild(env.browser("Jaina"), **DND)
    assert env.count("GuildReports") == 2
    guild(env.browser(), **DND)  # a visitor gets the public listing, not either user's
    assert env.count("GuildReports") == 3
    assert env.count("GuildHeader") == 1  # the identity is shared: it is public


def test_a_users_own_listing_is_listed_on_their_token(env):
    guild(env.browser("Thrall"), **DND)
    asks = [a for a in env.asks if a.database_points > 0]
    assert len(asks) == 1 and asks[0].user_token is True
    guild(env.browser(), **DND)
    visitor = [a for a in env.asks if a.database_points > 0][-1]
    assert visitor.user_token is False


def test_a_missing_guild_is_remembered_for_an_hour(env):
    client = env.browser()
    for _ in range(2):
        assert guild(client, **{**DND, "name": "Nobody"}).json()["code"] == "guild_not_found"
    assert env.count("GuildHeader") == 1
    env.moment.advance(seconds=SearchConfig().not_found_max_age_s)
    guild(client, **{**DND, "name": "Nobody"})
    assert env.count("GuildHeader") == 2


def test_the_cap_limits_the_nights_and_says_so(tmp_path):
    env = Env(tmp_path, search=replace(SearchConfig(), max_reports=1))
    body = guild(env.browser(), **DND).json()
    assert [n["code"] for n in body["nights"]] == ["SYNTHA"]
    assert body["capped"] is True


@pytest.mark.parametrize(
    ("params", "status", "code"),
    [
        ({**DND, "server": "nowhere"}, 404, "server_unknown"),
        ({**DND, "region": "kr"}, 404, "server_unknown"),
        ({**DND, "name": "Nobody"}, 404, "guild_not_found"),
        ({"url": "https://www.warcraftlogs.com/reports/ABC"}, 400, "bad_guild_url"),
        ({"region": "us"}, 422, "bad_request"),
    ],
)
def test_guild_errors_are_typed(env, params, status, code):
    response = guild(env.browser(), **params)
    assert (response.status_code, response.json()["code"]) == (status, code)


def test_an_exhausted_budget_answers_429_with_a_wait(tmp_path):
    env = Env(tmp_path, limit=10.0)
    env.budget._reader = FakeReader(limit=10.0, spent=10.0)
    asyncio.run(env.budget.sync())
    response = guild(env.browser(), **DND)
    assert response.status_code == 429
    assert response.json()["code"] == "budget_exhausted"
    assert int(response.headers["Retry-After"]) >= 1


# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------


def test_a_character_search_keeps_only_the_pulls_they_were_in(env):
    body = character(env.browser(), name="alphadps").json()
    assert body["character"]["name"] == "Alphadps"
    pulls = {n["code"]: [p["fight"] for g in n["groups"] for p in g["pulls"]] for n in body["nights"]}
    assert pulls == {"SYNTHA": [1, 2], "SYNTHB": [1], "SYNTHOLD": [1, 2]}
    assert [n["code"] for n in body["nights"]] == ["SYNTHA", "SYNTHB", "SYNTHOLD"]
    synthb = next(n for n in body["nights"] if n["code"] == "SYNTHB")["groups"][0]
    assert synthb["best_fight"] == 1 and synthb["pulls"][0]["label"] == "P1"
    assert body["capped"] is True and body["left_out"] == 1


def test_the_encounter_filter_narrows_the_pulls(env):
    assert character(env.browser(), name="alphadps", encounter=ENCOUNTER).json()["encounter_id"] == ENCOUNTER
    assert character(env.browser(), name="alphadps", encounter=12345).json()["nights"] == []


def test_a_character_search_spends_database_points(env):
    character(env.browser(), name="alphadps")
    listing = [a for a in env.asks if a.database_points > 0]
    assert len(listing) == 1 and env.count("CharacterReports") == 1


def test_a_character_listing_is_reused_inside_its_window(env):
    character(env.browser(), name="alphadps")
    character(env.browser(), name="Alphadps")
    assert env.count("CharacterReports") == 1
    env.moment.advance(seconds=SearchConfig().listing_max_age_s)
    character(env.browser(), name="alphadps")
    assert env.count("CharacterReports") == 2


def test_a_missing_character_is_remembered(env):
    for _ in range(2):
        assert character(env.browser(), name="nobody").json()["code"] == "character_not_found"
    assert env.count("CharacterReports") == 1


def test_an_unknown_character_is_404(env):
    response = character(env.browser(), name="nobody")
    assert (response.status_code, response.json()["code"]) == (404, "character_not_found")


# ---------------------------------------------------------------------------
# The recorded searches (Death and Destruction and Dastard on Skullcrusher-US)
# ---------------------------------------------------------------------------

RECORDED_GUILD = WCL_FIXTURES / search_guild_reports_path(27112)
RECORDED_CHARACTER = WCL_FIXTURES / search_character_path("us", "skullcrusher", "dastard")


def _recorded(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _listed_codes(body: dict, key: str) -> list[str]:
    data = body["data"]
    page = data["reportData"]["reports"] if key == "guild" else data["characterData"]["character"]["recentReports"]
    return [r["code"] for r in page["data"]]


def _needs_pages(codes: list[str]) -> None:
    if not all(is_recorded(WCL_FIXTURES, code) for code in codes):
        pytest.skip("the searched reports' metadata is not pulled from Git LFS")


@pytest.fixture
def recorded_env(tmp_path) -> Env:
    at = datetime.fromisoformat(_recorded(RECORDED_GUILD)["khadbotRecordedAt"])
    return Env(tmp_path, root=WCL_FIXTURES, now=at)


def test_the_recorded_guild_search_replays_offline(recorded_env):
    listed = _listed_codes(_recorded(RECORDED_GUILD), "guild")
    _needs_pages(listed)
    url = "https://www.warcraftlogs.com/guild/us/skullcrusher/death%20and%20destruction"
    body = guild(recorded_env.browser(), url=url).json()
    assert (body["guild"]["id"], body["guild"]["server_slug"], body["guild"]["region"]) == (27112, "skullcrusher", "US")
    assert body["guild"]["faction"] in {"Horde", "Alliance"}
    codes = [n["code"] for n in body["nights"]]
    assert codes and set(codes) <= set(listed)
    starts = [n["started_at"] for n in body["nights"]]
    assert starts == sorted(starts, reverse=True)
    assert body["capped"] is True  # WCL had more than the 15 reports listed


def test_the_recorded_character_search_keeps_only_dastards_pulls(recorded_env):
    listed = _listed_codes(_recorded(RECORDED_CHARACTER), "character")
    _needs_pages(listed)
    body = character(recorded_env.browser(), name="Dastard").json()
    assert body["nights"]
    for night in body["nights"]:
        meta = _recorded(WCL_FIXTURES / night["code"] / "report.json")
        actors = character_actor_ids(meta, "Dastard")
        theirs = {f["id"] for f in meta["fights"] if actors.intersection(f.get("friendlyPlayers") or [])}
        assert {p["fight"] for g in night["groups"] for p in g["pulls"]} <= theirs
