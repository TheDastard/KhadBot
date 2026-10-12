"""
tests/unit/encounter/test_annotate.py

Annotation: cluster boundaries, each failure rule positive and negative, damage windows,
the wipe cutoff, enc=None, and the on-disk annotation cache.
"""

from __future__ import annotations

import polars as pl
import pytest
from helpers.events import BOSS, make_actors, make_events, make_pull

from khadbot.encounter import annotate as annotate_mod
from khadbot.encounter.annotate import ANNOTATION_COLUMNS, AnnotationCache, annotate, annotate_pull
from khadbot.encounter.config_files import encfile_hash, parse_encounter

ACTORS = make_actors()


def enc_with(mechanics=(), windows=()):
    return parse_encounter(
        {
            "encounter_id": 1,
            "name": "X",
            "zone": {"id": 1, "name": "Z"},
            "file_version": 1,
            "phases": [{"id": "p1", "name": "P1", "difficulties": ["heroic"], "start": {"trigger": "pull"}}],
            "mechanics": [{**m, "difficulties": ["heroic"]} for m in mechanics],
            "damage_windows": [{**w, "difficulties": ["heroic"]} for w in windows],
        }
    )


def mech(failure=None, *, ability=100, mid="m", window_ms=1000, kind="avoidable"):
    out = {"id": mid, "name": mid, "ability_ids": [ability], "kind": kind, "cluster_window_ms": window_ms}
    if failure:
        out["failure"] = failure
    return out


def hit(t, target, ability=100, amount=1000):
    return {
        "t_ms": t,
        "type": "damage",
        "source_id": BOSS,
        "target_id": target,
        "ability_id": ability,
        "amount": amount,
    }


def run(rows, enc, **pull_kw):
    return annotate(make_pull(**pull_kw), make_events(rows), enc, ACTORS)


def col(a, name):
    return a.frame[name].to_list()


# ===========================================================================
# Columns and clusters
# ===========================================================================


def test_adds_annotation_columns():
    a = run([hit(0, 1)], enc_with([mech()]))
    for name, dtype in ANNOTATION_COLUMNS.items():
        assert a.frame.schema[name] == dtype
    assert a.frame["seq"].to_list() == [0]


def test_cluster_boundaries_gap_inclusive():
    # Gaps of 900 and 1000 stay in the cluster (<= window); 1100 starts a new one.
    a = run([hit(0, 1), hit(900, 2), hit(1900, 3), hit(3000, 1)], enc_with([mech()]))
    assert col(a, "cluster_id") == [1, 1, 1, 2]


def test_clusters_are_per_mechanic():
    enc = enc_with([mech(mid="a", ability=100), mech(mid="b", ability=200)])
    a = run([hit(0, 1, 100), hit(500, 1, 200), hit(5000, 1, 100), hit(5100, 1, 200)], enc)
    assert col(a, "mechanic_id") == ["a", "b", "a", "b"]
    assert col(a, "cluster_id") == [1, 1, 2, 2]


def test_only_friendly_damage_is_clustered():
    rows = [
        {"t_ms": 0, "type": "begincast", "source_id": BOSS, "ability_id": 100},
        hit(100, BOSS),  # not a friendly target
        hit(200, 1),
    ]
    a = run(rows, enc_with([mech({"rule": "any_hit"})]))
    assert col(a, "mechanic_id") == ["m", "m", "m"]
    assert col(a, "cluster_id") == [None, None, 1]
    assert col(a, "is_failure") == [False, False, True]


# ===========================================================================
# Failure rules
# ===========================================================================


def test_any_hit():
    a = run([hit(0, 1), hit(10, 2, ability=999)], enc_with([mech({"rule": "any_hit"})]))
    assert col(a, "is_failure") == [True, False]


def test_no_failure_rule_means_no_failures():
    a = run([hit(0, 1)], enc_with([mech(None, kind="unavoidable")]))
    assert col(a, "is_failure") == [False]


def test_hits_per_cluster_gt():
    rows = [hit(0, 1), hit(100, 1), hit(200, 1), hit(300, 2), hit(5000, 1)]
    a = run(rows, enc_with([mech({"rule": "hits_per_cluster_gt", "value": 1})]))
    # Player 1 is hit 3 times in cluster 1: one failure, on the 2nd hit. Player 2 once; cluster 2 once.
    assert col(a, "is_failure") == [False, True, False, False, False]


def test_soak_count_lt():
    rows = [hit(0, 1), hit(10, 2), hit(5000, 1), hit(5010, 2), hit(5020, 3)]
    a = run(rows, enc_with([mech({"rule": "soak_count_lt", "value": 3}, kind="soak")]))
    # Cluster 1 has 2 players (failure on its first row); cluster 2 has 3 (fine).
    assert col(a, "is_failure") == [True, False, False, False, False]


