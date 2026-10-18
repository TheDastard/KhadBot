"""
tests/unit/encounter/test_analysis.py

Analysis functions: death records, friendly damage, mechanic stats, cooldown uses and damage-window coverage,
on SYNTH01 and on small hand-written frames for edge cases (battle res, no HP data, aura edges).
"""

from __future__ import annotations

import pytest
from helpers.encounter.events import BOSS, make_actors, make_events, make_pull
from helpers.encounter.real_fixtures import REAL_REPORTS

from khadbot.encounter.analysis.aggro import aggro_bands, aggro_holder, aggro_summaries
from khadbot.encounter.analysis.cooldowns import aura_intervals, cooldown_uses, window_coverage
from khadbot.encounter.analysis.deaths import death_records
from khadbot.encounter.analysis.friendly_damage import friendly_damage
from khadbot.encounter.analysis.mechanics import mechanic_clusters, mechanic_stats
from khadbot.encounter.annotate import annotate, annotate_pull
from khadbot.encounter.config_files import parse_encounter, parse_friendly_damage, parse_registry
from khadbot.encounter.models import FriendlyDamage

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

    def test_death_ability_zero_falls_back_to_last_hit(self):
        # Events normalized before the killing ability was filled in carry WCL's placeholder 0.
        rows = [_hit(1_000, 1, 10_000, ability=300), _death(1_000, 1, ability=0)]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        (d,) = death_records(a, ACTORS, abilities={0: "Unknown Ability", 300: "Smash"})
        assert (d.killing_ability_id, d.killing_ability_name) == (300, "Smash")

    def test_death_ability_zero_without_hits_is_unknown(self):
        a = annotate(make_pull(), make_events([_death(1_000, 1, ability=0)]), None, ACTORS)
        (d,) = death_records(a, ACTORS, abilities={0: "Unknown Ability"})
        assert (d.killing_ability_id, d.killing_ability_name) == (None, None)

    def test_window_self_damage_counts_self_inflicted_registry_abilities(self):
        rows = [
            _from(1_000, 3, 3, SELF_BURN, 2_000),
            _from(2_000, -1, 3, SELF_BURN, 1_000),  # Environment-sourced, still matched by ability ID
            _from(3_000, 2, 3, LINK, 5_000),  # redistribution, not self-inflicted
            _from(3_500, 3, 3, PROC, 700),  # involuntary: self-damage, but not the player's choice
            _hit(4_000, 3, 50_000),
            _death(4_000, 3),
        ]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        (d,) = death_records(a, ACTORS, friendly=FRIENDLY)
        assert (d.window_damage, d.window_self_damage) == (58_700, 3_000)
        (without,) = death_records(a, ACTORS)
        assert without.window_self_damage == 0

    def test_window_self_damage_respects_the_window(self):
        rows = [_from(1_000, 3, 3, SELF_BURN, 2_000), _hit(15_000, 3, 50_000), _death(15_000, 3)]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        (d,) = death_records(a, ACTORS, friendly=FRIENDLY)
        assert d.window_self_damage == 0


# ===========================================================================
# Friendly damage
# ===========================================================================

SELF_BURN, LINK, DELAY, PROC, STRAY = 701, 702, 703, 704, 799
FRIENDLY = parse_friendly_damage(
    {
        "abilities": [
            {"ability_id": SELF_BURN, "name": "Self Burn", "class": "self_inflicted"},
            {"ability_id": LINK, "name": "Link", "class": "redistribution"},
            {"ability_id": DELAY, "name": "Delay", "class": "delayed_boss"},
            {"ability_id": PROC, "name": "Proc", "class": "involuntary"},
        ]
    }
)
NAMES = {SELF_BURN: "Self Burn", LINK: "Link", DELAY: "Delay", PROC: "Proc", STRAY: "Stray Bolt"}


def _from(t, source, target, ability, amount):
    return {
        "t_ms": t,
        "type": "damage",
        "source_id": source,
        "target_id": target,
        "ability_id": ability,
        "amount": amount,
    }


