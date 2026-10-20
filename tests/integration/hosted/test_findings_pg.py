"""
tests/integration/hosted/test_findings_pg.py

PostgresFindingsStore against a real database: the shared findings contract plus the hosted-only checks.
"""

from __future__ import annotations

import pytest
from helpers.encounter.findings import make
from helpers.encounter.findings_contract import FindingsStoreContract

from khadbot.db import PostgresFindingsStore, migrate


@pytest.fixture
def store(pool) -> PostgresFindingsStore:
    migrate(pool)
    return PostgresFindingsStore(pool)


class TestPostgresFindingsStore(FindingsStoreContract):
    def test_report_is_stored_as_jsonb(self, store, pool):
        store.put(make("aaaa0000-1"))
        with pool.connection() as conn:
            (kind,) = conn.execute("SELECT jsonb_typeof(report) FROM findings WHERE cache_key = 'k1'").fetchone()
        assert kind == "object"

    def test_flag_persists_across_pools(self, store, pool, database_url, schema_name):
        from khadbot.db import open_pool

        store.put(make("aaaa0000-1", key="k1"))
        assert store.flag("aaaa0000-1") is True
        other = open_pool(database_url, kwargs={"options": f"-c search_path={schema_name}"})
        try:
            assert PostgresFindingsStore(other).get("k1").flagged is True
        finally:
            other.close()