def _cast(t, src=11):
    return {"t_ms": t, "type": "cast", "source_id": src, "ability_id": 100}


def _kick(t):
    return {
        "t_ms": t,
        "type": "interrupt",
        "source_id": 1,
        "target_id": 11,
        "ability_id": 7,
        "extra_ability_id": 100,
        "data_type": "Interrupts",
    }


def test_not_interrupted():
    rows = [
        _kick(950),
        _cast(1000),  # interrupted 50 ms before: fine
        _kick(1800),
        _cast(2000),  # interrupt 200 ms before: outside tolerance, failure
        _cast(5000),  # no interrupt: failure
        _cast(6000, src=1),  # a friendly cast of the same ID never counts
        _kick(7000),  # interrupt with no completed cast (the normal case): nothing to flag
    ]
    a = run(rows, enc_with([mech({"rule": "not_interrupted"}, kind="interrupt")]))
    assert a.frame.filter(pl.col("is_failure"))["t_ms"].to_list() == [2000, 5000]


def _aura(t, typ, target):
    return {"t_ms": t, "type": typ, "source_id": BOSS, "target_id": target, "ability_id": 100, "data_type": "Debuffs"}


def test_debuff_duration_gt():
    rows = [
        _aura(0, "applydebuff", 1),
        _aura(0, "applydebuff", 2),
        _aura(5_000, "removedebuff", 2),  # 5 s: fine
        _aura(10_000, "removedebuff", 1),  # 10 s: failure
        _aura(50_000, "applydebuff", 3),  # never removed; pull ends at 60 s -> 10 s: failure
        _aura(55_000, "applydebuff", 4),  # never removed; 5 s left: fine
    ]
    a = run(rows, enc_with([mech({"rule": "debuff_duration_gt", "ms": 8000}, kind="debuff")]))
    flagged = a.frame.filter(pl.col("is_failure")).select("target_id", pl.col("type").cast(pl.Utf8)).rows()
    assert flagged == [(1, "applydebuff"), (3, "applydebuff")]


def blamed(a):
    """(seq, blamed_id) of every failure row."""
    return a.frame.filter(pl.col("is_failure")).select("seq", "blamed_id").rows()


def test_victim_rules_blame_the_player_hit():
    a = run([hit(0, 1), hit(10, BOSS), hit(20, 2)], enc_with([mech({"rule": "any_hit"})]))
    assert blamed(a) == [(0, 1), (2, 2)]
    assert col(a, "blamed_id") == [1, None, 2]


def test_not_interrupted_blames_no_one():
    a = run([_cast(1_000)], enc_with([mech({"rule": "not_interrupted"}, kind="interrupt")]))
    assert blamed(a) == [(0, None)]


def test_hit_amount_gt():
    rows = [
        {**hit(0, 1), "unmitigated": 1_500_000},
        {**hit(5, 2), "unmitigated": 400_000},
        {**hit(10, 3, amount=900_000), "absorbed": 200_000},  # no unmitigated: amount + absorbed
        {**hit(15, 4, amount=900_000)},
    ]
    a = run(rows, enc_with([mech({"rule": "hit_amount_gt", "value": 1_000_000})]))
    assert blamed(a) == [(0, 1), (2, 3)]


def test_aura_applied():
    rows = [
        _aura(0, "applydebuff", 3),
        _aura(500, "removedebuff", 3),
        {**_aura(600, "applydebuff", BOSS)},  # not a player
        hit(700, 2),  # the mechanic's damage is not itself a failure
    ]
    a = run(rows, enc_with([mech({"rule": "aura_applied"}, kind="unavoidable")]))
    assert blamed(a) == [(0, 3)]


def _swing(t, target, src=11, inst=None):
    """An NPC melee swing from the Threat data."""
    return {
        "t_ms": t,
        "type": "cast",
        "source_id": src,
        "source_instance": inst,
        "target_id": target,
        "ability_id": 1,
        "data_type": "Threat",
    }


def _taunt(t, player, npc=11, inst=None):
    """A taunt debuff landing on an NPC, from the Threat data."""
    return {
        "t_ms": t,
        "type": "applydebuff",
        "source_id": player,
        "target_id": npc,
        "target_instance": inst,
        "ability_id": 9,
        "data_type": "Threat",
    }