def _enc_naming(*ability_ids):
    return parse_encounter(
        {
            "encounter_id": 1,
            "name": "X",
            "zone": {"id": 1, "name": "Z"},
            "file_version": 1,
            "phases": [{"id": "p1", "name": "P1", "difficulties": ["heroic"], "start": {"trigger": "pull"}}],
            "ignored_abilities": [{"ability_id": i, "name": "n", "difficulties": ["heroic"]} for i in ability_ids],
        }
    )


class TestFriendlyDamage:
    def test_classes_players_and_unclassified(self):
        rows = [
            _hit(500, 1, 100_000),  # boss damage: not friendly
            _from(1_000, 3, 3, SELF_BURN, 4_000),
            _from(2_000, -1, 4, SELF_BURN, 6_000),  # Environment source, matched by ability ID
            _from(3_000, 2, 1, LINK, 10_000),
            _from(4_000, 4, 4, DELAY, 3_000),
            _from(4_500, 5, 5, PROC, 1_000),  # involuntary: counted, never in the per-player list
            _from(5_000, 5, 1, STRAY, 2_000),  # friendly source, no file names it
            _from(6_000, -1, 1, 555, 9_000),  # Environment, unknown ability: encounter damage, not friendly
        ]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        fd = friendly_damage(a, None, FRIENDLY, ACTORS, NAMES)
        assert fd.damage == 26_000
        assert fd.share_pct == round(26_000 / 135_000 * 100, 1)
        assert fd.by_class == {
            "redistribution": 10_000,
            "delayed_boss": 3_000,
            "self_inflicted": 10_000,
            "involuntary": 1_000,
            "unclassified": 2_000,
        }
        assert list(fd.by_class) == ["redistribution", "delayed_boss", "self_inflicted", "involuntary", "unclassified"]
        assert [
            (p.name, p.damage, [(x.ability_id, x.name, x.damage) for x in p.abilities]) for p in fd.self_inflicted
        ] == [
            ("Beta", 6_000, [(SELF_BURN, "Self Burn", 6_000)]),
            ("Alpha", 4_000, [(SELF_BURN, "Self Burn", 4_000)]),
        ]
        assert [(u.ability_id, u.name, u.damage, u.share_pct) for u in fd.unclassified] == [
            (STRAY, "Stray Bolt", 2_000, 1.5)
        ]

    def test_encounter_file_abilities_are_never_unclassified(self):
        rows = [_from(1_000, 3, 4, STRAY, 2_000), _from(2_000, 3, 4, 800, 1_000)]
        enc = _enc_naming(STRAY)
        a = annotate(make_pull(difficulty=4), make_events(rows), enc, ACTORS)
        fd = friendly_damage(a, enc, FRIENDLY, ACTORS, NAMES)
        assert [u.ability_id for u in fd.unclassified] == [800]

    def test_registry_wins_over_the_encounter_file(self):
        enc = _enc_naming(SELF_BURN)
        a = annotate(make_pull(difficulty=4), make_events([_from(1_000, 3, 3, SELF_BURN, 2_000)]), enc, ACTORS)
        assert friendly_damage(a, enc, FRIENDLY, ACTORS, NAMES).by_class == {"self_inflicted": 2_000}

    def test_caps_and_tie_order(self):
        rows = [_from(1_000 + i, p, p, SELF_BURN, 1_000) for i, p in enumerate([5, 4, 3, 2, 1])]
        rows += [_from(2_000, 3, 4, 800 + i, 100 * (i + 1)) for i in range(3)]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        fd = friendly_damage(a, None, FRIENDLY, ACTORS, NAMES, player_cap=2, unclassified_cap=2)
        assert [p.name for p in fd.self_inflicted] == ["Tank", "Healer"]  # equal damage: actor ID order
        assert [u.ability_id for u in fd.unclassified] == [802, 801]
        assert fd.by_class["unclassified"] == 600  # totals are never capped

    def test_top_three_abilities_per_player(self):
        reg = parse_friendly_damage(
            {"abilities": [{"ability_id": i, "name": str(i), "class": "self_inflicted"} for i in (1, 2, 3, 4)]}
        )
        rows = [_from(1_000, 3, 3, i, 1_000 * i) for i in (1, 2, 3, 4)]
        a = annotate(make_pull(), make_events(rows), None, ACTORS)
        (p,) = friendly_damage(a, None, reg, ACTORS, {}).self_inflicted
        assert p.damage == 10_000
        assert [(x.ability_id, x.name, x.damage) for x in p.abilities] == [
            (4, None, 4_000),
            (3, None, 3_000),
            (2, None, 2_000),
        ]

    def test_none(self):
        a = annotate(make_pull(), make_events([_hit(500, 1, 1_000)]), None, ACTORS)
        assert friendly_damage(a, None, FRIENDLY, ACTORS, NAMES) == FriendlyDamage()
        empty = parse_friendly_damage({"abilities": []})
        fd = friendly_damage(
            annotate(make_pull(), make_events([_from(500, 3, 4, STRAY, 1_000)]), None, ACTORS), None, empty, ACTORS, {}
        )
        assert fd.by_class == {"unclassified": 1_000} and fd.unclassified[0].name is None


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

    def test_aura_ids_map_to_their_cooldown(self):
        def aura(t, typ, target, spell):
            return {"t_ms": t, "type": typ, "source_id": 2, "target_id": target, "ability_id": spell}

        rows = [
            aura(1_000, "applybuff", 1, 11),  # aura ID of cooldown 10
            aura(2_000, "applybuff", 1, 10),  # the cast ID as an aura pairs on its own
            aura(3_000, "removebuff", 1, 11),
            aura(4_000, "removebuff", 1, 10),
            aura(5_000, "applybuff", 3, 11),  # never removed
        ]
        frame = make_events(rows)
        got = aura_intervals(frame, {10: 10, 11: 10}, end_ms=60_000)
        assert [(i.target_id, i.spell_id, i.start_ms, i.end_ms) for i in got] == [
            (1, 10, 1_000, 3_000),
            (1, 10, 2_000, 4_000),
            (3, 10, 5_000, 60_000),
        ]

    def test_registry_kinds_filter(self, synth_registry):
        assert synth_registry.spell_ids(["raid"]) == [900301]
        reg = parse_registry({"cooldowns": []})
        assert reg.spell_ids() == []


