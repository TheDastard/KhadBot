"""
tests/unit/encounter/test_findings.py

FindingsStore: round trip, schema created on first use, each cache-key input changes the key,
flagging persists and hides findings from ``for_pull``.
"""

from __future__ import annotations

import re

import pytest
from helpers.encounter.findings import make

from khadbot.encounter.findings import FindingsStore, cache_key

BASE = {
    "pull_key": "SYNTH01:1",
    "events_hash": "e" * 64,
    "positions_hash": "q" * 64,
    "encfile_hash": "f" * 12,
    "defensive_cooldowns_hash": "r" * 12,
    "friendly_damage_hash": "d" * 12,
    "prompt_version": "p" * 12,
    "graph_version": "1",
    "model_id": "model-a",
    "depth": "standard",
}


@pytest.fixture
def store(tmp_path) -> FindingsStore:
    return FindingsStore(tmp_path / "nested" / "khadbot.sqlite")


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


def test_missing_key_is_none(store):
    assert store.get("nope") is None
    assert store.for_pull("SYNTH01:1") == []


def test_round_trip_creates_schema_and_parent_dir(store):
    pf = make()
    store.put(pf)
    assert store.db_path.exists()
    assert store.get("k1") == pf
    assert FindingsStore(store.db_path).get("k1") == pf  # a fresh handle reads the same file


def test_put_replaces_same_key(store):
    store.put(make("aaaa0000-1"))
    store.put(make("bbbb0000-1"))
    assert [f.id for f in store.get("k1").findings] == ["bbbb0000-1"]
    assert len(store.for_pull("SYNTH01:1")) == 1


def test_for_pull_filters_by_pull_and_orders_newest_first(store):
    store.put(make(key="old", minutes=0))
    store.put(make(key="new", minutes=5))
    store.put(make(pull="SYNTH01:2", key="other"))
    assert [pf.cache_key for pf in store.for_pull("SYNTH01:1")] == ["new", "old"]


def test_flag_persists_and_hides_from_for_pull(store):
    store.put(make("aaaa0000-1", key="k1"))
    store.put(make("bbbb0000-1", key="k2", minutes=1))
    assert store.flag("aaaa0000-1") is True

    reopened = FindingsStore(store.db_path)
    assert reopened.get("k1").flagged is True
    assert reopened.get("k2").flagged is False
    assert [pf.cache_key for pf in reopened.for_pull("SYNTH01:1")] == ["k2"]
    assert {pf.cache_key for pf in reopened.for_pull("SYNTH01:1", include_flagged=True)} == {"k1", "k2"}


def test_flag_unknown_finding_returns_false(store):
    store.put(make())
    assert store.flag("zzzz9999-9") is False
    assert store.get("k1").flagged is False


def test_flag_matches_ids_exactly(store):
    store.put(make("aaaa0000-1"))
    assert store.flag("aaaa0000") is False  # a substring of the stored text is not a finding ID


def test_put_after_flag_clears_it(store):
    store.put(make("aaaa0000-1"))
    store.flag("aaaa0000-1")
    store.put(make("cccc0000-1"))  # regenerated findings under the same key
    assert store.get("k1").flagged is False
