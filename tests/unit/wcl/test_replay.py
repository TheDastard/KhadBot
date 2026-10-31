"""
tests/unit/wcl/test_replay.py

The WCL replay transport: recorded reports and pages answer, a missing report answers with a GraphQL error, anything
unrecorded is refused by name; sign-in passes through; the simulated rate-limit counter charges, resets and answers
429; the live report reveals its recorded fights one at a time; and replay is off unless configured. No test touches
the network: every one runs under a socket guard.
"""

from __future__ import annotations

import json
import shutil

import httpx
import pytest
from helpers.encounter.synth import SYNTH_DEFENSIVE_COOLDOWN_IDS, SYNTH_ENEMY_CAST_IDS, SYNTH_OFFENSIVE_COOLDOWN_IDS
from helpers.wcl.replay import WCL_FIXTURES, guard_network, replay_client

from khadbot.config import WclReplayConfig, reset_config
from khadbot.encounter.ingest import wcl_queries
from khadbot.encounter.ingest.fetch import RawPageCache, fetch_batches, fetch_pull, filter_hash, specs_for
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.errors import ConfigurationError
from khadbot.wcl import replay
from khadbot.wcl.client import (
    GRAPHQL_USER_URL,
    TOKEN_URL,
    WarcraftLogsAPIError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
    WarcraftLogsRateLimitError,
)
from khadbot.wcl.oauth import current_user, exchange_code
from khadbot.wcl.replay import ReplayTransport, WclReplayRefusedError
from khadbot.wcl.reports import REPORT_READABLE_QUERY, report_readable

CODE = "SYNTH01"
META = json.loads((WCL_FIXTURES / CODE / "report.json").read_text(encoding="utf-8"))
PULLS, _ = parse_report_meta(CODE, META)
SPECS = specs_for(SYNTH_DEFENSIVE_COOLDOWN_IDS, SYNTH_ENEMY_CAST_IDS[90001], SYNTH_OFFENSIVE_COOLDOWN_IDS)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = guard_network(monkeypatch)
    yield attempts
    assert attempts == []


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


def transport(clock=None, **kw) -> ReplayTransport:
    return ReplayTransport(WCL_FIXTURES, clock=clock or Clock(), **kw)


def events_variables(fight_ids, spec, start, end):
    return {
        "code": CODE,
        "fightIDs": fight_ids,
        "startTime": start,
        "endTime": end,
        "dataType": spec.data_type,
        "hostility": spec.hostility,
        "filter": spec.filter_expression,
        "limit": 10000,
    }


# ---------------------------------------------------------------------------
# The layout and queries replay relies on
# ---------------------------------------------------------------------------


def test_replay_reads_the_raw_cache_layout_and_knows_the_queries_ingest_sends():
    for spec in SPECS:
        assert replay.filter_hash(spec.filter_expression) == filter_hash(spec.filter_expression)
        assert replay.spec_dir_name(spec.data_type, spec.hostility) == spec.dir_name
    assert "query GetReportMeta(" in wcl_queries.GET_REPORT_META
    assert "query GetEventsPage(" in wcl_queries.GET_EVENTS_PAGE
    assert "query ReportReadable(" in REPORT_READABLE_QUERY


# ---------------------------------------------------------------------------
# Recorded data
# ---------------------------------------------------------------------------


async def test_report_metadata_is_the_recorded_report_without_khadbots_keys(tmp_path):
    shutil.copytree(WCL_FIXTURES / CODE, tmp_path / CODE)
    meta = {**META, "khadbotFetchedWith": "app"}
    (tmp_path / CODE / "report.json").write_text(json.dumps(meta), encoding="utf-8")
    async with replay_client(ReplayTransport(tmp_path, clock=Clock())) as client:
        data = await client.query(wcl_queries.GET_REPORT_META, {"code": CODE})
    assert data["reportData"]["report"] == META