def test_mechanic_failures_count_the_blamed_player():
    rule = {"rule": "per_cluster", "blame": {"by": "cast_target", "ability_id": 555, "ms": 5_000}}
    enc = parse_encounter(
        {
            "encounter_id": 1,
            "name": "X",
            "zone": {"id": 1, "name": "Z"},
            "file_version": 1,
            "phases": [{"id": "p1", "name": "P1", "difficulties": ["heroic"], "start": {"trigger": "pull"}}],
            "mechanics": [
                {
                    "id": "m",
                    "name": "m",
                    "difficulties": ["heroic"],
                    "ability_ids": [100],
                    "kind": "avoidable",
                    "failure": rule,
                }
            ],
        }
    )
    rows = [
        {"t_ms": 0, "type": "cast", "source_id": BOSS, "target_id": 1, "ability_id": 555},
        _hit(500, 3, 1000),
        _hit(500, 4, 1000),
    ]
    actors = make_actors()
    a = annotate(make_pull(), make_events(rows), enc, actors)
    [stat] = mechanic_stats(a, enc, actors, [])
    assert (stat.failures, stat.failures_by_player) == (1, {"Tank": 1})
    [cluster] = mechanic_clusters(a, "m", actors)
    assert (cluster.failures, cluster.failed_players) == (1, ["Tank"])


