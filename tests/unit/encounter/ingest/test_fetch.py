"""
tests/unit/encounter/ingest/test_fetch.py

RawPageCache, build_filter and the paginated fetcher. WCL is replaced by a stub client
(no network, no respx); asyncio.sleep is patched so backoff tests run instantly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import orjson
import pytest

from khadbot.encounter.ingest.fetch import (
    DEFAULT_SPECS,
    MissingFixtureError,
    RawPageCache,
    build_filter,
    fetch_pull,
    filter_hash,
    specs_for,
)
from khadbot.encounter.models import FetchSpec, Pull
from khadbot.storage import LocalBlobStore
from khadbot.wcl.client import WarcraftLogsNetworkError, WarcraftLogsRateLimitError

CODE = "ABC123"
EMPTY_PAGE = {"data": [], "nextPageTimestamp": None}


def make_pull(start: int = 1_000, end: int = 100_000) -> Pull:
    return Pull(
        report_code=CODE,
        fight_id=1,
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


class StubClient:
    """Serves a fixed page chain per data type; records every request's variables."""

    def __init__(self, chains: dict[str, list[dict]] | None = None, errors: list[Exception] | None = None) -> None:
        # chains: dir_name -> pages; the i-th request for that data type gets page i (last page repeats)
        self.chains = chains or {}
        self.errors = list(errors or [])
        self.calls: list[dict] = []

    async def query(self, gql: str, variables: dict | None = None, **_):
        assert variables is not None
        self.calls.append(variables)
        if self.errors:
            raise self.errors.pop(0)
        key = variables["dataType"] + ("-Enemies" if variables["hostility"] == "Enemies" else "")
        served = sum(
            1 for c in self.calls if (c["dataType"], c["hostility"]) == (variables["dataType"], variables["hostility"])
        )
        pages = self.chains.get(key) or [EMPTY_PAGE]
        page = pages[min(served, len(pages)) - 1]
        return {"reportData": {"report": {"events": page}}}


def ev(t: int) -> dict:
    return {"timestamp": t, "type": "damage", "sourceID": 1, "targetID": 1}


# ===========================================================================
# build_filter / specs
# ===========================================================================


class TestBuildFilter:
    def test_buffs_use_given_ids_sorted_and_deduped(self):
        assert build_filter("Buffs", buff_spell_ids=[3, 1, 3, 2]) == "ability.id IN (1,2,3)"

    def test_buffs_without_ids_unfiltered(self):
        assert build_filter("Buffs") is None
        assert build_filter("Buffs", buff_spell_ids=[]) is None

    def test_enemy_casts_use_encounter_ids(self):
        assert build_filter("Casts", "Enemies", encounter_ability_ids=[20, 10]) == "ability.id IN (10,20)"

    def test_friendly_casts_are_unfiltered_even_with_ids(self):
        assert build_filter("Casts", "Friendlies", encounter_ability_ids=[10]) is None

    @pytest.mark.parametrize("dt", ["Deaths", "DamageTaken", "Debuffs", "Interrupts", "Dispels", "Healing"])
    def test_other_types_unfiltered(self, dt):
        assert build_filter(dt, buff_spell_ids=[1], encounter_ability_ids=[2]) is None

    def test_specs_for_applies_policy(self):
        specs = {s.dir_name: s.filter_expression for s in specs_for([5], [9])}
        assert specs["Buffs"] == "ability.id IN (5)"
        assert specs["Casts-Enemies"] == "ability.id IN (9)"
        assert specs["Casts"] is None
        assert len(specs) == len(DEFAULT_SPECS)

    def test_offensive_ids_add_a_second_buffs_request(self):
        specs = specs_for([5], [9], [7, 6])
        assert len(specs) == len(DEFAULT_SPECS) + 1
        by_key = {s.key: s for s in specs}
        assert by_key["Buffs"].filter_expression == "ability.id IN (5)"
        offensive = by_key["Buffs:offensive"]
        assert (offensive.data_type, offensive.dir_name, offensive.filter_expression) == (
            "Buffs",
            "Buffs",
            "ability.id IN (6,7)",
        )

    @pytest.mark.parametrize("ids", [None, []])
    def test_no_offensive_ids_means_no_request(self, ids):
        # An omitted filter would fetch every buff.
        assert [s.key for s in specs_for([5], [9], ids)] == [s.key for s in specs_for([5], [9])]

    def test_both_buffs_requests_cache_apart(self, tmp_path):
        cache = RawPageCache(tmp_path)
        defensive, offensive = (s for s in specs_for([5], None, [7]) if s.data_type == "Buffs")
        cache.write_page(CODE, 1, defensive, 0, {"data": [1], "nextPageTimestamp": None})
        cache.write_page(CODE, 1, offensive, 0, {"data": [2], "nextPageTimestamp": None})
        assert [p["data"] for p in cache.pages(CODE, 1, defensive)] == [[1]]
        assert [p["data"] for p in cache.pages(CODE, 1, offensive)] == [[2]]


