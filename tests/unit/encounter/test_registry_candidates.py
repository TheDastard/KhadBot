"""
tests/unit/encounter/test_registry_candidates.py

Proposing cooldown- and friendly-damage-registry candidates from SimC client data, and checking registry
entries against it. Tables are built in SimC's row format with fake spell IDs from 900001 upward.
"""

from __future__ import annotations

import pytest
from helpers.simc.tables import class_spells, effect_row, spec_spells, spell_data, spell_row, traits

from khadbot.encounter.registry_candidates import (
    check_registry,
    classify_defensive,
    classify_offensive,
    defensive_candidates,
    friendly_damage_candidates,
    offensive_candidates,
    scripted_spells,
)
from khadbot.simc import codes as c
from khadbot.simc.client_data import ClientData, parse_player_spells, parse_spells

CASTER, ALLY, ENEMY, ANY, AREA_RAID, DEST_AREA_ALLY = 1, 21, 6, 25, 56, 31
MANA_AURA = 21  # A_OBS_MOD_MANA: not defensive, so a support aura

# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------

PERSONAL, EXTERNAL, RAID_AURA, TRIGGERED, TRIGGER_CAST, SHORT, BARRIER, BARRIER_AURA = range(900001, 900009)
SUPPORT, SCRIPTED, NUKE, HEALER, NOT_PLAYER = range(900009, 900014)


@pytest.fixture
def data() -> ClientData:
    spells = [
        spell_row("Personal Wall", PERSONAL, cooldown=120000, duration=8000),
        spell_row("Ally Shield", EXTERNAL, charges=1, charge_cd=90000, duration=12000),
        spell_row("Raid Aura", RAID_AURA, cooldown=180000, duration=10000),
        spell_row("Rally Cast", TRIGGER_CAST, cooldown=180000),
        spell_row("Rally Buff", TRIGGERED, duration=10000),
        spell_row("Short Wall", SHORT, cooldown=30000, duration=6000),
        spell_row("Barrier", BARRIER, cooldown=180000, duration=10000),
        spell_row("Barrier", BARRIER_AURA, duration=900000),
        spell_row("Mana Gift", SUPPORT, cooldown=180000, duration=8000),
        spell_row("Mystery Wall", SCRIPTED, charges=1, charge_cd=180000, duration=6000),
        spell_row("Big Nuke", NUKE, cooldown=120000),
        spell_row("Raid Heal", HEALER, cooldown=180000),
        spell_row("Boss Shield", NOT_PLAYER, cooldown=120000),
    ]
    effects = [
        effect_row(1, PERSONAL, 0, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_TAKEN, base=-30, target_1=CASTER),
        effect_row(2, EXTERNAL, 0, c.E_APPLY_AURA, c.A_SCHOOL_ABSORB, target_1=ALLY),
        effect_row(3, RAID_AURA, 0, c.E_APPLY_AREA_AURA_RAID, c.A_MOD_DAMAGE_PERCENT_TAKEN, base=-10, target_1=CASTER),
        effect_row(4, TRIGGER_CAST, 0, c.E_TRIGGER_SPELL, 0, trigger=TRIGGERED, target_1=CASTER),
        effect_row(5, TRIGGERED, 0, c.E_APPLY_AURA, c.A_MOD_INCREASE_HEALTH_2, base=10, target_1=AREA_RAID),
        effect_row(6, SHORT, 0, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_TAKEN, base=-20, target_1=CASTER),
        effect_row(7, BARRIER, 0, c.E_CREATE_AREA_TRIGGER, 0, target_1=DEST_AREA_ALLY),
        effect_row(8, BARRIER_AURA, 0, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_TAKEN, base=-20, target_1=CASTER),
        effect_row(9, SUPPORT, 0, c.E_APPLY_AURA, MANA_AURA, base=5, target_1=ALLY),
        effect_row(10, SCRIPTED, 0, c.E_APPLY_AURA, c.A_DUMMY, base=50, target_1=CASTER),
        effect_row(11, NUKE, 0, c.E_SCHOOL_DAMAGE, 0, target_1=ENEMY),
        effect_row(12, NUKE, 1, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_TAKEN, base=10, target_1=CASTER),
        effect_row(13, HEALER, 0, c.E_HEAL, 0, target_1=AREA_RAID),
        effect_row(14, NOT_PLAYER, 0, c.E_APPLY_AURA, c.A_SCHOOL_ABSORB, target_1=CASTER),
    ]
    spells_by_id, effects_by_spell = parse_spells(spell_data(spells, effects))
    players = parse_player_spells(
        class_spells([(1, 0, PERSONAL), (5, 0, EXTERNAL), (1, 0, TRIGGER_CAST), (1, 0, SHORT), (5, 0, NUKE)]),
        spec_spells([(5, 256, BARRIER), (11, 105, SUPPORT), (11, 104, SCRIPTED)]),
        traits([(7, RAID_AURA, 264), (7, HEALER, 264), (7, HEALER, 262)]),
    )
    return ClientData(build="12.0.0.1", spells=spells_by_id, effects=effects_by_spell, player_spells=players)


