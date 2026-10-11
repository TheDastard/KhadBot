"""
tests/unit/encounter/test_evidence.py

Evidence refs: parsing, resolution of every ref kind on SYNTH01, malformed and stale refs,
the in-process records cache, and number extraction and matching for the verify step.
"""

from __future__ import annotations

import re

import polars as pl
import pytest

from khadbot.encounter.digest import render_digest
from khadbot.encounter.evidence import (
    EncounterData,
    UnresolvableRef,
    claim_numbers,
    extract_numbers,
    number_supported,
    numeric_values,
    parse_ref,
    resolve,
)
from khadbot.encounter.normalize import events_hash

# ===========================================================================
# Parsing
# ===========================================================================


def test_parse_every_kind():
    assert parse_ref("ev:ABC:7:death:3").n == 3
    assert parse_ref("ev:ABC:7:death:3").pull_key == "ABC:7"
    assert parse_ref("ev:ABC:7:mech:swirl").key == "swirl"
    cd = parse_ref("ev:ABC:7:cd:98008:2")
    assert (cd.kind, cd.spell_id, cd.n) == ("cd", 98008, 2)
    win = parse_ref("ev:ABC:7:win:big_aoe:1")
    assert (win.kind, win.key, win.n) == ("win", "big_aoe", 1)
    assert parse_ref("ev:ABC:7:event:0").n == 0
    assert parse_ref("ev:ABC:7:pull").kind == "pull"
    plan = parse_ref("plan:p1:0")
    assert (plan.kind, plan.key, plan.n, plan.pull_key) == ("plan", "p1", 0, None)


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "ev",
        "ev:ABC:7",
        "foo:ABC:7:pull",
        "ev::7:pull",
        "ev:ABC:x:pull",
        "ev:ABC:7:bogus:1",
        "ev:ABC:7:death",
        "ev:ABC:7:death:0",
        "ev:ABC:7:death:abc",
        "ev:ABC:7:death:1:2",
        "ev:ABC:7:cd:98008",
        "ev:ABC:7:win:big_aoe:0",
        "ev:ABC:7:event:-1",
        "ev:ABC:7:mech:",
        "ev:ABC:7:pull:1",
        "plan:p1",
    ],
)
def test_malformed_refs_raise(ref):
    with pytest.raises(UnresolvableRef):
        parse_ref(ref)


# ===========================================================================
# Resolution on SYNTH01
# ===========================================================================


def test_resolve_every_kind(synth_data):
    death = synth_data.resolve("ev:SYNTH01:1:death:1")
    assert (death["name"], death["t_ms"], death["killing_ability_name"]) == ("Alphadps", 8000, "Synth Swirl")

    mech = synth_data.resolve("ev:SYNTH01:1:mech:swirl")
    assert (mech["failures"], mech["clusters"], len(mech["cluster_rows"])) == (8, 8, 8)
    assert mech["cluster_rows"][1] == {
        "cluster": 2,
        "t_ms": 8000,
        "end_ms": 8000,
        "phase_id": "p1",
        "hits": 1,
        "players": 1,
        "damage": 100000,
        "failures": 1,
        "failed_players": ["Alphadps"],
    }

    cd = synth_data.resolve("ev:SYNTH01:2:cd:900301:2")
    assert (cd["caster"], cd["t_ms"]) == ("Healz", 44000)

    win = synth_data.resolve("ev:SYNTH01:1:win:cataclysm:2")
    assert (win["t_ms"], win["covered"], win["cooldowns_active"]) == (40000, False, [])

    event = synth_data.resolve("ev:SYNTH01:1:event:5")
    assert event == {
        "ref": "ev:SYNTH01:1:event:5",
        "seq": 5,
        "t_ms": 1800,
        "type": "cast",
        "source": "Betadps",
        "ability": "Synth Strike A",
        "ability_id": 900001,
        "phase_id": "p1",
    }

    pull = synth_data.resolve("ev:SYNTH01:1:pull")
    assert pull["outcome"] == "wipe"
    assert (pull["deaths"], pull["deaths_before_cutoff"], pull["cutoff_ms"]) == (4, 3, 41000)