# ===========================================================================
# RawPageCache
# ===========================================================================


class TestRawPageCache:
    def test_page_round_trip_and_ordering(self, tmp_path):
        cache = RawPageCache(tmp_path)
        spec = FetchSpec(data_type="Deaths")
        cache.write_page(CODE, 1, spec, 1, {"data": [2], "nextPageTimestamp": None})
        cache.write_page(CODE, 1, spec, 0, {"data": [1], "nextPageTimestamp": 5})
        assert [p["data"] for p in cache.pages(CODE, 1, spec)] == [[1], [2]]
        assert not cache.is_complete(CODE, 1, spec)
        cache.mark_complete(CODE, 1, spec)
        assert cache.is_complete(CODE, 1, spec)

    def test_report_meta_round_trip(self, tmp_path):
        cache = RawPageCache(tmp_path)
        assert cache.report_meta(CODE) is None
        cache.write_report_meta(CODE, {"title": "x"})
        assert cache.report_meta(CODE) == {"title": "x"}

    def test_filter_change_uses_a_different_directory(self, tmp_path):
        cache = RawPageCache(tmp_path)
        a = FetchSpec(data_type="Buffs", filter_expression="ability.id IN (1)")
        b = FetchSpec(data_type="Buffs", filter_expression="ability.id IN (1,2)")
        cache.write_page(CODE, 1, a, 0, EMPTY_PAGE)
        cache.mark_complete(CODE, 1, a)
        assert cache.is_complete(CODE, 1, a)
        assert not cache.is_complete(CODE, 1, b)
        assert cache.pages(CODE, 1, b) == []

    def test_enemy_and_friendly_casts_do_not_collide(self, tmp_path):
        cache = RawPageCache(tmp_path)
        friendly, enemy = FetchSpec(data_type="Casts"), FetchSpec(data_type="Casts", hostility="Enemies")
        cache.write_page(CODE, 1, friendly, 0, {"data": [1], "nextPageTimestamp": None})
        assert cache.pages(CODE, 1, enemy) == []

    def test_no_temp_files_left_behind(self, tmp_path):
        cache = RawPageCache(tmp_path)
        cache.write_page(CODE, 1, FetchSpec(data_type="Deaths"), 0, EMPTY_PAGE)
        assert not list(tmp_path.rglob("*.tmp"))

    def test_blob_store_keys_mirror_the_data_directory(self, tmp_path):
        blobs = LocalBlobStore(tmp_path)
        cache = RawPageCache(blobs)
        spec = FetchSpec(data_type="Deaths")
        page = {"data": [{"timestamp": 1}], "nextPageTimestamp": None}
        cache.write_report_meta(CODE, {"title": "x"})
        cache.write_page(CODE, 1, spec, 0, page)
        cache.mark_complete(CODE, 1, spec)
        assert blobs.list() == [
            f"raw/{CODE}/1/Deaths/none/_complete",
            f"raw/{CODE}/1/Deaths/none/page_000.json",
            f"raw/{CODE}/report.json",
        ]
        # The bytes are orjson's, as the path-based cache wrote them.
        assert (tmp_path / "raw" / CODE / "1" / "Deaths" / "none" / "page_000.json").read_bytes() == orjson.dumps(page)
        assert RawPageCache(tmp_path / "raw").pages(CODE, 1, spec) == [page]

    def test_pages_are_only_the_page_objects_directly_in_the_spec_directory(self, tmp_path):
        blobs = LocalBlobStore(tmp_path)
        cache = RawPageCache(blobs)
        spec = FetchSpec(data_type="Deaths")
        cache.write_page(CODE, 1, spec, 0, EMPTY_PAGE)
        base = f"raw/{CODE}/1/Deaths/none"
        blobs.put(f"{base}/notes.json", b"{}")
        blobs.put(f"{base}/nested/page_001.json", b"{}")
        (tmp_path / "raw" / CODE / "1" / "Deaths" / "none" / "page_001.json.tmp").write_bytes(b"partial")
        assert cache.pages(CODE, 1, spec) == [EMPTY_PAGE]

    def test_filter_hash(self):
        assert filter_hash(None) == "none"
        assert filter_hash("") == "none"
        assert len(filter_hash("ability.id IN (1)")) == 12