def test_hits_on_non_tank():
    rows = [
        _swing(0, 1, inst=1),  # two adds sharing actor ID 11: instance 1 tanked by player 1,
        _swing(500, 5, inst=2),  # instance 2 by player 5
        {**hit(1_000, 1), "source_id": 11, "source_instance": 1},  # its own tank: fine
        {**hit(1_000, 5), "source_id": 11, "source_instance": 1},  # the other add's tank: failure
        {**hit(1_000, 2), "source_id": 11, "source_instance": 1},  # failure
        {**hit(2_000, 2), "source_id": 11, "source_instance": 1},  # same player, same cluster: no second failure
        {**hit(2_000, 3), "source_id": BOSS},  # the boss has no aggro holder: failure
        _swing(9_000, 4, inst=1),
        {**hit(20_000, 4), "source_id": 11, "source_instance": 1},  # holder's last swing 11 s ago: failure
        _taunt(29_000, 3, inst=1),
        {**hit(30_000, 3), "source_id": 11, "source_instance": 1},  # taunted it a second ago: fine
    ]
    a = run(rows, enc_with([mech({"rule": "hits_on_non_tank", "ms": 10_000}, window_ms=1_000)]))
    assert blamed(a) == [(3, 5), (4, 2), (6, 3), (8, 4)]


def test_per_cluster_blames_cast_target():
    cast = {"t_ms": 1_000, "type": "cast", "source_id": BOSS, "target_id": 1, "ability_id": 555}
    rows = [
        {**cast, "target_id": 2, "t_ms": 0},  # older cast at a different target
        cast,
        hit(1_400, 3),
        hit(1_400, 4),
        hit(1_800, 5),
        hit(30_000, 3),  # second occurrence, no cast in the 5 s before
    ]
    rule = {"rule": "per_cluster", "blame": {"by": "cast_target", "ability_id": 555, "ms": 5_000}}
    a = run(rows, enc_with([mech(rule, window_ms=2_000)]))
    assert blamed(a) == [(2, 1), (5, None)]
    assert a.frame.filter(pl.col("is_failure"))["cluster_id"].to_list() == [1, 2]


def test_per_cluster_blames_aggro_holder():
    rows = [
        _swing(0, 1),
        _swing(2_000, 2),  # the add (game ID 2) swung at player 2 ...
        _taunt(3_000, 3),  # ... then player 3 taunted it and never reached melee
        _swing(3_500, 4, src=BOSS),  # a different NPC
        {**hit(4_000, 5, ability=1), "source_id": 11},  # a melee damage hit is not aggro evidence
        hit(5_000, 4),
        hit(5_000, 5),
    ]
    rule = {"rule": "per_cluster", "blame": {"by": "aggro_holder", "npc_game_id": 2, "ms": 30_000}}
    a = run(rows, enc_with([mech(rule, ability=100)]))
    assert blamed(a) == [(5, 3)]


def test_per_cluster_aggro_holder_outside_lookback_or_after_death():
    rule = {"rule": "per_cluster", "blame": {"by": "aggro_holder", "npc_game_id": 2, "ms": 5_000}}
    old = [_swing(0, 1), hit(10_000, 4)]  # last swing 10 s before, lookback 5 s
    assert blamed(run(old, enc_with([mech(rule)]))) == [(1, None)]
    died = [_swing(0, 1), {"t_ms": 500, "type": "death", "target_id": 11, "data_type": "Threat"}, hit(1_000, 4)]
    assert blamed(run(died, enc_with([mech(rule)]))) == [(2, None)]


def test_per_cluster_without_a_match_still_fails():
    rule = {"rule": "per_cluster", "blame": {"by": "aggro_holder", "npc_game_id": 2}}
    a = run([hit(0, 1), hit(10, 2)], enc_with([mech(rule)]))
    assert blamed(a) == [(0, None)]


# ===========================================================================
# Damage windows
# ===========================================================================


def test_damage_window_occurrences_on_hits_only():
    window = {"id": "w", "name": "W", "ability_ids": [300], "cluster_window_ms": 2000}
    rows = [
        {"t_ms": 0, "type": "cast", "source_id": BOSS, "ability_id": 300},
        hit(1000, 1, 300),
        hit(1000, 2, 300),
        hit(10_000, 1, 300),
    ]
    a = run(rows, enc_with(windows=[window]))
    assert col(a, "window_id") == [None, "w", "w", "w"]
    assert col(a, "window_occurrence") == [None, 1, 1, 2]
    assert col(a, "mechanic_id") == [None] * 4


# ===========================================================================
# Cutoff
# ===========================================================================


def _death(t, target):
    return {"t_ms": t, "type": "death", "source_id": BOSS, "target_id": target}