def test_mechanic_with_no_hits_resolves(synth_data):
    curse = synth_data.resolve("ev:SYNTH01:1:mech:curse")
    assert (curse["hits"], curse["failures"], curse["cluster_rows"]) == (0, 1, [])


def test_cluster_rows_count_only_hit_failures(synth_data):
    # The missed Shadow Bolt is a failure on the enemy cast row, which belongs to no cluster.
    bolt = synth_data.resolve("ev:SYNTH01:1:mech:shadow_bolt")
    assert (bolt["failures"], bolt["hits"]) == (1, 1)
    assert [(c["hits"], c["failures"]) for c in bolt["cluster_rows"]] == [(1, 0)]


def test_event_rows_mark_failures_and_round_hp(synth_data):
    frame = synth_data.records("SYNTH01:1").annotated.frame
    seq = frame.filter(pl.col("is_failure") & pl.col("hp_pct").is_not_null())["seq"][0]
    row = synth_data.resolve(f"ev:SYNTH01:1:event:{seq}")
    assert row["failure"] is True
    assert row["mechanic_id"] == "swirl"
    assert row["hp_pct"] == round(row["hp_pct"], 1)
    assert "source_id" not in row and "target_id" not in row


@pytest.mark.parametrize("fight", [1, 2])
def test_every_digest_ref_resolves_to_its_record(synth_data, fight):
    digest = synth_data.digest(f"SYNTH01:{fight}")
    refs = re.findall(r"ev:[^\s\]]+", render_digest(digest))
    assert refs
    for ref in refs:
        assert synth_data.resolve(ref)["ref"] == ref
    for record in [*digest.deaths, *digest.cooldowns, *digest.coverage_windows]:
        assert synth_data.resolve(record.ref) == record.model_dump(mode="json")
    for stat in digest.mechanics:
        resolved = synth_data.resolve(stat.ref)
        assert {k: resolved[k] for k in stat.model_dump()} == stat.model_dump(mode="json")


def test_refs_beyond_digest_caps_still_resolve(synth_ingested, synth_enc_root, synth_registry, monkeypatch):
    from khadbot.encounter import digest as digest_mod

    monkeypatch.setattr(digest_mod, "DEATH_CAP", 1)
    data = EncounterData(synth_ingested, enc_root=synth_enc_root, registry=synth_registry)
    assert len(data.digest("SYNTH01:1").deaths) == 1
    assert data.resolve("ev:SYNTH01:1:death:4")["name"] == "Tankyboi"


@pytest.mark.parametrize(
    "ref",
    [
        "ev:SYNTH01:1:death:5",
        "ev:SYNTH01:1:mech:nope",
        "ev:SYNTH01:1:cd:900301:2",
        "ev:SYNTH01:1:cd:123:1",
        "ev:SYNTH01:1:win:cataclysm:3",
        "ev:SYNTH01:1:win:nope:1",
        "ev:SYNTH01:1:event:999999",
        "ev:SYNTH01:9:pull",
        "ev:NOPE:1:pull",
        "plan:p1:0",
        "ev:SYNTH01:1:death",
    ],
)
def test_stale_or_unknown_refs_raise(synth_data, ref):
    with pytest.raises(UnresolvableRef):
        synth_data.resolve(ref)


def test_module_resolve_accepts_data_or_store(synth_data, synth_ingested):
    assert resolve("ev:SYNTH01:1:death:1", synth_data)["name"] == "Alphadps"
    # A bare store uses config/ files: no synthetic encounter file, but deaths still resolve.
    assert resolve("ev:SYNTH01:1:death:1", synth_ingested)["name"] == "Alphadps"


# ===========================================================================
# Records cache
# ===========================================================================


