"""
tests/unit/encounter/test_perf.py

Performance budgets (plan 16.5), run on real recorded WCL fixtures:

- Ingest: parse + normalize + store a full night in under 30 s, excluding network.
- Annotation: annotate + digest one pull in under 2 s; re-annotate a whole night after an encounter
  file edit in under 30 s. These use the authored file in config/encounters.

Skipped unless run with ``-m perf`` AND the real fixtures exist
(tests/fixtures/wcl/<code>/ recorded by scripts/record_wcl_fixture.py).
"""

from __future__ import annotations

import time

import pytest
from helpers.synth import FIXTURES

from khadbot.encounter.annotate import annotate_pull
from khadbot.encounter.config_files import load_encounter, load_registry
from khadbot.encounter.digest import build_digest
from khadbot.encounter.fetch import RawPageCache
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.store import ParquetEventStore

REAL_REPORTS = sorted(p.name for p in (FIXTURES / "wcl").iterdir() if p.is_dir() and p.name != "SYNTH01")


@pytest.mark.perf
@pytest.mark.skipif(not REAL_REPORTS, reason="no real WCL fixtures recorded yet")
@pytest.mark.parametrize("code", REAL_REPORTS)
async def test_ingest_budget(code, tmp_path):
    t0 = time.perf_counter()
    result = await ingest_report(
        code, store=ParquetEventStore(tmp_path / "events"), cache=RawPageCache(FIXTURES / "wcl"), offline=True
    )
    elapsed = time.perf_counter() - t0
    assert not result.failed
    assert elapsed < 30, f"{code} took {elapsed:.1f}s"


async def _ingested(code, tmp_path) -> ParquetEventStore:
    store = ParquetEventStore(tmp_path / "events")
    result = await ingest_report(code, store=store, cache=RawPageCache(FIXTURES / "wcl"), offline=True)
    assert not result.failed
    return store


@pytest.mark.perf
@pytest.mark.skipif(not REAL_REPORTS, reason="no real WCL fixtures recorded yet")
@pytest.mark.parametrize("code", REAL_REPORTS)
async def test_digest_budget_per_pull(code, tmp_path):
    store = await _ingested(code, tmp_path)
    registry = load_registry()
    for pull in store.pulls(code):
        t0 = time.perf_counter()
        build_digest(pull.pull_key, store, registry=registry)
        elapsed = time.perf_counter() - t0
        assert elapsed < 2, f"{pull.pull_key} annotate + digest took {elapsed:.2f}s"


@pytest.mark.perf
@pytest.mark.skipif(not REAL_REPORTS, reason="no real WCL fixtures recorded yet")
@pytest.mark.parametrize("code", REAL_REPORTS)
async def test_reannotate_night_budget(code, tmp_path):
    store = await _ingested(code, tmp_path)
    t0 = time.perf_counter()
    for pull in store.pulls(code):
        annotate_pull(pull.pull_key, store, load_encounter(pull.encounter_id))
    elapsed = time.perf_counter() - t0
    assert elapsed < 30, f"re-annotating {code} took {elapsed:.1f}s"