def test_cutoff_on_wipe_is_nth_player_death():
    rows = [_death(5_000, 1), _death(6_000, 99), _death(7_000, 2), _death(9_000, 3), _death(12_000, 4)]
    a = run(rows, enc_with())  # 5 players, fraction 0.5 -> 3rd death; actor 99 is not a player
    assert a.cutoff_ms == 9_000


def test_cutoff_needs_enough_deaths():
    assert run([_death(5_000, 1), _death(6_000, 2)], enc_with()).cutoff_ms is None


def test_no_cutoff_on_kill():
    rows = [_death(1, 1), _death(2, 2), _death(3, 3)]
    assert run(rows, enc_with(), kill=True).cutoff_ms is None


def test_cutoff_fraction():
    rows = [_death(1_000, 1), _death(2_000, 2)]
    a = annotate(make_pull(), make_events(rows), None, ACTORS, cutoff_fraction=0.2)
    assert a.cutoff_ms == 1_000


# ===========================================================================
# No encounter file
# ===========================================================================


def test_without_encounter_file():
    rows = [hit(0, 1), _death(1_000, 1), _death(2_000, 2), _death(3_000, 3)]
    a = annotate(make_pull(), make_events(rows), None, ACTORS)
    assert a.phases == []
    assert a.encfile_hash == "none"
    assert a.unmatched_phase is None
    for name in ANNOTATION_COLUMNS:
        if name != "is_failure":
            assert a.frame[name].null_count() == a.frame.height
    assert not a.frame["is_failure"].any()
    assert a.cutoff_ms == 3_000


# ===========================================================================
# SYNTH01 and the cache
# ===========================================================================


@pytest.mark.parametrize(("fight", "cutoff"), [(1, 41_000), (2, None)])
def test_synth(synth_ingested, synth_enc, fight, cutoff):
    a = annotate_pull(f"SYNTH01:{fight}", synth_ingested, synth_enc)
    assert a.cutoff_ms == cutoff
    assert a.unmatched_phase is None
    frame = a.frame
    assert frame.filter(pl.col("t_ms") >= a.phases[1].start_ms)["phase_id"].unique().to_list() == ["p2"]
    missed = frame.filter(pl.col("is_failure") & (pl.col("mechanic_id") == "shadow_bolt"))
    assert missed["t_ms"].to_list() == ([32_000] if fight == 1 else [])
    curse = frame.filter(pl.col("is_failure") & (pl.col("mechanic_id") == "curse"))
    assert curse.height == (1 if fight == 1 else 0)
    occurrences = frame.filter(pl.col("window_id") == "cataclysm")["window_occurrence"].unique().sort()
    assert occurrences.to_list() == [1, 2]


def test_cache_round_trip_and_invalidation(synth_ingested, synth_enc, tmp_path, monkeypatch):
    cache = AnnotationCache(tmp_path / "annotated")
    first = annotate_pull("SYNTH01:1", synth_ingested, synth_enc, cache=cache)
    stem = f"1__{encfile_hash(synth_enc)}"
    assert (tmp_path / "annotated" / "SYNTH01" / f"{stem}.parquet").exists()
    assert not list((tmp_path / "annotated").rglob("*.tmp"))

    def boom(*args, **kwargs):
        raise AssertionError("cache miss")

    monkeypatch.setattr(annotate_mod, "annotate", boom)
    second = annotate_pull("SYNTH01:1", synth_ingested, synth_enc, cache=cache)
    assert second.frame.equals(first.frame)
    assert second.phases == first.phases
    assert second.cutoff_ms == first.cutoff_ms

    # A different cutoff fraction or a changed events_hash is a miss.
    with pytest.raises(AssertionError, match="cache miss"):
        annotate_pull("SYNTH01:1", synth_ingested, synth_enc, cache=cache, cutoff_fraction=0.4)
    assert cache.get(first.pull, first.encfile_hash, "other-hash", 0.5) is None


def test_cache_misses_after_annotation_version_change(synth_ingested, synth_enc, tmp_path, monkeypatch):
    cache = AnnotationCache(tmp_path / "annotated")
    first = annotate_pull("SYNTH01:1", synth_ingested, synth_enc, cache=cache)
    ev_hash = synth_ingested.events_hash("SYNTH01:1")
    assert cache.get(first.pull, first.encfile_hash, ev_hash, 0.5) is not None
    monkeypatch.setattr(annotate_mod, "ANNOTATION_VERSION", annotate_mod.ANNOTATION_VERSION + 1)
    assert cache.get(first.pull, first.encfile_hash, ev_hash, 0.5) is None


def test_annotate_pull_unknown_pull(synth_ingested, synth_enc):
    with pytest.raises(KeyError, match="SYNTH01:99"):
        annotate_pull("SYNTH01:99", synth_ingested, synth_enc)
