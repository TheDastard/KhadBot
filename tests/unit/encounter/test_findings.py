"""
tests/unit/encounter/test_findings.py

Findings cache key, plus the SQLite store: the shared contract (tests/helpers/encounter/findings_contract.py),
schema created on first use, and persistence across handles.
"""

from __future__ import annotations

import re

import pytest
from helpers.encounter.findings import make
from helpers.encounter.findings_contract import FindingsStoreContract

from khadbot.encounter.findings import FindingsStore, SqliteFindingsStore, cache_key

BASE = {
    "pull_key": "SYNTH01:1",
    "events_hash": "e" * 64,
    "positions_hash": "q" * 64,
    "encfile_hash": "f" * 12,
    "defensive_cooldowns_hash": "r" * 12,
    "offensive_cooldowns_hash": "o" * 12,
    "utility_cooldowns_hash": "u" * 12,
    "friendly_damage_hash": "d" * 12,
    "prompt_version": "p" * 12,
    "graph_version": "1",
    "model_id": "model-a",
    "depth": "standard",
}


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def test_cache_key_is_16_hex_and_stable():
    assert re.fullmatch(r"[0-9a-f]{16}", cache_key(**BASE))
    assert cache_key(**BASE) == cache_key(**BASE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pull_key", "SYNTH01:2"),
        ("events_hash", "0" * 64),
        ("positions_hash", "0" * 64),
        ("encfile_hash", "0" * 12),
        ("encfile_hash", None),
        ("defensive_cooldowns_hash", "0" * 12),
        ("offensive_cooldowns_hash", "0" * 12),
        ("utility_cooldowns_hash", "0" * 12),
        ("friendly_damage_hash", "0" * 12),
        ("prompt_version", "0" * 12),
        ("graph_version", "2"),
        ("model_id", "model-b"),
        ("depth", "deep"),
    ],
)
def test_each_input_changes_the_key(field, value):
    assert cache_key(**{**BASE, field: value}) != cache_key(**BASE)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestSqliteFindingsStore(FindingsStoreContract):
    @pytest.fixture
    def store(self, tmp_path) -> SqliteFindingsStore:
        return SqliteFindingsStore(tmp_path / "nested" / "khadbot.sqlite")

    def test_creates_schema_and_parent_dir(self, store):
        store.put(make())
        assert store.db_path.exists()
        assert SqliteFindingsStore(store.db_path).get("k1") == make()  # a fresh handle reads the same file

    def test_flag_persists_across_handles(self, store):
        store.put(make("aaaa0000-1", key="k1"))
        store.put(make("bbbb0000-1", key="k2", minutes=1))
        assert store.flag("aaaa0000-1") is True
        reopened = SqliteFindingsStore(store.db_path)
        assert reopened.get("k1").flagged is True
        assert [pf.cache_key for pf in reopened.for_pull("SYNTH01:1")] == ["k2"]


def test_sqlite_store_satisfies_the_protocol(tmp_path):
    store: FindingsStore = SqliteFindingsStore(tmp_path / "f.sqlite")
    assert store.get("nope") is None