# ===========================================================================
# fetch_pull
# ===========================================================================


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("khadbot.encounter.ingest.fetch.asyncio.sleep", sleep)
    return sleep


class TestFetchPull:
    async def test_follows_next_page_timestamp_until_null(self, tmp_path):
        spec = FetchSpec(data_type="Deaths")
        client = StubClient(
            {
                "Deaths": [
                    {"data": [ev(1_100)], "nextPageTimestamp": 2_000},
                    {"data": [ev(2_100)], "nextPageTimestamp": 3_000},
                    {"data": [ev(3_100)], "nextPageTimestamp": None},
                ]
            }
        )
        cache = RawPageCache(tmp_path)
        out = await fetch_pull(client, cache, CODE, make_pull(), [spec])
        assert out == {"Deaths": False}
        assert [c["startTime"] for c in client.calls] == [1_000, 2_000, 3_000]
        assert len(cache.pages(CODE, 1, spec)) == 3
        assert cache.is_complete(CODE, 1, spec)

    async def test_complete_spec_is_not_refetched(self, tmp_path):
        spec = FetchSpec(data_type="Deaths")
        client = StubClient({"Deaths": [{"data": [ev(1_100)], "nextPageTimestamp": None}]})
        cache = RawPageCache(tmp_path)
        await fetch_pull(client, cache, CODE, make_pull(), [spec])
        await fetch_pull(client, cache, CODE, make_pull(), [spec])
        assert len(client.calls) == 1

    async def test_resumes_after_crash_mid_pagination(self, tmp_path):
        spec = FetchSpec(data_type="Deaths")
        cache = RawPageCache(tmp_path)
        cache.write_page(CODE, 1, spec, 0, {"data": [ev(1_100)], "nextPageTimestamp": 2_000})  # then "crash"
        client = StubClient({"Deaths": [{"data": [ev(2_100)], "nextPageTimestamp": None}]})
        await fetch_pull(client, cache, CODE, make_pull(), [spec])
        assert [c["startTime"] for c in client.calls] == [2_000]
        assert len(cache.pages(CODE, 1, spec)) == 2
        assert cache.is_complete(CODE, 1, spec)

    async def test_final_page_without_marker_is_completed_without_a_request(self, tmp_path):
        spec = FetchSpec(data_type="Deaths")
        cache = RawPageCache(tmp_path)
        cache.write_page(CODE, 1, spec, 0, EMPTY_PAGE)
        client = StubClient()
        await fetch_pull(client, cache, CODE, make_pull(), [spec])
        assert client.calls == []
        assert cache.is_complete(CODE, 1, spec)

    async def test_filter_change_refetches_only_that_data_type(self, tmp_path):
        cache = RawPageCache(tmp_path)
        deaths = FetchSpec(data_type="Deaths")
        buffs_v1 = FetchSpec(data_type="Buffs", filter_expression="ability.id IN (1)")
        buffs_v2 = FetchSpec(data_type="Buffs", filter_expression="ability.id IN (1,2)")
        await fetch_pull(StubClient(), cache, CODE, make_pull(), [deaths, buffs_v1])
        client = StubClient()
        await fetch_pull(client, cache, CODE, make_pull(), [deaths, buffs_v2])
        assert [c["dataType"] for c in client.calls] == ["Buffs"]

    async def test_non_advancing_timestamp_is_flagged_truncated(self, tmp_path):
        spec = FetchSpec(data_type="Deaths")
        client = StubClient({"Deaths": [{"data": [ev(1_100)], "nextPageTimestamp": 1_000}]})
        out = await fetch_pull(client, RawPageCache(tmp_path), CODE, make_pull(), [spec])
        assert out == {"Deaths": True}
        assert len(client.calls) == 1

    async def test_sends_filter_and_hostility(self, tmp_path):
        spec = FetchSpec(data_type="Casts", hostility="Enemies", filter_expression="ability.id IN (9)")
        client = StubClient()
        await fetch_pull(client, RawPageCache(tmp_path), CODE, make_pull(), [spec])
        call = client.calls[0]
        assert call["hostility"] == "Enemies"
        assert call["filter"] == "ability.id IN (9)"
        assert call["fightIDs"] == [1]
        assert call["endTime"] == 100_000

    async def test_rate_limit_retries_then_succeeds(self, tmp_path, _no_sleep):
        spec = FetchSpec(data_type="Deaths")
        client = StubClient(
            errors=[WarcraftLogsRateLimitError("slow", retry_after_s=7.0), WarcraftLogsRateLimitError("slow")]
        )
        await fetch_pull(client, RawPageCache(tmp_path), CODE, make_pull(), [spec], max_retries=3)
        assert len(client.calls) == 3
        assert [c.args[0] for c in _no_sleep.await_args_list] == [7.0, 2.0]  # Retry-After, then 2**attempt

    async def test_rate_limit_gives_up_after_max_retries(self, tmp_path):
        spec = FetchSpec(data_type="Deaths")
        client = StubClient(errors=[WarcraftLogsRateLimitError("slow")] * 50)
        with pytest.raises(WarcraftLogsRateLimitError):
            await fetch_pull(client, RawPageCache(tmp_path), CODE, make_pull(), [spec], max_retries=2)
        assert len(client.calls) == 3  # 1 try + 2 retries, no storm

    async def test_network_error_retries_then_succeeds(self, tmp_path, _no_sleep):
        spec = FetchSpec(data_type="Deaths")
        client = StubClient(errors=[WarcraftLogsNetworkError("timed out")])
        await fetch_pull(client, RawPageCache(tmp_path), CODE, make_pull(), [spec], max_retries=3)
        assert len(client.calls) == 2
        assert [c.args[0] for c in _no_sleep.await_args_list] == [1.0]

    async def test_network_error_gives_up_after_max_retries(self, tmp_path):
        spec = FetchSpec(data_type="Deaths")
        client = StubClient(errors=[WarcraftLogsNetworkError("timed out")] * 50)
        with pytest.raises(WarcraftLogsNetworkError):
            await fetch_pull(client, RawPageCache(tmp_path), CODE, make_pull(), [spec], max_retries=2)
        assert len(client.calls) == 3

    async def test_one_failing_spec_keeps_the_others_cached(self, tmp_path):
        cache = RawPageCache(tmp_path)
        ok, bad = FetchSpec(data_type="Deaths"), FetchSpec(data_type="Healing")

        class Flaky(StubClient):
            async def query(self, gql, variables=None, **kw):
                if variables["dataType"] == "Healing":
                    raise RuntimeError("boom")
                return await super().query(gql, variables, **kw)

        with pytest.raises(RuntimeError):
            await fetch_pull(Flaky(), cache, CODE, make_pull(), [ok, bad])
        assert cache.is_complete(CODE, 1, ok)

    async def test_offline_raises_for_missing_data(self, tmp_path):
        with pytest.raises(MissingFixtureError):
            await fetch_pull(
                None, RawPageCache(tmp_path), CODE, make_pull(), [FetchSpec(data_type="Deaths")], offline=True
            )

    async def test_offline_uses_cached_data(self, tmp_path):
        spec = FetchSpec(data_type="Deaths")
        cache = RawPageCache(tmp_path)
        cache.write_page(CODE, 1, spec, 0, EMPTY_PAGE)
        cache.mark_complete(CODE, 1, spec)
        assert await fetch_pull(None, cache, CODE, make_pull(), [spec], offline=True) == {"Deaths": False}

    async def test_progress_callback(self, tmp_path):
        seen = []
        await fetch_pull(
            StubClient(),
            RawPageCache(tmp_path),
            CODE,
            make_pull(),
            [FetchSpec(data_type="Deaths")],
            progress=lambda k, s, p: seen.append((k, s, p)),
        )
        assert seen == [(f"{CODE}:1", "fetched Deaths", 1.0)]
