"""
tests/unit/encounter/test_perf.py

Performance budgets, run on real recorded WCL fixtures:

- Ingest: parse + normalize + store a full night in under 30 s, excluding network. The fixtures hold only
  the recorded pulls of each night, so this checks those pulls against the night budget.
- Annotation: annotate + digest one pull in under 2 s; re-annotate a whole night after an encounter
  file edit in under 30 s. These use the authored file in config/encounters.
- Query tools: p95 under 200 ms per call on one analysed pull (the longest in each report).

Skipped unless run with ``-m perf`` AND the real fixtures exist
(tests/fixtures/encounter/wcl/<code>/ recorded by scripts/data/record_wcl_fixture.py).
"""

from __future__ import annotations

import statistics
import time

import pytest
from helpers.encounter.real_fixtures import REAL_REPORTS, ingest_fixture

from khadbot.encounter.analyst import query_tools as qt
from khadbot.encounter.analyst.query_tools import AnalystScope
from khadbot.encounter.annotate import annotate_pull
from khadbot.encounter.config_files import load_cooldowns, load_encounter_view
from khadbot.encounter.digest import build_digest
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.store import ParquetEventStore


@pytest.mark.perf
@pytest.mark.skipif(not REAL_REPORTS, reason="no real WCL fixtures recorded yet")
@pytest.mark.parametrize("code", REAL_REPORTS)
async def test_ingest_budget(code, tmp_path):
    t0 = time.perf_counter()
    result = await ingest_fixture(code, ParquetEventStore(tmp_path / "events"))
    elapsed = time.perf_counter() - t0
    assert not result.failed
    assert result.pulls
    assert elapsed < 30, f"{code} took {elapsed:.1f}s"


async def _ingested(code, tmp_path) -> ParquetEventStore:
    store = ParquetEventStore(tmp_path / "events")
    result = await ingest_fixture(code, store)
    assert not result.failed
    assert result.pulls
    return store


@pytest.mark.perf
@pytest.mark.skipif(not REAL_REPORTS, reason="no real WCL fixtures recorded yet")
@pytest.mark.parametrize("code", REAL_REPORTS)
async def test_digest_budget_per_pull(code, tmp_path):
    store = await _ingested(code, tmp_path)
    registry = load_cooldowns("defensive")
    for pull in store.pulls(code):
        t0 = time.perf_counter()
        build_digest(pull.pull_key, store, defensive=registry)
        elapsed = time.perf_counter() - t0
        assert elapsed < 2, f"{pull.pull_key} annotate + digest took {elapsed:.2f}s"


@pytest.mark.perf
@pytest.mark.skipif(not REAL_REPORTS, reason="no real WCL fixtures recorded yet")
@pytest.mark.parametrize("code", REAL_REPORTS)
async def test_reannotate_night_budget(code, tmp_path):
    store = await _ingested(code, tmp_path)
    t0 = time.perf_counter()
    for pull in store.pulls(code):
        annotate_pull(pull.pull_key, store, load_encounter_view(pull.encounter_id, pull.difficulty))
    elapsed = time.perf_counter() - t0
    assert elapsed < 30, f"re-annotating {code} took {elapsed:.1f}s"


@pytest.mark.perf
@pytest.mark.skipif(not REAL_REPORTS, reason="no real WCL fixtures recorded yet")
@pytest.mark.parametrize("code", REAL_REPORTS)
async def test_query_tool_p95_budget(code, tmp_path):
    store = await _ingested(code, tmp_path)
    pull = max(store.pulls(code), key=lambda p: p.duration_ms)
    data = EncounterData(store)
    scope = AnalystScope(data, (pull.pull_key,))
    records = data.records(pull.pull_key)  # warm: the budget is for a query on an analysed pull

    calls = [
        lambda: qt.list_pulls_result(scope),
        lambda: qt.get_digest_result(scope, pull.pull_key),
        lambda: qt.query_deaths_result(scope, pull.pull_key, include_after_cutoff=True),
        lambda: qt.cooldown_timeline_result(scope, pull.pull_key),
        lambda: qt.compare_pulls_result(scope, "deaths"),
    ]
    calls += [lambda m=m: qt.query_mechanic_result(scope, pull.pull_key, m.mechanic_id) for m in records.mechanics]
    calls += [
        lambda t=t: qt.query_events_result(scope, pull.pull_key, t, t + qt.MAX_EVENT_WINDOW_MS)
        for t in range(0, pull.duration_ms, qt.MAX_EVENT_WINDOW_MS)
    ]
    timings = []
    for _ in range(5):
        for call in calls:
            t0 = time.perf_counter()
            assert call().ok
            timings.append(time.perf_counter() - t0)
    p95 = statistics.quantiles(timings, n=20)[-1]
    assert p95 < 0.2, f"{pull.pull_key} query tool p95 {p95 * 1000:.0f} ms"
