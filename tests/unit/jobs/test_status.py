"""
tests/unit/jobs/test_status.py

The ``jobs`` table rules on the in-memory backend (the contract in tests/helpers/jobs/store_contract.py), plus
``covers``. The Postgres backend runs the same contract in tests/integration/hosted/test_jobs_pg.py.
"""

from __future__ import annotations

import pytest
from helpers.jobs.store_contract import (  # noqa: F401 - collected here
    TestCancel,
    TestChildren,
    TestClaimIngest,
    TestEvents,
    TestLifecycle,
)

from khadbot.jobs import IngestReport
from khadbot.jobs.status import JobStore, MemoryJobBackend, covers


@pytest.fixture
def store() -> JobStore:
    return JobStore(MemoryJobBackend())


class TestCovers:
    def test_all_fights_cover_a_subset(self):
        assert covers(IngestReport(code="A"), IngestReport(code="A", fights=[2]))

    def test_a_subset_does_not_cover_all_fights(self):
        assert not covers(IngestReport(code="A", fights=[2]), IngestReport(code="A"))
        assert not covers(IngestReport(code="A", fights=[2]), IngestReport(code="A", fights=[2, 3]))

    def test_offline_does_not_cover_online_but_online_covers_offline(self):
        assert not covers(IngestReport(code="A", offline=True), IngestReport(code="A"))
        assert covers(IngestReport(code="A"), IngestReport(code="A", offline=True))

    def test_a_metadata_refresh_is_its_own_job(self):
        assert not covers(IngestReport(code="A"), IngestReport(code="A", refresh_meta=True))
