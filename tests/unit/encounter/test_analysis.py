"""
tests/unit/encounter/test_analysis.py

Analysis functions: death records, mechanic stats, cooldown uses and damage-window coverage,
on SYNTH01 and on small hand-written frames for edge cases (battle res, no HP data, aura edges).
"""

from __future__ import annotations

import pytest
from helpers.events import BOSS, make_actors, make_events, make_pull

from khadbot.encounter.analysis.cooldowns import aura_intervals, cooldown_uses, window_coverage
from khadbot.encounter.analysis.deaths import death_records
from khadbot.encounter.analysis.mechanics import mechanic_stats
from khadbot.encounter.annotate import annotate, annotate_pull
from khadbot.encounter.config_files import parse_registry

ACTORS = make_actors()


@pytest.fixture
def synth(synth_ingested, synth_enc, synth_registry):
    """Annotated SYNTH01 pulls plus everything the analysis functions take."""
    actors = synth_ingested.actors("SYNTH01")
    abilities = synth_ingested.abilities("SYNTH01")

    def get(fight):
        a = annotate_pull(f"SYNTH01:{fight}", synth_ingested, synth_enc)
        deaths = death_records(a, actors, synth_registry, abilities)
        return a, deaths

    return get, actors


# ===========================================================================
# Deaths
# ===========================================================================


class TestDeathsSynth:
    def test_every_death_in_order_with_refs(self, synth):
        get, _ = synth
        _, deaths = get(1)
        assert [(d.name, d.t_ms) for d in deaths] == [
            ("Alphadps", 8_000),
            ("Gammadps", 23_000),
            ("Betadps", 41_000),
            ("Tankyboi", 50_000),
        ]
        assert [d.ref for d in deaths] == [f"ev:SYNTH01:1:death:{n}" for n in range(1, 5)]
        assert [d.after_cutoff for d in deaths] == [False, False, False, True]
        assert [d.phase_id for d in deaths] == ["p1", "p1", "p2", "p2"]

    def test_one_shot(self, synth):
        get, _ = synth
        one_shot = get(1)[1][0]
        assert one_shot.time_to_die_ms == 100
        assert one_shot.killing_ability_name == "Synth Swirl"
        assert one_shot.overkill == 40_000
        assert one_shot.top_hits[0].amount == 100_000
        assert one_shot.top_hits[0].source_name == "Synthetic Boss"
        assert one_shot.mechanic_ids == ["swirl"]
        assert one_shot.window_healing == 14_000

    def test_multi_hit(self, synth):
        get, _ = synth
        multi = get(1)[1][1]
        assert multi.killing_ability_id == 900102
        assert multi.time_to_die_ms == 8_900
        fire_pools = [h for h in multi.top_hits if h.ability_id == 900102]
        assert len(fire_pools) == 3
        assert len(multi.top_hits) == 5
        assert [h.amount for h in multi.top_hits] == sorted((h.amount for h in multi.top_hits), reverse=True)
        # Swirl, Cataclysm, then three Fire Pools (the last capped by overkill).
        assert multi.window_damage == 14_000 + 35_000 + 28_000 + 28_000 + 10_000
        assert multi.active_defensives == [900301]  # Link Totem aura 18-26 s

    def test_kill_death(self, synth):
        get, _ = synth
        (death,) = get(2)[1]
        assert (death.name, death.t_ms, death.after_cutoff) == ("Gammadps", 55_000, False)


def _hit(t, target, amount, hp_pct=None, ability=100):
    return {
        "t_ms": t,
        "type": "damage",
        "source_id": BOSS,
        "target_id": target,
        "ability_id": ability,
        "amount": amount,
        "overkill": 0,
        "hp_pct": hp_pct,
    }


def _death(t, target, ability=100):
    return {"t_ms": t, "type": "death", "source_id": BOSS, "target_id": target, "ability_id": ability}


class TestDeathsEdgeCases:
    def test_same_player_dies_twice(self):
        rows = [
            _hit(1_000, 1, 50_000, 95.0),
            _hit(2_000, 1, 50_000, 0.0),
            _death(2_000, 1),
            # battle res at ~5 s; second life
            {"t_ms": 5_000, "type": "heal", "source_id": 2, "target_id": 1, "amount": 30_000, "hp_pct": 92.0},
            _hit(8_000, 1, 40_000, 0.0, ability=200),
            _death(8_000, 1, ability=200),
        ]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        first, second = death_records(a, ACTORS)
        assert first.window_damage == 100_000
        assert first.time_to_die_ms == 1_000
        # The second window starts after the first death even though 10 s would reach back to 0.
        assert second.window_damage == 40_000
        assert [h.ability_id for h in second.top_hits] == [200]
        assert second.window_healing == 30_000
        assert second.time_to_die_ms == 3_000
        assert (first.ref, second.ref) == ("ev:TEST:1:death:1", "ev:TEST:1:death:2")

    def test_no_hp_data_and_no_healing(self):
        rows = [_hit(1_000, 1, 50_000), _hit(2_000, 1, 60_000), _death(2_000, 1)]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        (d,) = death_records(a, ACTORS)
        assert d.time_to_die_ms is None
        assert d.window_healing is None  # Healing never ingested
        assert d.killing_ability_name is None  # no ability names given
        assert d.active_defensives == []

    def test_window_excludes_old_damage_and_pets(self):
        rows = [_hit(1_000, 1, 99_000), _hit(15_000, 1, 10_000), _death(15_000, 1), _death(16_000, 77)]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        (d,) = death_records(a, ACTORS)  # actor 77 is not a player
        assert d.window_damage == 10_000

    def test_killing_ability_falls_back_to_last_hit(self):
        rows = [_hit(1_000, 1, 10_000, ability=300), {**_death(1_000, 1), "ability_id": None}]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        (d,) = death_records(a, ACTORS, abilities={300: "Smash"})
        assert (d.killing_ability_id, d.killing_ability_name) == (300, "Smash")


