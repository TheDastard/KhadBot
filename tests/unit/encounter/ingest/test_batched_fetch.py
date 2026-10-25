"""
tests/unit/encounter/ingest/test_batched_fetch.py

Batched fetch: one paginated stream per small spec across several pulls, split by each row's ``fight`` into the
per-pull cache entries. WCL is replaced by a stub client (no network); asyncio.sleep is patched.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from helpers.encounter.synth import (
    FIXTURES,
    SYNTH_DEFENSIVE_COOLDOWN_IDS,
    SYNTH_ENEMY_CAST_IDS,
    SYNTH_OFFENSIVE_COOLDOWN_IDS,
)

from khadbot.config import EncounterConfig
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import (
    BATCHED_SPEC_KEYS,
    RawPageCache,
    fetch_batch,
    fetch_batches,
    fetch_pull,
)
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.encounter.models import FetchSpec, Pull
from khadbot.encounter.store import ParquetEventStore
from khadbot.storage import join_key
from khadbot.wcl.client import WarcraftLogsAPIError

CODE = "ABC123"
CFG = EncounterConfig()
DEATHS = FetchSpec(data_type="Deaths")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("khadbot.encounter.ingest.fetch.asyncio.sleep", sleep)
    return sleep


def make_pull(fight: int, start: int, end: int) -> Pull:
    return Pull(
        report_code=CODE,
        fight_id=fight,
        encounter_id=7,
        encounter_name="Boss",
        difficulty=5,
        kill=False,
        start_ms=start,
        end_ms=end,
        duration_ms=end - start,
        last_phase=1,
        boss_pct=50.0,
        friendly_player_ids=[1],
    )


P1 = make_pull(1, 1_000, 50_000)
P2 = make_pull(2, 60_000, 100_000)
P3 = make_pull(3, 110_000, 150_000)


def row(t: int, fight: int | None) -> dict:
    out = {"timestamp": t, "type": "death", "sourceID": 1, "targetID": 1}
    if fight is not None:
        out["fight"] = fight
    return out


def page(rows: list[dict], nxt: int | None = None) -> dict:
    return {"data": rows, "nextPageTimestamp": nxt}


class StubClient:
    """Serves page chains keyed by (dataType, hostility, fightIDs); the i-th such request gets page i."""

    def __init__(
        self,
        chains: dict[tuple[str, str, tuple[int, ...]], list[dict]] | None = None,
        fail: Callable[[dict], Exception | None] | None = None,
    ) -> None:
        self.chains = chains or {}
        self.fail = fail
        self.calls: list[dict] = []

    async def query(self, gql: str, variables: dict | None = None, **_):
        assert variables is not None
        self.calls.append(variables)
        if self.fail and (exc := self.fail(variables)):
            raise exc
        key = (variables["dataType"], variables["hostility"], tuple(variables["fightIDs"]))
        served = sum(1 for c in self.calls if (c["dataType"], c["hostility"], tuple(c["fightIDs"])) == key)
        pages = self.chains.get(key) or [page([])]
        return {"reportData": {"report": {"events": pages[min(served, len(pages)) - 1]}}}


def rows_of(cache: RawPageCache, fight: int, spec: FetchSpec = DEATHS) -> list[int]:
    return [r["timestamp"] for p in cache.pages(CODE, fight, spec) for r in p["data"]]


def batch_keys(cache: RawPageCache) -> list[str]:
    return cache.blobs.list(join_key(cache.prefix, CODE, "batch") + "/")


# ===========================================================================
# fetch_batch
# ===========================================================================


class TestFetchBatch:
    async def test_one_request_split_into_each_pulls_entry(self, tmp_path):
        cache = RawPageCache(tmp_path)
        client = StubClient({("Deaths", "Friendlies", (1, 2)): [page([row(70_000, 2), row(2_000, 1), row(3_000, 1)])]})
        out = await fetch_batch(client, cache, CODE, [P2, P1], DEATHS, cfg=CFG, max_retries=1)

        assert out == {1: False, 2: False}
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["fightIDs"] == [1, 2]
        assert (call["startTime"], call["endTime"]) == (1_000, 100_000)
        assert cache.pages(CODE, 1, DEATHS) == [page([row(2_000, 1), row(3_000, 1)])]
        assert cache.pages(CODE, 2, DEATHS) == [page([row(70_000, 2)])]
        assert cache.is_complete(CODE, 1, DEATHS) and cache.is_complete(CODE, 2, DEATHS)
        assert batch_keys(cache) == []

    async def test_pull_without_rows_gets_an_empty_page(self, tmp_path):
        cache = RawPageCache(tmp_path)
        client = StubClient({("Deaths", "Friendlies", (1, 2)): [page([row(2_000, 1)])]})
        await fetch_batch(client, cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert cache.pages(CODE, 2, DEATHS) == [page([])]
        assert cache.is_complete(CODE, 2, DEATHS)

    async def test_pagination_keeps_row_order_per_fight(self, tmp_path):
        cache = RawPageCache(tmp_path)
        client = StubClient(
            {
                ("Deaths", "Friendlies", (1, 2)): [
                    page([row(2_000, 1), row(61_000, 2)], 30_000),
                    page([row(40_000, 1), row(90_000, 2)]),
                ]
            }
        )
        await fetch_batch(client, cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert [c["startTime"] for c in client.calls] == [1_000, 30_000]
        assert rows_of(cache, 1) == [2_000, 40_000]
        assert rows_of(cache, 2) == [61_000, 90_000]
        assert len(cache.pages(CODE, 1, DEATHS)) == 1  # one merged page per pull

    async def test_resumes_from_the_last_cached_batch_page(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.write_batch_page(CODE, DEATHS, [1, 2], 0, page([row(2_000, 1)], 30_000))  # then "crash"
        client = StubClient({("Deaths", "Friendlies", (1, 2)): [page([row(61_000, 2)])]})
        await fetch_batch(client, cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert [c["startTime"] for c in client.calls] == [30_000]
        assert rows_of(cache, 1) == [2_000]
        assert rows_of(cache, 2) == [61_000]

    async def test_completed_stream_without_split_makes_no_request(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.write_batch_page(CODE, DEATHS, [1, 2], 0, page([row(2_000, 1), row(61_000, 2)]))
        cache.mark_batch_complete(CODE, DEATHS, [1, 2])
        client = StubClient()
        await fetch_batch(client, cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert client.calls == []
        assert rows_of(cache, 1) == [2_000] and rows_of(cache, 2) == [61_000]
        assert batch_keys(cache) == []

    async def test_final_page_without_marker_is_completed_without_a_request(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.write_batch_page(CODE, DEATHS, [1, 2], 0, page([row(2_000, 1)]))
        client = StubClient()
        await fetch_batch(client, cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert client.calls == []
        assert cache.is_complete(CODE, 1, DEATHS) and cache.is_complete(CODE, 2, DEATHS)

    async def test_crash_mid_split_leaves_the_finished_pull_alone(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.write_batch_page(CODE, DEATHS, [1, 2], 0, page([row(2_000, 1), row(61_000, 2)]))
        cache.mark_batch_complete(CODE, DEATHS, [1, 2])
        mine = page([row(2_500, 1)])  # not what the batch holds: proves the entry is not rewritten
        cache.replace_pages(CODE, 1, DEATHS, [mine])
        cache.mark_complete(CODE, 1, DEATHS)

        await fetch_batch(StubClient(), cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)

        assert cache.pages(CODE, 1, DEATHS) == [mine]
        assert rows_of(cache, 2) == [61_000]
        assert cache.is_complete(CODE, 2, DEATHS)
        assert batch_keys(cache) == []

    async def test_a_partial_entry_without_a_marker_is_replaced(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.write_batch_page(CODE, DEATHS, [1, 2], 0, page([row(2_000, 1), row(61_000, 2)]))
        cache.mark_batch_complete(CODE, DEATHS, [1, 2])
        cache.write_page(CODE, 2, DEATHS, 0, page([row(1, 2)], 5))  # a stale partial per-pull page
        cache.write_page(CODE, 2, DEATHS, 1, page([row(2, 2)], 6))
        await fetch_batch(StubClient(), cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert cache.pages(CODE, 2, DEATHS) == [page([row(61_000, 2)])]

    async def test_stall_marks_only_pulls_ending_after_it(self, tmp_path, caplog):
        cache = RawPageCache(tmp_path)
        client = StubClient(
            {
                ("Deaths", "Friendlies", (1, 2)): [
                    page([row(2_000, 1)], 55_000),
                    page([row(61_000, 2)], 55_000),  # did not advance: stalled at 55_000
                ]
            }
        )
        with caplog.at_level(logging.WARNING):
            out = await fetch_batch(client, cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert out == {1: False, 2: True}
        assert len(client.calls) == 2
        assert "did not advance" in caplog.text
        assert cache.is_complete(CODE, 1, DEATHS) and cache.is_complete(CODE, 2, DEATHS)

    async def test_stall_survives_a_resumed_split(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.write_batch_page(CODE, DEATHS, [1, 2], 0, page([row(2_000, 1)], 55_000))
        cache.write_batch_page(CODE, DEATHS, [1, 2], 1, page([row(61_000, 2)], 55_000))
        cache.mark_batch_stalled(CODE, DEATHS, [1, 2], 55_000)
        cache.mark_batch_complete(CODE, DEATHS, [1, 2])  # crashed before the split
        client = StubClient()
        out = await fetch_batch(client, cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert client.calls == []
        assert out == {1: False, 2: True}
        assert batch_keys(cache) == []

    async def test_stray_rows_are_dropped_and_logged(self, tmp_path, caplog):
        cache = RawPageCache(tmp_path)
        client = StubClient(
            {("Deaths", "Friendlies", (1, 2)): [page([row(2_000, 1), row(3_000, None), row(4_000, 9), row(61_000, 2)])]}
        )
        with caplog.at_level(logging.WARNING):
            await fetch_batch(client, cache, CODE, [P1, P2], DEATHS, cfg=CFG, max_retries=1)
        assert rows_of(cache, 1) == [2_000] and rows_of(cache, 2) == [61_000]
        assert "dropped 2 rows" in caplog.text


# ===========================================================================
# fetch_batches
# ===========================================================================


def specs_of(*keys: str) -> list[FetchSpec]:
    table = {
        "Deaths": DEATHS,
        "Healing": FetchSpec(data_type="Healing"),
        "Casts-Enemies": FetchSpec(data_type="Casts", hostility="Enemies", filter_expression="ability.id IN (9)"),
    }
    return [table[k] for k in keys]


class TestFetchBatches:
    def test_the_batched_keys_are_the_small_specs(self):
        assert "Healing" not in BATCHED_SPEC_KEYS and "DamageTaken" not in BATCHED_SPEC_KEYS
        assert "Casts" not in BATCHED_SPEC_KEYS and "Casts-Enemies" in BATCHED_SPEC_KEYS

    async def test_batches_the_pulls_that_need_a_spec(self, tmp_path):
        cache = RawPageCache(tmp_path)
        client = StubClient()
        specs = {p.fight_id: specs_of("Deaths") for p in (P1, P2, P3)}
        out = await fetch_batches(client, cache, CODE, [P1, P2, P3], specs, cfg=CFG)
        assert out == {}
        assert [c["fightIDs"] for c in client.calls] == [[1, 2, 3]]
        assert all(cache.is_complete(CODE, f, DEATHS) for f in (1, 2, 3))

    async def test_complete_pull_is_excluded_and_a_lone_pull_is_not_batched(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.mark_complete(CODE, 1, DEATHS)
        client = StubClient()
        specs = {p.fight_id: specs_of("Deaths") for p in (P1, P2)}
        await fetch_batches(client, cache, CODE, [P1, P2], specs, cfg=CFG)
        assert client.calls == []
        assert not cache.is_complete(CODE, 2, DEATHS)

    async def test_pull_with_partial_pages_is_left_to_the_per_pull_fetch(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.write_page(CODE, 1, DEATHS, 0, page([row(2_000, 1)], 5_000))
        client = StubClient()
        specs = {p.fight_id: specs_of("Deaths") for p in (P1, P2, P3)}
        await fetch_batches(client, cache, CODE, [P1, P2, P3], specs, cfg=CFG)
        assert [c["fightIDs"] for c in client.calls] == [[2, 3]]
        assert not cache.is_complete(CODE, 1, DEATHS)

    async def test_non_batchable_specs_are_never_batched(self, tmp_path):
        client = StubClient()
        specs = {p.fight_id: specs_of("Healing") for p in (P1, P2)}
        await fetch_batches(client, RawPageCache(tmp_path), CODE, [P1, P2], specs, cfg=CFG)
        assert client.calls == []

    async def test_enemy_casts_with_different_filters_form_two_streams(self, tmp_path):
        other = FetchSpec(data_type="Casts", hostility="Enemies", filter_expression="ability.id IN (8)")
        mine = specs_of("Casts-Enemies")[0]
        specs = {1: [mine], 2: [mine], 3: [other], 4: [other]}
        pulls = [P1, P2, make_pull(3, 110_000, 150_000), make_pull(4, 160_000, 200_000)]
        client = StubClient()
        await fetch_batches(client, RawPageCache(tmp_path), CODE, pulls, specs, cfg=CFG)
        assert sorted((c["filter"], c["fightIDs"]) for c in client.calls) == [
            ("ability.id IN (8)", [3, 4]),
            ("ability.id IN (9)", [1, 2]),
        ]

    async def test_chunks_of_at_most_the_configured_size(self, tmp_path):
        cache = RawPageCache(tmp_path)
        client = StubClient()
        specs = {p.fight_id: specs_of("Deaths") for p in (P1, P2, P3)}
        cfg = EncounterConfig(wcl_batch_max_fights=2)
        await fetch_batches(client, cache, CODE, [P3, P1, P2], specs, cfg=cfg)
        assert [c["fightIDs"] for c in client.calls] == [[1, 2]]
        assert not cache.is_complete(CODE, 3, DEATHS)

    async def test_truncation_is_reported_per_fight_and_spec(self, tmp_path):
        client = StubClient(
            {("Deaths", "Friendlies", (1, 2)): [page([row(2_000, 1)], 55_000), page([row(61_000, 2)], 55_000)]}
        )
        specs = {p.fight_id: specs_of("Deaths") for p in (P1, P2)}
        out = await fetch_batches(client, RawPageCache(tmp_path), CODE, [P1, P2], specs, cfg=CFG)
        assert out == {2: {"Deaths"}}

    async def test_a_failed_batch_is_logged_and_left_incomplete(self, tmp_path, caplog):
        cache = RawPageCache(tmp_path)
        client = StubClient(fail=lambda v: WarcraftLogsAPIError("boom"))
        specs = {p.fight_id: specs_of("Deaths") for p in (P1, P2)}
        with caplog.at_level(logging.WARNING):
            out = await fetch_batches(client, cache, CODE, [P1, P2], specs, cfg=CFG)
        assert out == {}
        assert "falling back per pull" in caplog.text
        assert not cache.is_complete(CODE, 1, DEATHS) and not cache.is_complete(CODE, 2, DEATHS)
        # The per-pull fetch then picks the specs up.
        ok = StubClient()
        await fetch_pull(ok, cache, CODE, P1, [DEATHS])
        assert cache.is_complete(CODE, 1, DEATHS)


# ===========================================================================
# ingest_report
# ===========================================================================

SYNTH = "SYNTH01"


@pytest.fixture
def online(tmp_path):
    """An online ingest on SYNTH01's report metadata with no cached events; returns kwargs and the pulls."""
    cache = RawPageCache(tmp_path / "raw")
    cache.write_report_meta(SYNTH, RawPageCache(FIXTURES / "wcl").report_meta(SYNTH))
    pulls, _ = parse_report_meta(SYNTH, cache.report_meta(SYNTH))
    kwargs = {
        "cache": cache,
        "store": ParquetEventStore(tmp_path / "events"),
        "defensive_spell_ids": SYNTH_DEFENSIVE_COOLDOWN_IDS,
        "offensive_spell_ids": SYNTH_OFFENSIVE_COOLDOWN_IDS,
        "enemy_cast_ids": SYNTH_ENEMY_CAST_IDS,
    }
    return kwargs, sorted(pulls, key=lambda p: p.fight_id)


