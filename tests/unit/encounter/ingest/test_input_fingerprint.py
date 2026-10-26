"""
tests/unit/encounter/ingest/test_input_fingerprint.py

The input fingerprint (what a pull's events are made from) and the ingest skip built on it, on the synthetic
SYNTH01 fixture (offline; no network). The skip lives in ``process.normalize_pull_into_store``, so the
spies patch the names it calls there.
"""

from __future__ import annotations

import orjson
import pytest
from helpers.encounter.synth import SYNTH_DEFENSIVE_COOLDOWN_IDS, SYNTH_ENEMY_CAST_IDS, SYNTH_OFFENSIVE_COOLDOWN_IDS

from khadbot.encounter import store as store_module
from khadbot.encounter.ingest import fetch as fetch_module
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest import process as process_module
from khadbot.encounter.ingest.fetch import input_fingerprint, specs_for
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.encounter.models import Pull

CODE = "SYNTH01"


@pytest.fixture
def parts(synth_cache):
    pulls, actors = parse_report_meta(CODE, synth_cache.report_meta(CODE))
    pull = next(p for p in pulls if p.fight_id == 1)
    ability_ids = SYNTH_ENEMY_CAST_IDS[pull.encounter_id]
    specs = specs_for(SYNTH_DEFENSIVE_COOLDOWN_IDS, ability_ids, SYNTH_OFFENSIVE_COOLDOWN_IDS)
    return pull, specs, actors


def _first_page_key(cache, pull: Pull, spec) -> str:
    return f"{cache._dir(CODE, pull.fight_id, spec)}/page_000.json"


def _strip_fingerprints(store) -> None:
    key = store._pulls_key(CODE)
    records = orjson.loads(store.blobs.get(key))
    for rec in records:
        rec.pop("input_fingerprint")
    store.blobs.put(key, orjson.dumps(records))


def _spy_puts(store) -> list[str]:
    puts: list[str] = []
    real = store.blobs.put

    def put(key, data):
        puts.append(key)
        real(key, data)

    store.blobs.put = put
    return puts