@pytest.mark.parametrize(
    ("spell_id", "target", "role"),
    [
        (PERSONAL, "personal", "defensive"),
        (EXTERNAL, "external", "defensive"),
        (RAID_AURA, "raid", "defensive"),
        (TRIGGER_CAST, "raid", "defensive"),
        (BARRIER, "raid", "defensive"),
        (SUPPORT, "external", "support"),
        (HEALER, "raid", "healing"),
    ],
)
def test_targets_and_roles(data: ClientData, spell_id: int, target: str, role: str) -> None:
    cand = classify_defensive(data, spell_id)
    assert cand is not None
    assert (cand.target, cand.role) == (target, role)


def test_candidate_fields(data: ClientData) -> None:
    cand = classify_defensive(data, PERSONAL)
    assert cand is not None
    assert (cand.name, cand.class_name, cand.cooldown_s, cand.duration_s) == ("Personal Wall", "Warrior", 120.0, 8.0)
    assert cand.spec_ids == []
    assert cand.evidence == [f"{PERSONAL}#0 A_MOD_DAMAGE_PERCENT_TAKEN T_UNIT_CASTER"]


def test_triggered_aura_is_named(data: ClientData) -> None:
    cand = classify_defensive(data, TRIGGER_CAST)
    assert cand is not None
    assert cand.aura_spell_ids == [TRIGGERED]
    assert cand.duration_s == 10.0


def test_same_name_aura_keeps_the_casts_duration(data: ClientData) -> None:
    cand = classify_defensive(data, BARRIER)
    assert cand is not None
    assert cand.aura_spell_ids == [BARRIER_AURA]
    assert cand.duration_s == 10.0
    assert "(same-name spell)" in cand.evidence[0]


@pytest.mark.parametrize("spell_id", [SHORT, SCRIPTED, NUKE, NOT_PLAYER])
def test_not_candidates(data: ClientData, spell_id: int) -> None:
    assert classify_defensive(data, spell_id) is None


def test_min_cooldown_is_configurable(data: ClientData) -> None:
    assert classify_defensive(data, SHORT, min_cooldown_s=30) is not None


def test_candidates_sorted_by_class_then_name(data: ClientData) -> None:
    names = [(x.class_name, x.name) for x in defensive_candidates(data)]
    assert names == sorted(names)
    assert {x.spell_id for x in defensive_candidates(data)} == {
        PERSONAL, EXTERNAL, RAID_AURA, TRIGGER_CAST, BARRIER, SUPPORT, HEALER
    }  # fmt: skip


def test_scripted_spells_list_dummy_cooldowns_only(data: ClientData) -> None:
    assert scripted_spells(data) == [(SCRIPTED, "Mystery Wall", "Druid")]


def test_check_registry_statuses(data: ClientData) -> None:
    found = defensive_candidates(data)
    entries = [
        (PERSONAL, "Personal Wall", "personal"),
        (EXTERNAL, "Ally Shield", "raid"),
        (TRIGGER_CAST, "Rally", "raid"),
        (SCRIPTED, "Mystery Wall", "personal"),
        (900999, "Gone", "raid"),
    ]
    got = {r.spell_id: r for r in check_registry(data, entries, found, "target")}
    assert got[PERSONAL].status == "matched"
    assert (got[EXTERNAL].status, got[EXTERNAL].proposed) == ("target_differs", "external")
    assert (got[TRIGGER_CAST].status, got[TRIGGER_CAST].game_name) == ("name_differs", "Rally Cast")
    assert (got[SCRIPTED].status, got[SCRIPTED].cooldown_s, got[SCRIPTED].duration_s) == ("not_a_candidate", 180.0, 6.0)
    assert got[900999].status == "not_in_game_data"


# ---------------------------------------------------------------------------
# Offensive cooldowns
# ---------------------------------------------------------------------------

LUST, WRATH, INFUSION, LEAP, LEAP_IMMUNE, WINGS, WEAKEN, QUICK, WAR_CRY, WAR_CRY_AURA = range(900031, 900041)
CLOAK, CLOAK_IMMUNE = 900041, 900042
BESTIAL_WRATH = 19574  # skipped by name: the real spell's pet aura carries both effects