async def test_every_recorded_page_answers_and_a_pull_fetches_as_recorded(tmp_path):
    cache = RawPageCache(tmp_path / "raw")
    pull = PULLS[0]  # fight 1: DamageTaken is two pages
    async with replay_client(transport()) as client:
        flags = await fetch_pull(client, cache, CODE, pull, SPECS)
    recorded = RawPageCache(WCL_FIXTURES)
    assert not any(flags.values())
    for spec in SPECS:
        assert cache.pages(CODE, 1, spec) == recorded.pages(CODE, 1, spec)
    assert len(cache.pages(CODE, 1, SPECS[1])) == 2


async def test_a_recorded_report_is_readable_and_an_unknown_one_answers_as_missing():
    async with replay_client(transport()) as client:
        assert await report_readable(client, CODE) is True
        assert await report_readable(client, "NOSUCHREPORT1") is False
        with pytest.raises(WarcraftLogsAPIError) as missing:
            await client.query(wcl_queries.GET_REPORT_META, {"code": "NOSUCHREPORT1"})
    assert not isinstance(missing.value, WarcraftLogsPrivateReportError | WclReplayRefusedError)
    assert missing.value.errors and "NOSUCHREPORT1" in missing.value.errors[0]["message"]


async def test_an_unrecorded_filter_is_refused_naming_its_hash():
    spec = SPECS[0].model_copy(update={"data_type": "Buffs", "filter_expression": "ability.id IN (1)"})
    pull = PULLS[0]
    async with replay_client(transport()) as client:
        with pytest.raises(WclReplayRefusedError) as refused:
            await client.query(wcl_queries.GET_EVENTS_PAGE, events_variables([1], spec, pull.start_ms, pull.end_ms))
    message = str(refused.value)
    assert f"{CODE}:1 Buffs filter {filter_hash('ability.id IN (1)')}" in message


async def test_a_page_that_was_never_recorded_is_refused():
    pull, spec = PULLS[0], SPECS[0]
    async with replay_client(transport()) as client:
        with pytest.raises(WclReplayRefusedError, match="starting at 123"):
            await client.query(wcl_queries.GET_EVENTS_PAGE, events_variables([1], spec, 123, pull.end_ms))
        with pytest.raises(WclReplayRefusedError, match="up to"):
            await client.query(wcl_queries.GET_EVENTS_PAGE, events_variables([1], spec, pull.start_ms, 1))
        with pytest.raises(WclReplayRefusedError, match="no fight 99"):
            await client.query(wcl_queries.GET_EVENTS_PAGE, events_variables([99], spec, pull.start_ms, 1))


async def test_a_multi_fight_request_is_refused_without_a_recorded_batch_stream():
    spec = SPECS[0]
    async with replay_client(transport()) as client:
        with pytest.raises(WclReplayRefusedError, match=r"fights \[1, 2\] Deaths filter none.*multi-fight"):
            await client.query(
                wcl_queries.GET_EVENTS_PAGE,
                events_variables([1, 2], spec, PULLS[0].start_ms, PULLS[1].end_ms),
            )


async def test_a_recorded_batch_stream_replays(tmp_path):
    # A recording with a batch stream: SYNTH01's Deaths for both fights as one stream of two pages. Real rows name
    # their fight; the synthetic ones don't, so each is tagged with it here.
    shutil.copytree(WCL_FIXTURES / CODE, tmp_path / "rec" / CODE)
    recording = RawPageCache(tmp_path / "rec")
    spec = SPECS[0]

    def rows_of(fight: int) -> list[dict]:
        pages = RawPageCache(WCL_FIXTURES).pages(CODE, fight, spec)
        return [{**r, "fight": fight} for page in pages for r in page["data"]]

    rows = [r for p in PULLS for r in rows_of(p.fight_id)]
    split = PULLS[1].start_ms
    recording.write_batch_page(CODE, spec, [1, 2], 0, {"data": rows[:1], "nextPageTimestamp": split})
    recording.write_batch_page(CODE, spec, [1, 2], 1, {"data": rows[1:], "nextPageTimestamp": None})
    cache = RawPageCache(tmp_path / "raw")
    async with replay_client(ReplayTransport(tmp_path / "rec", clock=Clock())) as client:
        truncated = await fetch_batches(client, cache, CODE, PULLS, {p.fight_id: [spec] for p in PULLS})
    assert truncated == {}
    for pull in PULLS:
        got = [r for page in cache.pages(CODE, pull.fight_id, spec) for r in page["data"]]
        assert got == rows_of(pull.fight_id) and got