# ===========================================================================
# Mechanics
# ===========================================================================


class TestMechanics:
    def test_failure_counts(self, synth, synth_enc):
        get, actors = synth
        a, deaths = get(1)
        stats = {m.mechanic_id: m for m in mechanic_stats(a, synth_enc, actors, deaths)}
        swirl = stats["swirl"]
        assert (swirl.hits, swirl.clusters, swirl.failures) == (8, 8, 8)
        assert swirl.failures_by_player == {"Betadps": 3, "Alphadps": 2, "Healz": 2, "Gammadps": 1}
        assert swirl.deaths_in_window == 3
        assert swirl.ref == "ev:SYNTH01:1:mech:swirl"
        bolt = stats["shadow_bolt"]
        assert (bolt.failures, bolt.hits, bolt.failures_by_player) == (1, 1, {})  # the cast is the failure
        curse = stats["curse"]
        assert (curse.failures, curse.hits, curse.failures_by_player) == (1, 0, {"Tankyboi": 1})

    def test_kill_has_clean_interrupts(self, synth, synth_enc):
        get, actors = synth
        a, deaths = get(2)
        stats = {m.mechanic_id: m for m in mechanic_stats(a, synth_enc, actors, deaths)}
        assert stats["shadow_bolt"].failures == 0
        assert stats["curse"].failures == 0

    def test_without_encounter_file(self, synth):
        get, actors = synth
        a, deaths = get(1)
        assert mechanic_stats(a, None, actors, deaths) == []


# ===========================================================================
# Cooldowns and coverage
# ===========================================================================


class TestCooldowns:
    def test_uses_including_unverified(self, synth, synth_registry):
        get, actors = synth
        a, _ = get(1)
        uses = cooldown_uses(a, synth_registry, actors)
        assert [(u.spell_name, u.caster, u.t_ms, u.phase_id) for u in uses] == [
            ("Synth Link Totem", "Healz", 18_000, "p1"),
            ("Synth Guardian", "Gammadps", 38_000, "p2"),
        ]
        assert not synth_registry.get(900302).verified  # still listed; the digest flags it
        assert uses[1].ref == "ev:SYNTH01:1:cd:900302:1"

    def test_refs_numbered_per_spell(self, synth, synth_registry):
        get, actors = synth
        a, _ = get(2)
        assert [u.ref for u in cooldown_uses(a, synth_registry, actors)] == [
            "ev:SYNTH01:2:cd:900301:1",
            "ev:SYNTH01:2:cd:900301:2",
        ]

    @pytest.mark.parametrize(("fight", "covered"), [(1, [True, False]), (2, [True, True])])
    def test_window_coverage(self, synth, synth_registry, fight, covered, synth_enc):
        get, _ = synth
        a, _ = get(fight)
        rows = window_coverage(a, synth_enc, synth_registry)
        assert [w.covered for w in rows] == covered
        assert [w.occurrence for w in rows] == [1, 2]
        assert rows[0].cooldowns_active == [900301]
        assert rows[1].ref == f"ev:SYNTH01:{fight}:win:cataclysm:2"

    def test_externals_do_not_cover_raid_windows(self, synth, synth_registry, synth_enc):
        # Fight 1's second Cataclysm (40 s) lands while Guardian (external) is on the tank.
        get, _ = synth
        a, _ = get(1)
        second = window_coverage(a, synth_enc, synth_registry)[1]
        assert second.t_ms == 40_000
        assert second.cooldowns_active == []

    def test_no_windows_without_encounter_file(self, synth, synth_registry):
        get, _ = synth
        a, _ = get(1)
        assert window_coverage(a, None, synth_registry) == []


class TestAuraIntervals:
    def test_pairs_open_and_preexisting(self):
        def aura(t, typ, target, spell=1):
            return {"t_ms": t, "type": typ, "source_id": 2, "target_id": target, "ability_id": spell}

        rows = [
            aura(2_000, "removebuff", 3),  # up before the pull
            aura(5_000, "applybuff", 1),
            aura(9_000, "removebuff", 1),
            aura(50_000, "applybuff", 4),  # never removed
            aura(6_000, "applybuff", 5, spell=2),  # not requested
        ]
        frame = make_events(sorted(rows, key=lambda r: r["t_ms"]))
        got = aura_intervals(frame, [1], end_ms=60_000)
        assert [(i.target_id, i.start_ms, i.end_ms) for i in got] == [
            (3, 0, 2_000),
            (1, 5_000, 9_000),
            (4, 50_000, 60_000),
        ]
        assert got[1].active_at(9_000) and not got[1].active_at(9_001)
        assert aura_intervals(frame, [], end_ms=60_000) == []

    def test_registry_kinds_filter(self, synth_registry):
        assert synth_registry.spell_ids(["raid"]) == [900301]
        reg = parse_registry({"cooldowns": []})
        assert reg.spell_ids() == []