@pytest.fixture
def offensive_data() -> ClientData:
    spells = [
        spell_row("Lust", LUST, cooldown=300000, duration=40000),
        spell_row("Wrath", WRATH, cooldown=120000, duration=20000),
        spell_row("Infusion", INFUSION, cooldown=120000, duration=15000),
        spell_row("Leap", LEAP, cooldown=120000, duration=24000),
        spell_row("Leap Landing", LEAP_IMMUNE, duration=1000),
        spell_row("Wings", WINGS, cooldown=120000, duration=20000),
        spell_row("Weaken", WEAKEN, cooldown=120000, duration=10000),
        spell_row("Quick", QUICK, cooldown=30000, duration=10000),
        spell_row("War Cry", WAR_CRY, cooldown=180000),
        spell_row("War Cry", WAR_CRY_AURA, duration=30000),
        spell_row("Cloak", CLOAK, cooldown=120000, duration=5000),
        spell_row("Cloak Cleanse", CLOAK_IMMUNE, duration=1000),
        spell_row("Bestial Wrath", BESTIAL_WRATH, cooldown=90000, duration=15000),
    ]
    effects = [
        effect_row(1, LUST, 0, c.E_APPLY_AREA_AURA_RAID, c.A_HASTE_ALL, base=30, target_1=CASTER),
        effect_row(2, WRATH, 0, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_DONE, base=20, target_1=CASTER),
        effect_row(3, INFUSION, 0, c.E_APPLY_AURA, c.A_HASTE_ALL, base=20, target_1=ALLY),
        effect_row(13, INFUSION, 1, c.E_APPLY_AURA, MANA_AURA, base=5, target_1=ALLY),
        effect_row(4, LEAP, 0, c.E_APPLY_AURA, c.A_HASTE_ALL, base=20, target_1=CASTER),
        effect_row(5, LEAP, 1, c.E_TRIGGER_SPELL, 0, trigger=LEAP_IMMUNE, target_1=CASTER),
        effect_row(6, LEAP_IMMUNE, 0, c.E_APPLY_AURA, c.A_DAMAGE_IMMUNITY, target_1=CASTER),
        effect_row(7, WINGS, 0, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_DONE, base=20, target_1=CASTER),
        effect_row(8, WINGS, 1, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_TAKEN, base=-20, target_1=CASTER),
        effect_row(9, WEAKEN, 0, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_DONE, base=-10, target_1=CASTER),
        effect_row(10, QUICK, 0, c.E_APPLY_AURA, c.A_HASTE_ALL, base=10, target_1=CASTER),
        effect_row(11, WAR_CRY, 0, c.E_DUMMY, 0, target_1=AREA_RAID),
        effect_row(12, WAR_CRY_AURA, 0, c.E_APPLY_AURA, c.A_MOD_PERCENT_STAT, base=10, target_1=CASTER),
        effect_row(14, CLOAK, 0, c.E_TRIGGER_SPELL, 0, trigger=CLOAK_IMMUNE, target_1=CASTER),
        effect_row(15, CLOAK_IMMUNE, 0, c.E_APPLY_AURA, c.A_SCHOOL_IMMUNITY, target_1=CASTER),
        effect_row(16, BESTIAL_WRATH, 0, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_DONE, base=25, target_1=CASTER),
        effect_row(17, BESTIAL_WRATH, 1, c.E_APPLY_AURA, c.A_SCHOOL_ABSORB, target_1=CASTER),
    ]
    spells_by_id, effects_by_spell = parse_spells(spell_data(spells, effects))
    players = parse_player_spells(
        class_spells(
            [
                (7, 0, LUST),
                (2, 0, WRATH),
                (5, 0, INFUSION),
                (12, 0, LEAP),
                (2, 0, WINGS),
                (1, 0, WAR_CRY),
                (4, 0, CLOAK),
                (3, 0, BESTIAL_WRATH),
            ]
        ),
        spec_spells([(1, 71, WEAKEN), (8, 63, QUICK)]),
        traits([]),
    )
    return ClientData(build="12.0.0.1", spells=spells_by_id, effects=effects_by_spell, player_spells=players)


@pytest.mark.parametrize(
    ("spell_id", "target"),
    [(LUST, "raid"), (WRATH, "personal"), (INFUSION, "external"), (LEAP, "personal"), (WAR_CRY, "raid")],
)
def test_offensive_targets(offensive_data: ClientData, spell_id: int, target: str) -> None:
    cand = classify_offensive(offensive_data, spell_id)
    assert cand is not None
    assert (cand.target, cand.role) == (target, "offensive")