def _count_normalizes(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = process_module.normalize_pull_with_positions

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(process_module, "normalize_pull_with_positions", spy)
    return calls


# ===========================================================================
# page_sizes and input_fingerprint
# ===========================================================================


class TestPageSizes:
    def test_sorted_and_without_marker(self, synth_cache, parts):
        pull, specs, _ = parts
        spec = specs[0]
        synth_cache.write_page(CODE, pull.fight_id, spec, 2, {"data": []})
        names = [n for n, _ in synth_cache.page_sizes(CODE, pull.fight_id, spec)]
        assert names == sorted(names) and "page_002.json" in names
        assert "_complete" not in names
        assert all(n.startswith("page_") for n in names)

    def test_matches_the_stored_bytes(self, synth_cache, parts):
        pull, specs, _ = parts
        spec = specs[0]
        stored = synth_cache.blobs.get(_first_page_key(synth_cache, pull, spec))
        assert dict(synth_cache.page_sizes(CODE, pull.fight_id, spec))["page_000.json"] == len(stored)


class TestInputFingerprint:
    def test_stable(self, synth_cache, parts):
        pull, specs, actors = parts
        first = input_fingerprint(synth_cache, pull, specs, actors)
        assert first is not None and len(first) == 64
        assert input_fingerprint(synth_cache, pull, specs, actors) == first
        assert input_fingerprint(synth_cache, pull, list(reversed(specs)), list(reversed(actors))) == first

    def test_none_while_a_spec_is_incomplete(self, synth_cache, parts):
        pull, specs, actors = parts
        synth_cache.blobs.delete(f"{synth_cache._dir(CODE, pull.fight_id, specs[0])}/_complete")
        assert input_fingerprint(synth_cache, pull, specs, actors) is None

    def test_page_size_change(self, synth_cache, parts):
        pull, specs, actors = parts
        before = input_fingerprint(synth_cache, pull, specs, actors)
        key = _first_page_key(synth_cache, pull, specs[0])
        synth_cache.blobs.put(key, synth_cache.blobs.get(key) + b" ")
        assert input_fingerprint(synth_cache, pull, specs, actors) != before

    def test_page_added(self, synth_cache, parts):
        pull, specs, actors = parts
        before = input_fingerprint(synth_cache, pull, specs, actors)
        synth_cache.write_page(CODE, pull.fight_id, specs[0], 9, {"data": []})
        assert input_fingerprint(synth_cache, pull, specs, actors) != before

    def test_filter_change(self, synth_cache, parts):
        pull, specs, actors = parts
        before = input_fingerprint(synth_cache, pull, specs, actors)
        buffs = next(s for s in specs if s.key == "Buffs")
        changed_buffs = buffs.model_copy(update={"filter_expression": "ability.id IN (1)"})
        # A changed filter is another directory: give it the same pages and marker so only the filter differs.
        for i, page in enumerate(synth_cache.pages(CODE, pull.fight_id, buffs)):
            synth_cache.write_page(CODE, pull.fight_id, changed_buffs, i, page)
        synth_cache.mark_complete(CODE, pull.fight_id, changed_buffs)
        changed = [changed_buffs if s is buffs else s for s in specs]
        after = input_fingerprint(synth_cache, pull, changed, actors)
        assert after is not None and after != before

    def test_pull_change(self, synth_cache, parts):
        pull, specs, actors = parts
        before = input_fingerprint(synth_cache, pull, specs, actors)
        assert input_fingerprint(synth_cache, pull.model_copy(update={"boss_pct": 1.5}), specs, actors) != before

    def test_actor_change(self, synth_cache, parts):
        pull, specs, actors = parts
        before = input_fingerprint(synth_cache, pull, specs, actors)
        renamed = [actors[0].model_copy(update={"name": "Someone Else"}), *actors[1:]]
        assert input_fingerprint(synth_cache, pull, specs, renamed) != before

    def test_normalize_version_change(self, synth_cache, parts, monkeypatch):
        pull, specs, actors = parts
        before = input_fingerprint(synth_cache, pull, specs, actors)
        monkeypatch.setattr(fetch_module, "NORMALIZE_VERSION", fetch_module.NORMALIZE_VERSION + 1)
        assert input_fingerprint(synth_cache, pull, specs, actors) != before


# ===========================================================================
# Store
# ===========================================================================


class TestStoreFingerprint:
    async def test_round_trip_and_old_record(self, synth_ingest_kwargs, synth_store):
        await ingest_report(CODE, **synth_ingest_kwargs)
        fp = synth_store.input_fingerprint("SYNTH01:1")
        assert fp is not None and len(fp) == 64
        assert synth_store.input_fingerprint("SYNTH01:999") is None
        _strip_fingerprints(synth_store)
        assert synth_store.input_fingerprint("SYNTH01:1") is None

    async def test_set_touches_only_the_record(self, synth_ingest_kwargs, synth_store):
        await ingest_report(CODE, **synth_ingest_kwargs)
        _strip_fingerprints(synth_store)
        puts = _spy_puts(synth_store)
        synth_store.set_input_fingerprint("SYNTH01:1", "abc")
        assert puts == [synth_store._pulls_key(CODE)]
        assert synth_store.input_fingerprint("SYNTH01:1") == "abc"
        assert synth_store.input_fingerprint("SYNTH01:2") is None

    def test_set_unknown_pull_raises(self, synth_store):
        with pytest.raises(KeyError):
            synth_store.set_input_fingerprint("SYNTH01:1", "abc")

    async def test_write_pull_uses_a_passed_positions_hash(self, synth_ingested, monkeypatch):
        store = synth_ingested
        pull = store.pulls(CODE)[0]
        df, positions = store.events(pull.pull_key), store.positions(pull.pull_key)
        calls: list[int] = []

        def fake_hash(table):
            calls.append(1)
            return "computed"

        monkeypatch.setattr(store_module, "_positions_hash", fake_hash)
        store.write_pull(pull, df, "h", [], positions, positions_hash="given", input_fingerprint="fp")
        assert calls == [] and store.positions_hash(pull.pull_key) == "given"
        assert store.input_fingerprint(pull.pull_key) == "fp"
        store.write_pull(pull, df, "h", [], positions)
        assert calls == [1] and store.positions_hash(pull.pull_key) == "computed"


# ===========================================================================
# ingest_report
# ===========================================================================


class TestIngestSkip:
    async def test_second_run_reads_no_pages(self, synth_ingest_kwargs, monkeypatch):
        first = await ingest_report(CODE, **synth_ingest_kwargs)
        assert first.pulls and not first.failed

        def forbidden(*args, **kwargs):
            raise AssertionError("an unchanged pull must not be read or normalized")

        monkeypatch.setattr(fetch_module.RawPageCache, "pages", forbidden)
        monkeypatch.setattr(process_module, "normalize_pull_with_positions", forbidden)
        again = await ingest_report(CODE, **synth_ingest_kwargs)
        assert again.pulls == [] and not again.failed
        assert sorted(again.skipped) == sorted(first.pulls)

    async def test_missing_fingerprint_is_added_without_a_rewrite(self, synth_ingest_kwargs, synth_store):
        first = await ingest_report(CODE, **synth_ingest_kwargs)
        fingerprints = {k: synth_store.input_fingerprint(k) for k in first.pulls}
        _strip_fingerprints(synth_store)
        puts = _spy_puts(synth_store)
        again = await ingest_report(CODE, **synth_ingest_kwargs)
        assert again.pulls == [] and sorted(again.skipped) == sorted(first.pulls)
        assert not [k for k in puts if k.endswith(".parquet") and not k.endswith("actors.parquet")]
        assert {k: synth_store.input_fingerprint(k) for k in first.pulls} == fingerprints

    async def test_changed_page_size_normalizes_again(
        self, synth_ingest_kwargs, synth_cache, synth_store, parts, monkeypatch
    ):
        pull, specs, _ = parts
        await ingest_report(CODE, **synth_ingest_kwargs)
        old = synth_store.input_fingerprint(pull.pull_key)
        key = _first_page_key(synth_cache, pull, specs[0])
        page = orjson.loads(synth_cache.blobs.get(key))
        synth_cache.blobs.put(key, orjson.dumps(page, option=orjson.OPT_INDENT_2))
        calls = _count_normalizes(monkeypatch)
        again = await ingest_report(CODE, [pull.fight_id], **synth_ingest_kwargs)
        # Same events in a different file: normalized again, found identical and skipped, with the new fingerprint.
        assert calls == [1] and again.skipped == [pull.pull_key]
        assert synth_store.input_fingerprint(pull.pull_key) not in (None, old)

    async def test_bumped_normalize_version_normalizes_again(self, synth_ingest_kwargs, monkeypatch):
        first = await ingest_report(CODE, **synth_ingest_kwargs)
        calls = _count_normalizes(monkeypatch)
        monkeypatch.setattr(fetch_module, "NORMALIZE_VERSION", fetch_module.NORMALIZE_VERSION + 1)
        again = await ingest_report(CODE, **synth_ingest_kwargs)
        assert not again.failed and len(calls) == len(first.pulls)

    async def test_written_pull_hashes_positions_once(self, synth_ingest_kwargs, monkeypatch):
        process_calls: list[int] = []
        store_calls: list[int] = []
        real_process, real_store = process_module.positions_hash, store_module._positions_hash

        def process_spy(table):
            process_calls.append(1)
            return real_process(table)

        def store_spy(table):
            store_calls.append(1)
            return real_store(table)

        monkeypatch.setattr(process_module, "positions_hash", process_spy)
        monkeypatch.setattr(store_module, "_positions_hash", store_spy)
        result = await ingest_report(CODE, **synth_ingest_kwargs)
        assert result.pulls
        assert len(process_calls) == len(result.pulls) and store_calls == []