class RateLimitStub(StubClient):
    async def rate_limit(self, **_) -> dict:
        return {"pointsSpentThisHour": 0.0}


BATCHED_TYPES = {"Deaths", "Buffs", "Debuffs", "Dispels", "Interrupts", "Threat", "Casts"}  # Casts: enemies only


class TestIngestReport:
    async def test_small_specs_are_batched_and_the_rest_go_per_pull(self, online):
        kwargs, pulls = online
        assert len(pulls) == 2
        ids = [p.fight_id for p in pulls]
        client = RateLimitStub()
        result = await ingest_report(SYNTH, client=client, **kwargs)

        assert result.failed == {} and len(result.pulls) == 2
        multi = [c for c in client.calls if len(c["fightIDs"]) == 2]
        single = [c for c in client.calls if len(c["fightIDs"]) == 1]
        assert all(c["fightIDs"] == ids for c in multi)
        # Eight batched specs (including the offensive Buffs request), once each.
        assert len(multi) == 8
        assert {c["dataType"] for c in multi} == BATCHED_TYPES
        assert sorted((c["dataType"], c["hostility"], c["fightIDs"][0]) for c in single) == sorted(
            (dt, hostility, f)
            for f in ids
            for dt, hostility in [("DamageTaken", "Friendlies"), ("Casts", "Friendlies"), ("Healing", "Friendlies")]
        )
        assert kwargs["store"].truncated(pulls[0].pull_key) == []
        assert batch_keys(kwargs["cache"]) == []

    async def test_batch_truncation_reaches_the_store(self, online):
        kwargs, pulls = online
        first, second = pulls
        stall = (first.end_ms + second.start_ms) // 2
        ids = tuple(p.fight_id for p in pulls)
        client = RateLimitStub(
            {("Deaths", "Friendlies", ids): [page([], stall), page([], stall)]}  # stalls before the second pull
        )
        result = await ingest_report(SYNTH, client=client, **kwargs)
        assert result.failed == {}
        store = kwargs["store"]
        assert store.truncated(first.pull_key) == []
        assert store.truncated(second.pull_key) == ["Deaths"]

    async def test_failed_batches_fall_back_to_per_pull_requests(self, online):
        kwargs, pulls = online
        client = RateLimitStub(fail=lambda v: WarcraftLogsAPIError("batch refused") if len(v["fightIDs"]) > 1 else None)
        result = await ingest_report(SYNTH, client=client, **kwargs)
        assert result.failed == {} and len(result.pulls) == 2
        single = [c for c in client.calls if len(c["fightIDs"]) == 1]
        assert len(single) == 2 * 11  # every spec once per pull: 10 default specs plus offensive Buffs
        assert len(client.calls) == len(single) + 8  # the eight refused batched attempts

    async def test_offline_runs_make_no_batched_requests(self, online):
        kwargs, _ = online
        client = RateLimitStub()
        result = await ingest_report(SYNTH, client=client, offline=True, **kwargs)
        assert client.calls == []
        assert len(result.failed) == 2