def test_offensive_candidate_fields(offensive_data: ClientData) -> None:
    cand = classify_offensive(offensive_data, LUST)
    assert cand is not None
    assert (cand.name, cand.class_name, cand.cooldown_s, cand.duration_s) == ("Lust", "Shaman", 300.0, 40.0)
    assert cand.evidence == [f"{LUST}#0 A_HASTE_ALL E_APPLY_AREA_AURA_RAID"]


def test_offensive_aura_on_an_ally_is_not_support(offensive_data: ClientData) -> None:
    # Power Infusion used to be a support candidate in the defensive list, through either of its auras.
    assert classify_defensive(offensive_data, INFUSION) is None


def test_triggered_immunity_does_not_make_an_offensive_cooldown_defensive(offensive_data: ClientData) -> None:
    assert classify_defensive(offensive_data, LEAP) is None
    cand = classify_offensive(offensive_data, LEAP)
    assert cand is not None
    assert cand.aura_spell_ids == []  # the immunity is not the cooldown's aura


def test_brief_triggered_immunity_still_counts_without_an_offensive_effect(offensive_data: ClientData) -> None:
    # Cloak of Shadows: its 1 s triggered immunity is the only defensive effect the data shows.
    cand = classify_defensive(offensive_data, CLOAK)
    assert cand is not None
    assert cand.aura_spell_ids == [CLOAK_IMMUNE]


def test_named_offensive_cooldowns_are_never_defensive(offensive_data: ClientData) -> None:
    assert classify_defensive(offensive_data, BESTIAL_WRATH) is None
    assert classify_offensive(offensive_data, BESTIAL_WRATH) is not None


def test_own_defensive_effect_keeps_a_spell_in_both_lists(offensive_data: ClientData) -> None:
    # Damage done and damage taken on the cast itself: a person picks the file.
    assert classify_defensive(offensive_data, WINGS) is not None
    assert classify_offensive(offensive_data, WINGS) is not None


@pytest.mark.parametrize("spell_id", [WEAKEN, QUICK])
def test_not_offensive_candidates(offensive_data: ClientData, spell_id: int) -> None:
    assert classify_offensive(offensive_data, spell_id) is None


def test_offensive_candidates_sorted_and_complete(offensive_data: ClientData) -> None:
    found = offensive_candidates(offensive_data)
    assert [(x.class_name, x.name) for x in found] == sorted((x.class_name, x.name) for x in found)
    assert {x.spell_id for x in found} == {LUST, WRATH, INFUSION, LEAP, WINGS, WAR_CRY, BESTIAL_WRATH}


def test_offensive_same_name_aura(offensive_data: ClientData) -> None:
    cand = classify_offensive(offensive_data, WAR_CRY)
    assert cand is not None
    assert cand.aura_spell_ids == [WAR_CRY_AURA]
    assert "(same-name spell)" in cand.evidence[0]


def test_scripted_list_leaves_out_offensive_candidates(offensive_data: ClientData) -> None:
    # War Cry's cast is a dummy effect, but its same-name aura makes it an offensive candidate.
    assert scripted_spells(offensive_data) == []


# ---------------------------------------------------------------------------
# Friendly damage
# ---------------------------------------------------------------------------

SELF_DOT, SHARED_HIT, RAID_BURN, SELF_LEECH, PROC, ENEMY_HIT, ANY_HIT, MULTI_CLASS = range(900021, 900029)


