"""
tests/helpers/encounter/findings_contract.py

Behaviour every ``FindingsStore`` implementation must show. Subclass it in a test module and define a ``store``
fixture: the unit suite does so for SQLite, the hosted integration suite for Postgres.
"""

from __future__ import annotations

from helpers.encounter.findings import make


class FindingsStoreContract:
    """Shared tests; the subclass supplies the ``store`` fixture."""

    def test_missing_key_is_none(self, store):
        assert store.get("nope") is None
        assert store.for_pull("SYNTH01:1") == []

    def test_round_trip(self, store):
        pf = make()
        store.put(pf)
        assert store.get("k1") == pf

    def test_put_replaces_same_key(self, store):
        store.put(make("aaaa0000-1"))
        store.put(make("bbbb0000-1"))
        assert [f.id for f in store.get("k1").findings] == ["bbbb0000-1"]
        assert len(store.for_pull("SYNTH01:1")) == 1

    def test_for_pull_filters_by_pull_and_orders_newest_first(self, store):
        store.put(make(key="old", minutes=0))
        store.put(make(key="new", minutes=5))
        store.put(make(pull="SYNTH01:2", key="other"))
        assert [pf.cache_key for pf in store.for_pull("SYNTH01:1")] == ["new", "old"]

    def test_flag_hides_from_for_pull(self, store):
        store.put(make("aaaa0000-1", key="k1"))
        store.put(make("bbbb0000-1", key="k2", minutes=1))
        assert store.flag("aaaa0000-1") is True
        assert store.get("k1").flagged is True
        assert store.get("k2").flagged is False
        assert [pf.cache_key for pf in store.for_pull("SYNTH01:1")] == ["k2"]
        assert {pf.cache_key for pf in store.for_pull("SYNTH01:1", include_flagged=True)} == {"k1", "k2"}

    def test_flag_unknown_finding_returns_false(self, store):
        store.put(make())
        assert store.flag("zzzz9999-9") is False
        assert store.get("k1").flagged is False

    def test_flag_matches_ids_exactly(self, store):
        store.put(make("aaaa0000-1"))
        assert store.flag("aaaa0000") is False  # a substring of the stored text is not a finding ID

    def test_put_after_flag_clears_it(self, store):
        store.put(make("aaaa0000-1"))
        store.flag("aaaa0000-1")
        store.put(make("cccc0000-1"))  # regenerated findings under the same key
        assert store.get("k1").flagged is False