def test_aggro_counts_swings_and_taunts():
    rows = [
        {"t_ms": 0, "type": "cast", "source_id": BOSS, "target_id": 1, "ability_id": 1, "data_type": "Threat"},
        {"t_ms": 1, "type": "cast", "source_id": BOSS, "target_id": 1, "ability_id": 1, "data_type": "Threat"},
        {"t_ms": 2, "type": "applydebuff", "source_id": 2, "target_id": BOSS, "ability_id": 9, "data_type": "Threat"},
        {"t_ms": 3, "type": "cast", "source_id": BOSS, "target_id": 2, "ability_id": 1, "data_type": "Threat"},
        {"t_ms": 4, "type": "cast", "source_id": 11, "target_id": 3, "ability_id": 1, "data_type": "Threat"},  # an add
        {"t_ms": 5, "type": "cast", "source_id": BOSS, "target_id": 1, "ability_id": 1},  # not Threat data
    ]
    a = annotate(make_pull(), make_events(rows), None, make_actors())
    [boss] = aggro_summaries(a, make_actors())
    assert (boss.npc, boss.swings, boss.taunts) == ("Boss", 3, {"Healer": 1})
    assert [(t.name, t.swings, t.share_pct) for t in boss.targets] == [("Tank", 2, 66.7), ("Healer", 1, 33.3)]


def test_aggro_empty_without_threat_data():
    a = annotate(make_pull(), make_events([_hit(0, 1, 100)]), None, make_actors())
    assert aggro_summaries(a, make_actors()) == []


def _swing(t, target, npc=BOSS, inst=None):
    return {
        "t_ms": t,
        "type": "cast",
        "source_id": npc,
        "source_instance": inst,
        "target_id": target,
        "ability_id": 1,
        "data_type": "Threat",
    }


def _taunt(t, player, npc=BOSS, inst=None):
    return {
        "t_ms": t,
        "type": "applydebuff",
        "source_id": player,
        "target_id": npc,
        "target_instance": inst,
        "ability_id": 9,
        "data_type": "Threat",
    }


def _npc_death(t, npc=11, inst=None):
    return {"t_ms": t, "type": "death", "target_id": npc, "target_instance": inst, "data_type": "Threat"}


def _bands(rows, duration_ms=60_000):
    a = annotate(make_pull(duration_ms=duration_ms), make_events(rows), None, make_actors())
    return [(b.npc_id, b.npc_instance, b.player_id, b.start_ms, b.end_ms, b.start) for b in aggro_bands(a)]


class TestAggroBands:
    def test_switch_and_taunt(self):
        rows = [_swing(0, 1), _swing(2_000, 1), _taunt(3_000, 2), _swing(4_000, 2), _swing(5_000, 1)]
        assert _bands(rows, duration_ms=10_000) == [
            (BOSS, None, 1, 0, 3_000, "melee"),
            (BOSS, None, 2, 3_000, 5_000, "taunt"),
            (BOSS, None, 1, 5_000, 10_000, "melee"),  # runs to the pull end
        ]

    def test_npc_death_ends_the_band(self):
        rows = [_swing(0, 3, npc=11, inst=1), _npc_death(4_000, inst=1), _swing(5_000, 3, npc=11, inst=2)]
        assert _bands(rows, duration_ms=10_000) == [
            (11, 1, 3, 0, 4_000, "melee"),
            (11, 2, 3, 5_000, 10_000, "melee"),
        ]

    def test_quiet_gap_ends_at_the_last_event(self):
        rows = [_swing(0, 1), _swing(1_000, 1), _swing(30_000, 1)]  # 29 s without a swing
        assert _bands(rows, duration_ms=100_000) == [
            (BOSS, None, 1, 0, 1_000, "melee"),
            (BOSS, None, 1, 30_000, 30_000, "melee"),  # quiet until the pull end too
        ]

    def test_instances_are_separate(self):
        rows = [_swing(0, 1, npc=11, inst=1), _swing(100, 2, npc=11, inst=2), _swing(200, 1, npc=11, inst=1)]
        bands = _bands(rows, duration_ms=1_000)
        assert bands == [(11, 1, 1, 0, 1_000, "melee"), (11, 2, 2, 100, 1_000, "melee")]

    def test_holder(self):
        a = annotate(
            make_pull(duration_ms=10_000),
            make_events([_swing(0, 1), _taunt(3_000, 2), _swing(0, 3, npc=11, inst=4)]),
            None,
            make_actors(),
        )
        bands = aggro_bands(a)
        assert aggro_holder(bands, {BOSS}, 2_999) == 1
        assert aggro_holder(bands, {BOSS}, 3_000) == 2
        assert aggro_holder(bands, {11}, 5_000, instance=4) == 3
        assert aggro_holder(bands, {11}, 5_000, instance=5) is None
        assert aggro_holder(bands, {99}, 5_000) is None


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _overlap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> int:
    return sum(max(0, min(e1, e2) - max(s1, s2)) for s1, e1 in a for s2, e2 in b)