@pytest.fixture
def friendly_data() -> ClientData:
    warlock = "0x00000100"
    spells = [
        spell_row("Self Dot", SELF_DOT, duration=10000, class_mask=warlock),
        spell_row("Shared Hit", SHARED_HIT, class_mask="0x00000002"),  # Paladin
        spell_row("Raid Burn", RAID_BURN, class_mask="0x00000800"),  # Demon Hunter
        spell_row("Self Leech", SELF_LEECH, class_mask="0x00000200"),  # Monk
        spell_row("Trinket Proc", PROC, class_mask="0x00000000"),
        spell_row("Enemy Hit", ENEMY_HIT, class_mask=warlock),
        spell_row("Any Hit", ANY_HIT, class_mask=warlock),
        spell_row("Shared Mask", MULTI_CLASS, class_mask="0x00000003"),
    ]
    effects = [
        effect_row(1, SELF_DOT, 0, c.E_APPLY_AURA, c.A_PERIODIC_DAMAGE_PERCENT, target_1=CASTER),
        effect_row(2, SHARED_HIT, 0, c.E_SCHOOL_DAMAGE, 0, target_1=ALLY),
        effect_row(3, RAID_BURN, 0, c.E_APPLY_AREA_AURA_RAID, c.A_PERIODIC_DAMAGE, target_1=CASTER),
        effect_row(4, SELF_LEECH, 0, c.E_APPLY_AURA, c.A_MOD_DAMAGE_PERCENT_TAKEN, base=-10, target_1=CASTER),
        effect_row(5, SELF_LEECH, 1, c.E_HEALTH_LEECH, 0, target_1=CASTER),
        effect_row(6, SELF_LEECH, 2, c.E_APPLY_AURA, c.A_PERIODIC_LEECH, target_1=CASTER),
        effect_row(7, PROC, 0, c.E_SCHOOL_DAMAGE, 0, target_1=CASTER),
        effect_row(8, ENEMY_HIT, 0, c.E_SCHOOL_DAMAGE, 0, target_1=ENEMY),
        effect_row(9, ENEMY_HIT, 1, c.E_APPLY_AURA, c.A_PERIODIC_DAMAGE, target_1=CASTER, target_2=ENEMY),
        effect_row(10, ANY_HIT, 0, c.E_SCHOOL_DAMAGE, 0, target_1=ANY),
        effect_row(11, MULTI_CLASS, 0, c.E_SCHOOL_DAMAGE, 0, target_1=CASTER),
    ]
    spells_by_id, effects_by_spell = parse_spells(spell_data(spells, effects))
    # None of these are player spells: the scan reads every spell with a class mask.
    return ClientData(build="12.0.0.1", spells=spells_by_id, effects=effects_by_spell, player_spells={})


def test_friendly_damage_finds_damage_on_caster_or_allies(friendly_data: ClientData) -> None:
    got = {x.spell_id: x for x in friendly_damage_candidates(friendly_data)}
    assert set(got) == {SELF_DOT, SHARED_HIT, RAID_BURN, SELF_LEECH, MULTI_CLASS}
    assert got[SELF_DOT].player_classes == ["Warlock"]
    assert got[SELF_DOT].duration_s == 10.0
    assert got[SELF_DOT].evidence == [f"{SELF_DOT}#0 A_PERIODIC_DAMAGE_PERCENT T_UNIT_CASTER"]
    assert got[SHARED_HIT].evidence == [f"{SHARED_HIT}#0 E_SCHOOL_DAMAGE T_UNIT_TARGET_ALLY"]
    assert got[RAID_BURN].evidence == [f"{RAID_BURN}#0 A_PERIODIC_DAMAGE E_APPLY_AREA_AURA_RAID"]
    # The damage-taken aura is not damage; the leech effect and leech aura are.
    assert [e.split()[1] for e in got[SELF_LEECH].evidence] == ["E_HEALTH_LEECH", "A_PERIODIC_LEECH"]
    assert got[MULTI_CLASS].player_classes == ["Warrior", "Paladin"]
    assert all(x.origin == "class" for x in got.values())


@pytest.mark.parametrize("spell_id", [PROC, ENEMY_HIT, ANY_HIT])
def test_friendly_damage_skips_items_enemies_and_any_target(friendly_data: ClientData, spell_id: int) -> None:
    assert spell_id not in {x.spell_id for x in friendly_damage_candidates(friendly_data)}


def test_friendly_damage_sorted_by_class_then_name(friendly_data: ClientData) -> None:
    keys = [(x.player_classes, x.name) for x in friendly_damage_candidates(friendly_data)]
    assert keys == sorted(keys)


def test_check_registry_compares_friendly_damage_origin(friendly_data: ClientData) -> None:
    found = friendly_damage_candidates(friendly_data)
    entries = [
        (SELF_DOT, "Self Dot", "class"),
        (SHARED_HIT, "Shared Hit", None),
        (RAID_BURN, "Burn", "class"),
        (PROC, "Trinket Proc", "item"),
        (900999, "Gone", "class"),
    ]
    got = {r.spell_id: r for r in check_registry(friendly_data, entries, found, "origin")}
    assert got[SELF_DOT].status == "matched"
    assert (got[SHARED_HIT].status, got[SHARED_HIT].proposed) == ("origin_differs", "class")
    assert (got[RAID_BURN].status, got[RAID_BURN].game_name) == ("name_differs", "Raid Burn")
    assert got[PROC].status == "not_a_candidate"
    assert got[900999].status == "not_in_game_data"
