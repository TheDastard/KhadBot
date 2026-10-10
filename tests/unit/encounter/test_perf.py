"""
tests/unit/encounter/test_perf.py

Ingest performance budget: parse + normalize + store the full wipe-heavy night in under 30 s,
excluding network. Skipped unless run with ``-m perf`` AND real recorded fixtures exist
(tests/fixtures/wcl/<code>/ recorded by scripts/record_wcl_fixture.py).
"""

from __future__ import annotations

import time

import pytest
from helpers.synth import FIXTURES

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