@pytest.mark.skipif(not REAL_REPORTS, reason="real WCL fixtures not pulled from Git LFS")
@pytest.mark.parametrize(
    ("code", "fight", "wcl_bands", "differing"),
    [
        # Each differing band is WCL keeping a holder longer after the NPC stops swinging: Gore Rattle in
        # Stage One on both pulls, and Ula'tek in the kill's last 24 s.
        ("trMgApZCVYkv3jdq", 18, 36, 1),
        ("H7z1fqDXjRTFnbtg", 20, 67, 2),
    ],
)
def test_aggro_bands_match_wcl_threat_table(code, fight, wcl_bands, differing):
    """Merged per NPC actor and player, the bands reproduce WCL's threat table and never name another player."""
    import asyncio
    import json
    import tempfile
    from pathlib import Path

    from helpers.encounter.real_fixtures import ingest_fixture
    from helpers.encounter.synth import FIXTURES

    from khadbot.encounter.store import ParquetEventStore

    with tempfile.TemporaryDirectory() as tmp:
        store = ParquetEventStore(Path(tmp))
        # The recorded filters, not the live registry's: a registry edit must not leave the fixture without Buffs.
        result = asyncio.run(ingest_fixture(code, store, [fight]))
        assert not result.failed
        a = annotate_pull(f"{code}:{fight}", store, None)
        bosses = {x.actor_id for x in store.actors(code) if x.kind == "boss"}
    ours: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for b in aggro_bands(a):  # merge instances of one NPC actor, as WCL's table does
        ours.setdefault((b.npc_id, b.player_id), []).append((b.start_ms, b.end_ms))
    ours = {k: _merged(v) for k, v in ours.items()}
    table = json.loads((FIXTURES / "wcl_views" / f"{code}_{fight}_threat_table.json").read_text())
    start = a.pull.start_ms
    wcl = {
        (t["id"], p["id"]): _merged([(b["startTime"] - start, b["endTime"] - start) for b in t["bands"]])
        for p in table["data"]["threat"]
        for t in p["targets"]
    }
    keys = set(wcl) | set(ours)
    assert sum(len(v) for v in wcl.values()) == wcl_bands
    assert sum(len(v) for v in ours.values()) == wcl_bands
    assert sum(1 for k in keys for b in wcl.get(k, []) if b not in ours.get(k, [])) == differing
    # Everything we call aggro, WCL does too (ours only ever ends a band earlier).
    assert sum(_overlap(ours.get(k, []), wcl.get(k, [])) for k in keys) == sum(
        e - s for v in ours.values() for s, e in v
    )
    # On single-instance bosses, the two never name different players at the same moment.
    wrong = sum(
        _overlap(o, w)
        for (n1, p1), o in ours.items()
        for (n2, p2), w in wcl.items()
        if n1 == n2 and n1 in bosses and p1 != p2
    )
    assert wrong == 0