async def test_another_query_and_another_url_are_refused():
    async with replay_client(transport()) as client:
        with pytest.raises(WclReplayRefusedError, match="CharacterData"):
            await client.query("query CharacterData { characterData { character(id: 1) { name } } }")
    async with httpx.AsyncClient(transport=transport()) as http:
        with pytest.raises(WclReplayRefusedError, match="GET request"):
            await http.get("https://www.warcraftlogs.com/api/v1/report")


async def test_an_lfs_pointer_is_refused_with_the_command_to_fix_it(tmp_path):
    (tmp_path / "LFSCODE").mkdir()
    (tmp_path / "LFSCODE" / "report.json").write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:0\n")
    async with replay_client(ReplayTransport(tmp_path, clock=Clock())) as client:
        with pytest.raises(WclReplayRefusedError, match="git lfs pull"):
            await client.query(wcl_queries.GET_REPORT_META, {"code": "LFSCODE"})


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------


async def test_the_app_token_is_local_and_sign_in_goes_to_real_wcl():
    sent: list[str] = []

    async def real(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "fake-user-access", "refresh_token": "fake-refresh"})
        return httpx.Response(200, json={"data": {"userData": {"currentUser": {"id": 7, "name": "Khadgar"}}}})

    t = ReplayTransport(WCL_FIXTURES, clock=Clock(), passthrough=real)
    async with replay_client(t) as client:
        await client.query(wcl_queries.GET_REPORT_META, {"code": CODE})  # the app token is answered locally
        user = await current_user(client, "fake-user-access")
    async with httpx.AsyncClient(transport=t) as http:
        tokens = await exchange_code(
            http, client_id="fake-id", client_secret="fake-secret", code="c", verifier="v", redirect_uri="http://x"
        )
    assert (user.id, user.name) == (7, "Khadgar")
    assert tokens.access_token.get_secret_value() == "fake-user-access"
    assert sent == [GRAPHQL_USER_URL, TOKEN_URL]


# ---------------------------------------------------------------------------
# The rate-limit counter
# ---------------------------------------------------------------------------


async def test_each_request_costs_a_point_and_a_read_shows_the_count_before_its_own(clock):
    async with replay_client(transport(clock, limit_per_hour=100, reset_s=600)) as client:
        first = await client.rate_limit()
        await client.query(wcl_queries.GET_REPORT_META, {"code": CODE})
        second = await client.rate_limit(user_token="fake-user-token")  # one pool on the current scheme
        clock.now += 250
        third = await client.rate_limit()
    assert first == {"limitPerHour": 100, "pointsSpentThisHour": 0, "pointsResetIn": 600}
    assert second["pointsSpentThisHour"] == 2
    assert third["pointsSpentThisHour"] == 3 and third["pointsResetIn"] == 350


async def test_refusals_cost_nothing():
    t = transport()
    async with replay_client(t) as client:
        with pytest.raises(WclReplayRefusedError):
            await client.query("query Other { x }")
        assert (await client.rate_limit())["pointsSpentThisHour"] == 0