def test_records_are_cached_until_events_change(synth_data, synth_ingested):
    first = synth_data.records("SYNTH01:1")
    assert synth_data.records("SYNTH01:1") is first

    pull = next(p for p in synth_ingested.pulls("SYNTH01") if p.fight_id == 1)
    df = synth_ingested.events(pull.pull_key).filter(pl.col("t_ms") < 30_000)
    synth_ingested.write_pull(pull, df, events_hash(df))

    rebuilt = synth_data.records("SYNTH01:1")
    assert rebuilt is not first
    with pytest.raises(UnresolvableRef):
        synth_data.resolve("ev:SYNTH01:1:death:3")  # the 41 s death is gone


def test_records_cache_is_bounded(synth_ingested, synth_enc_root, synth_registry):
    data = EncounterData(synth_ingested, enc_root=synth_enc_root, registry=synth_registry, max_cached=1)
    first = data.records("SYNTH01:1")
    data.records("SYNTH01:2")
    assert data.records("SYNTH01:1") is not first


def test_records_unknown_pull(synth_data):
    with pytest.raises(KeyError):
        synth_data.records("SYNTH01:9")
    assert synth_data.pull("garbage") is None


# ===========================================================================
# Numbers in claims
# ===========================================================================


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("took 1,234 damage", ["1,234"]),
        ("died at 12.4s", ["12.4s"]),
        ("died 12.4 seconds in", ["12.4 seconds"]),
        ("a 45% wipe", ["45%"]),
        ("Healbot2 and p2 and 3rd", []),
        ("at 2:14 into the pull", ["2:14"]),
        ("hit for 1.2k then 3M", ["1.2k", "3M"]),
        ("lasted 250ms", ["250ms"]),
        ("see ev:SYNTH01:1:death:2 for 8 hits", ["8"]),
        ("values 3,4 and 1,234,567.", ["3", "4", "1,234,567"]),
        ("v1.2 build", []),
        ("overkill -5", ["-5"]),
    ],
)
def test_extract_numbers(text, expected):
    assert extract_numbers(text) == expected


def test_claim_number_units():
    by_text = {n.text: n for n in claim_numbers("1,234 at 12.4s, 2:14, 45%, 1.2k, 3m, 250ms, 7 secs")}
    assert (by_text["1,234"].value, by_text["1,234"].unit, by_text["1,234"].decimals) == (1234, "", 0)
    assert (by_text["12.4s"].value, by_text["12.4s"].unit, by_text["12.4s"].decimals) == (12.4, "s", 1)
    assert (by_text["2:14"].value, by_text["2:14"].unit) == (134, "s")
    assert by_text["45%"].unit == "%"
    assert (by_text["1.2k"].value, by_text["1.2k"].unit) == (1.2, "k")
    assert by_text["3m"].unit == "m"
    assert by_text["250ms"].unit == "ms"
    assert (by_text["7 secs"].value, by_text["7 secs"].unit) == (7, "s")


def test_numeric_values_walks_records():
    record = {"a": 1, "b": [2.5, {"c": 3}], "flag": True, "none": None, "name": "Healbot2", "ref": "ev:X:7:death:3"}
    assert numeric_values(record) == {1.0, 2.5, 3.0, 2.0, 7.0}


@pytest.mark.parametrize(
    ("claim", "values", "ok"),
    [
        ("took 114,000", {114000}, True),
        ("took 114,001", {114000}, False),
        ("died at 23.0s", {23000}, True),
        ("died at 23s", {23400}, True),  # rounds to the claim's precision
        ("died at 23.4s", {23000}, False),
        ("died at 8.9s", {8900}, True),
        ("a 10s window", {10}, True),  # already in seconds
        ("35.0% boss health", {35.0}, True),
        ("9.9% of damage", {9.94}, True),
        ("9.9% of damage", {9.96}, False),
        ("114k damage", {114000}, True),
        ("1.2m damage", {1_150_000}, True),
        ("1.2m damage", {1_100_000}, False),
        ("at 0:08", {8000}, True),
        ("lasted 250ms", {250}, True),
        ("lasted 250ms", {250000}, False),
    ],
)
def test_number_supported(claim, values, ok):
    (number,) = claim_numbers(claim)
    assert number_supported(number, {float(v) for v in values}) is ok
