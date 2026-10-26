"""
tests/unit/encounter/ingest/test_fetch_report.py

The ingest job's half of ingest (``fetch_report``) on SYNTH01's report metadata with no cached events: raw pages and
one fetch manifest per pull, never the event store; several pulls fetched at once; one pull's failure leaving the
others ready; cancellation stopping at a page boundary; truncation flags kept in the cache and the manifest. WCL is a
stub client that answers every events request with an empty page, after a short delay.
"""

from __future__ import annotations

import asyncio

import pytest
from helpers.encounter.synth import (
    FIXTURES,
    SYNTH_DEFENSIVE_COOLDOWN_IDS,
    SYNTH_ENEMY_CAST_IDS,
    SYNTH_OFFENSIVE_COOLDOWN_IDS,
)

from khadbot.config import EncounterConfig
from khadbot.encounter.ingest import fetch_report
from khadbot.encounter.ingest.fetch import RawPageCache, fetch_pull
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.encounter.models import FetchSpec
from khadbot.encounter.store import ParquetEventStore
from khadbot.jobs.failures import JobCancelled
from khadbot.jobs.reporting import CancelFlag, CancellingRawCache
from khadbot.wcl.client import WarcraftLogsAPIError

SYNTH = "SYNTH01"


class SlowStub:
    """Answers each events request with an empty last page after ``delay`` s; records when each request ran."""

    def __init__(self, delay: float = 0.02, fail=None, on_request=None) -> None:
        self.delay = delay
        self.fail = fail
        self.on_request = on_request
        self.calls: list[dict] = []
        self.spans: list[tuple[tuple[int, ...], float, float]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def query(self, gql: str, variables: dict | None = None, **_):
        assert variables is not None
        self.calls.append(variables)
        if self.on_request:
            self.on_request(variables)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            await asyncio.sleep(self.delay)
            if self.fail and (exc := self.fail(variables)):
                raise exc
        finally:
            self.in_flight -= 1
            self.spans.append((tuple(variables["fightIDs"]), start, loop.time()))
        return {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": None}}}}

    async def rate_limit(self, **_) -> dict:
        return {"pointsSpentThisHour": 0.0}

    def overlapped(self, a: int, b: int) -> bool:
        """True if a single-pull request for fight ``a`` ran while one for fight ``b`` did."""
        mine = [(s, e) for f, s, e in self.spans if f == (a,)]
        theirs = [(s, e) for f, s, e in self.spans if f == (b,)]
        return any(s1 < e2 and s2 < e1 for s1, e1 in mine for s2, e2 in theirs)


@pytest.fixture
def online(tmp_path):
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


def files(root) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class TestRawPagesAndManifests:
    async def test_writes_pages_and_a_manifest_per_pull_and_no_events(self, online, tmp_path):
        kwargs, pulls = online
        out = await fetch_report(SYNTH, client=SlowStub(0), **kwargs)
        assert out.failed == {} and [p.fight_id for p in out.pulls] == [1, 2]
        assert sorted(out.written) == ["SYNTH01:1", "SYNTH01:2"] and out.cached == []
        assert not (tmp_path / "events").exists()
        cache = kwargs["cache"]
        for pull in pulls:
            manifest = cache.manifest(SYNTH, pull.fight_id)
            assert manifest == out.manifests[pull.fight_id]
            assert len(manifest.specs) == 11 and manifest.truncated() == []

    async def test_a_repeat_makes_no_request_and_writes_nothing(self, online, tmp_path):
        kwargs, _ = online
        await fetch_report(SYNTH, client=SlowStub(0), **kwargs)
        before = files(tmp_path / "raw")
        client = SlowStub(0)
        again = await fetch_report(SYNTH, client=client, **kwargs)
        assert client.calls == []
        assert again.written == [] and sorted(again.cached) == ["SYNTH01:1", "SYNTH01:2"]
        assert files(tmp_path / "raw") == before

    async def test_without_write_manifests_nothing_but_pages_is_written(self, online):
        kwargs, pulls = online
        out = await fetch_report(SYNTH, client=SlowStub(0), write_manifests=False, **kwargs)
        assert len(out.manifests) == 2
        assert all(kwargs["cache"].manifest(SYNTH, p.fight_id) is None for p in pulls)

    async def test_a_changed_filter_gives_a_new_manifest(self, online):
        kwargs, pulls = online
        await fetch_report(SYNTH, client=SlowStub(0), **kwargs)
        changed = {**kwargs, "defensive_spell_ids": [1]}
        client = SlowStub(0)
        out = await fetch_report(SYNTH, client=client, **changed)
        assert {c["dataType"] for c in client.calls} == {"Buffs"}  # only the Buffs spec is refetched
        assert sorted(out.written) == ["SYNTH01:1", "SYNTH01:2"]
        buffs = next(s for s in kwargs["cache"].manifest(SYNTH, 1).specs if s.key == "Buffs")
        assert buffs.filter_expression == "ability.id IN (1)"


class TestTruncation:
    async def test_truncation_is_kept_in_the_cache_and_reaches_the_manifest(self, online):
        kwargs, pulls = online
        cache, pull = kwargs["cache"], pulls[0]
        spec = FetchSpec(data_type="Healing")

        class Stalls(SlowStub):
            async def query(self, gql, variables=None, **kw):
                await super().query(gql, variables, **kw)
                start = variables["startTime"]
                return {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": start}}}}

        assert await fetch_pull(Stalls(0), cache, SYNTH, pull, [spec]) == {"Healing": True}
        assert cache.completion(SYNTH, pull.fight_id, spec) is True
        # A later fetch makes no request and still reports the truncation.
        client = SlowStub(0)
        assert await fetch_pull(client, cache, SYNTH, pull, [spec]) == {"Healing": True}
        assert client.calls == []
        out = await fetch_report(SYNTH, [pull.fight_id], client=SlowStub(0), **kwargs)
        assert out.manifests[pull.fight_id].truncated() == ["Healing"]


class TestConcurrency:
    async def test_pulls_are_fetched_at_once_within_the_request_limit(self, online):
        kwargs, _ = online
        client = SlowStub(0.02)
        out = await fetch_report(SYNTH, client=client, pull_concurrency=3, **kwargs)
        assert out.failed == {} and len(out.pulls) == 2
        assert client.overlapped(1, 2)
        assert client.max_in_flight <= EncounterConfig().wcl_max_concurrency

    async def test_one_pull_at_a_time_never_overlaps(self, online):
        kwargs, _ = online
        client = SlowStub(0.02)
        await fetch_report(SYNTH, client=client, pull_concurrency=1, **kwargs)
        assert not client.overlapped(1, 2)

    async def test_one_failed_pull_leaves_the_others_ready(self, online):
        kwargs, _ = online

        def fail(v):
            if v["fightIDs"] == [1] and v["dataType"] == "Healing":
                return WarcraftLogsAPIError("pull 1 refused")
            return None

        out = await fetch_report(SYNTH, client=SlowStub(0.01, fail=fail), **kwargs)
        assert out.failed == {"SYNTH01:1": "pull 1 refused"}
        assert [p.pull_key for p in out.pulls] == ["SYNTH01:2"] and out.written == ["SYNTH01:2"]
        cache = kwargs["cache"]
        assert cache.manifest(SYNTH, 1) is None and cache.manifest(SYNTH, 2) is not None

    @pytest.mark.parametrize("pull_concurrency", [1, 3])
    async def test_cancellation_stops_at_a_page_boundary(self, online, pull_concurrency):
        kwargs, _ = online
        flag = CancelFlag()

        def cancel_on_first_single_pull_request(v):
            if len(v["fightIDs"]) == 1:
                flag.requested = True

        client = SlowStub(0.01, on_request=cancel_on_first_single_pull_request)
        cache = CancellingRawCache(kwargs["cache"], flag)
        with pytest.raises(JobCancelled):
            await fetch_report(SYNTH, client=client, pull_concurrency=pull_concurrency, **{**kwargs, "cache": cache})
        single = [c for c in client.calls if len(c["fightIDs"]) == 1]
        # Each running spec stops after the page it was writing: no spec asks for a second page, and with one pull
        # at a time the second pull never starts.
        assert len(single) <= (3 if pull_concurrency == 1 else 6)
        if pull_concurrency == 1:
            assert {c["fightIDs"][0] for c in single} == {1}
        assert cache.manifest(SYNTH, 1) is None and cache.manifest(SYNTH, 2) is None
        # The pages written stay, and the next run resumes from them without asking for them again.
        resumed = SlowStub(0)
        out = await fetch_report(SYNTH, client=resumed, **kwargs)
        assert out.failed == {} and len(out.pulls) == 2
        done = {(c["dataType"], c["fightIDs"][0]) for c in single}
        assert not done & {(c["dataType"], c["fightIDs"][0]) for c in resumed.calls if len(c["fightIDs"]) == 1}
