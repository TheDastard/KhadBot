"""
tests/unit/encounter/test_records_store.py

Persisted pull records: a stored blob equals the built records, a second ``EncounterData`` serves records it did not
build, every ``produced_from`` input misses, and storage failures never fail a read.
"""

from __future__ import annotations

import logging

import polars as pl
import pytest
from helpers.encounter.real_fixtures import REAL_PULLS, REAL_REPORTS, ingest_fixture

from khadbot.encounter import evidence
from khadbot.encounter import records_store as records_store_mod
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.config_files import load_friendly_damage
from khadbot.encounter.digest import records_inputs
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.ingest.normalize import events_hash
from khadbot.encounter.records_store import RecordsStore
from khadbot.storage import LocalBlobStore

PULL_KEYS = ["SYNTH01:1", "SYNTH01:2"]


@pytest.fixture
def blobs(tmp_path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
def make_data(synth_ingested, synth_enc_root, synth_defensive, synth_offensive, synth_utility, blobs):
    def make(**kw) -> EncounterData:
        return EncounterData(
            synth_ingested,
            enc_root=synth_enc_root,
            defensive=synth_defensive,
            offensive=synth_offensive,
            utility=synth_utility,
            cache=AnnotationCache(blobs),
            records_store=RecordsStore(blobs),
            **kw,
        )

    return make


def _no_build(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("records were rebuilt instead of read from the records store")

    monkeypatch.setattr(evidence, "build_pull_records", boom)


def _dump(r) -> dict:
    return {
        "deaths": [x.model_dump(mode="json") for x in r.deaths],
        "mechanics": [x.model_dump(mode="json") for x in r.mechanics],
        "cooldowns": [x.model_dump(mode="json") for x in r.cooldowns],
        "windows": [x.model_dump(mode="json") for x in r.windows],
        "digest": r.digest.model_dump(mode="json"),
        "actors": [x.model_dump(mode="json") for x in r.actors],
        "abilities": r.abilities,
        "pull": r.pull.model_dump(mode="json"),
        "cutoff_ms": r.annotated.cutoff_ms,
        "phases": [x.model_dump(mode="json") for x in r.annotated.phases],
        "encfile_hash": r.annotated.encfile_hash,
        "enc": r.enc.model_dump(mode="json") if r.enc else None,
    }


def _blob_count(blobs) -> int:
    return len(blobs.list("records/"))


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pull_key", PULL_KEYS)
def test_stored_records_equal_the_built_ones(make_data, blobs, pull_key, monkeypatch):
    built = make_data().records(pull_key)
    assert any(k.startswith("records/SYNTH01/") for k in blobs.list("records/"))

    _no_build(monkeypatch)
    loaded = make_data().records(pull_key)
    assert loaded is not built
    assert _dump(loaded) == _dump(built)
    assert loaded.annotated.frame.equals(built.annotated.frame)


def test_a_second_instance_serves_records_it_did_not_build(make_data, monkeypatch):
    first = make_data()
    first.records("SYNTH01:1")
    first.records("SYNTH01:2")

    _no_build(monkeypatch)
    second = make_data()
    deaths = second.records("SYNTH01:1").digest.deaths
    assert deaths
    assert second.resolve(deaths[0].ref)["ref"] == deaths[0].ref
    assert second.records("SYNTH01:2").digest.pull_key == "SYNTH01:2"


def test_the_blob_key_names_the_report_fight_and_inputs_hash(make_data, blobs):
    r = make_data().records("SYNTH01:1")
    keys = blobs.list("records/")
    assert len(keys) == 1
    assert keys[0].startswith("records/SYNTH01/1__") and keys[0].endswith(".json")
    store = RecordsStore(blobs)
    assert store.key(r.pull, r.digest.produced_from) == keys[0]
    assert store.keys("SYNTH01") == keys


def test_records_inputs_is_what_the_digest_records(
    synth_ingested, synth_defensive, synth_offensive, synth_utility, make_data
):
    r = make_data().records("SYNTH01:1")
    inputs = records_inputs(
        r.pull, synth_ingested, r.enc, synth_defensive, synth_offensive, synth_utility, load_friendly_damage(), 0.5
    )
    assert set(inputs) == set(r.digest.produced_from)


# ---------------------------------------------------------------------------
# Misses
# ---------------------------------------------------------------------------


def test_changed_events_write_a_new_blob(make_data, synth_ingested, blobs):
    make_data().records("SYNTH01:1")
    pull = next(p for p in synth_ingested.pulls("SYNTH01") if p.fight_id == 1)
    df = synth_ingested.events(pull.pull_key).filter(pl.col("t_ms") < 30_000)
    synth_ingested.write_pull(pull, df, events_hash(df))

    fresh = make_data().records("SYNTH01:1")
    assert _blob_count(blobs) == 2
    assert fresh.digest.produced_from["events_hash"] == events_hash(df)


def _patch_inputs(monkeypatch, **override) -> None:
    """What a bump of a version constant does to ``records_inputs``, for the lookup and for the build alike."""
    from khadbot.encounter import digest

    real = digest.records_inputs

    def patched(*a, **k):
        return {**real(*a, **k), **override}

    monkeypatch.setattr(evidence, "records_inputs", patched)
    monkeypatch.setattr(digest, "records_inputs", patched)


@pytest.mark.parametrize("what", ["records_version", "annotation_version", "cutoff", "pull"])
def test_every_produced_from_input_misses(make_data, synth_ingested, blobs, monkeypatch, what):
    make_data().records("SYNTH01:1")
    assert _blob_count(blobs) == 1
    kw = {}
    if what == "records_version":
        _patch_inputs(monkeypatch, records_version="999")
    elif what == "annotation_version":
        _patch_inputs(monkeypatch, annotation_version="999")
    elif what == "cutoff":
        kw["cutoff_fraction"] = 0.4
    else:
        pull = next(p for p in synth_ingested.pulls("SYNTH01") if p.fight_id == 1)
        changed = pull.model_copy(update={"boss_hp_pct": 12.5})
        synth_ingested.write_pull(
            changed, synth_ingested.events(pull.pull_key), synth_ingested.events_hash(pull.pull_key)
        )

    calls = []
    real = evidence.build_pull_records

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(evidence, "build_pull_records", spy)
    make_data(**kw).records("SYNTH01:1")
    assert calls, f"changing {what} did not miss the records store"
    assert _blob_count(blobs) == 2


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def test_a_failed_write_is_logged_and_the_read_succeeds(make_data, blobs, monkeypatch, caplog):
    real_put = blobs.put

    def fail(key, data):
        if key.startswith("records/"):
            raise OSError("disk full")
        real_put(key, data)

    monkeypatch.setattr(blobs, "put", fail)
    with caplog.at_level(logging.WARNING, logger=records_store_mod.log.name):
        r = make_data().records("SYNTH01:1")
    assert r.digest.pull_key == "SYNTH01:1"
    assert "disk full" in caplog.text


@pytest.mark.parametrize("damage", [b"not json", b'{"deaths": 3}'])
def test_a_corrupt_blob_is_rebuilt_and_overwritten(make_data, blobs, damage, caplog):
    make_data().records("SYNTH01:1")
    (key,) = blobs.list("records/")
    blobs.put(key, damage)

    with caplog.at_level(logging.WARNING, logger=records_store_mod.log.name):
        r = make_data().records("SYNTH01:1")
    assert "rebuilding" in caplog.text
    assert r.deaths
    assert blobs.get(key) != damage


def test_a_blob_built_from_other_inputs_is_a_miss(make_data, blobs, caplog):
    make_data().records("SYNTH01:1")
    (key,) = blobs.list("records/")
    stored = blobs.get(key)
    assert stored is not None
    assert b'"records_version":"' in stored
    blobs.put(key, stored.replace(b'"records_version":"', b'"records_version":"x'))
    with caplog.at_level(logging.WARNING, logger=records_store_mod.log.name):
        make_data().records("SYNTH01:1")
    assert "other inputs" in caplog.text


def test_without_a_records_store_nothing_is_written(synth_ingested, synth_enc_root, synth_defensive, blobs):
    EncounterData(synth_ingested, enc_root=synth_enc_root, defensive=synth_defensive).records("SYNTH01:1")
    assert blobs.list("records/") == []


# ---------------------------------------------------------------------------
# Recorded fixtures
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_REPORTS, reason="real WCL fixtures not pulled from Git LFS")
def test_every_recorded_pull_round_trips_through_the_store(tmp_path, monkeypatch):
    """Records built from the recorded pulls with the authored files under config/ equal what the store serves."""
    import asyncio

    from khadbot.encounter.store import ParquetEventStore

    events = ParquetEventStore(LocalBlobStore(tmp_path / "events"))
    for code in REAL_REPORTS:
        assert not asyncio.run(ingest_fixture(code, events)).failed
    blobs = LocalBlobStore(tmp_path / "blobs")

    def data() -> EncounterData:
        return EncounterData(events, cache=AnnotationCache(blobs), records_store=RecordsStore(blobs), max_cached=1)

    built = {key: _dump(data().records(key)) for key in REAL_PULLS}
    assert _blob_count(blobs) == len(REAL_PULLS)

    _no_build(monkeypatch)
    fresh = data()
    assert {key: _dump(fresh.records(key)) for key in REAL_PULLS} == built