async def test_an_empty_window_answers_429_until_it_resets(clock):
    async with replay_client(transport(clock, limit_per_hour=2, reset_s=60)) as client:
        await client.query(wcl_queries.GET_REPORT_META, {"code": CODE})
        await client.rate_limit()
        with pytest.raises(WarcraftLogsRateLimitError) as limited:
            await client.query(wcl_queries.GET_REPORT_META, {"code": CODE})
        assert limited.value.retry_after_s is None  # WCL is not known to send Retry-After, so replay doesn't
        clock.now += 61
        reading = await client.rate_limit()
    assert reading["pointsSpentThisHour"] == 0 and reading["pointsResetIn"] == 59


# ---------------------------------------------------------------------------
# The live report
# ---------------------------------------------------------------------------


async def live_fights(client) -> list[int]:
    data = await client.query(wcl_queries.GET_REPORT_META, {"code": CODE})
    return [f["id"] for f in data["reportData"]["report"]["fights"]]


async def test_the_live_report_shows_one_more_recorded_fight_per_metadata_request():
    async with replay_client(transport(live_report=CODE)) as client:
        seen = [await live_fights(client) for _ in range(3)]
        assert await report_readable(client, CODE)  # the access check reveals nothing
        assert await live_fights(client) == [1, 2]
    assert seen == [[1], [1, 2], [1, 2]]


async def test_the_live_report_can_reveal_by_interval(clock):
    async with replay_client(transport(clock, live_report=CODE, live_interval_s=300)) as client:
        first = await live_fights(client)
        clock.now += 299
        still = await live_fights(client)
        clock.now += 1
        later = await live_fights(client)
    assert (first, still, later) == ([1], [1], [1, 2])


async def test_the_live_report_hides_fights_with_no_recorded_pages(tmp_path):
    shutil.copytree(WCL_FIXTURES / CODE, tmp_path / CODE)
    shutil.rmtree(tmp_path / CODE / "2")
    async with replay_client(ReplayTransport(tmp_path, clock=Clock(), live_report=CODE)) as client:
        assert [await live_fights(client) for _ in range(2)] == [[1], [1]]
        other = ReplayTransport(tmp_path, clock=Clock())  # not live: the recorded report.json as it is
    async with replay_client(other) as client:
        assert await live_fights(client) == [1, 2]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@pytest.fixture
def replay_env_vars(monkeypatch):
    for key in ("KHADBOT_WCL_REPLAY", "KHADBOT_WCL_REPLAY_LIVE"):
        monkeypatch.delenv(key, raising=False)
    replay.reset_configured_transport()
    yield monkeypatch
    replay.reset_configured_transport()
    reset_config()


def test_replay_is_off_by_default(replay_env_vars):
    assert not WclReplayConfig().enabled
    assert replay.configured_transport() is None


async def test_a_client_replays_when_the_setting_names_a_directory(replay_env_vars):
    replay_env_vars.setenv("KHADBOT_WCL_REPLAY", str(WCL_FIXTURES))
    replay_env_vars.setenv("KHADBOT_WCL_REPLAY_LIMIT", "50")
    configured = replay.configured_transport()
    assert configured is not None and configured is replay.configured_transport()  # one per process
    async with WarcraftLogsClient("fake-client-id", "fake-client-secret") as client:
        data = await client.query(wcl_queries.GET_REPORT_META, {"code": CODE})
        reading = await client.rate_limit()
    assert data["reportData"]["report"]["title"] == META["title"]
    assert reading["limitPerHour"] == 50 and reading["pointsSpentThisHour"] == 1


def test_settings_that_cannot_work_fail_fast(replay_env_vars, tmp_path):
    replay_env_vars.setenv("KHADBOT_WCL_REPLAY", str(tmp_path / "missing"))
    with pytest.raises(ConfigurationError, match="not a directory"):
        replay.configured_transport()
    replay_env_vars.setenv("KHADBOT_WCL_REPLAY", str(WCL_FIXTURES))
    replay_env_vars.setenv("KHADBOT_WCL_REPLAY_LIVE", "NOTRECORDED")
    with pytest.raises(ConfigurationError, match="KHADBOT_WCL_REPLAY_LIVE"):
        replay.configured_transport()
