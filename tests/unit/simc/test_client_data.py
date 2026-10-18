"""
tests/unit/simc/test_client_data.py

Parsing SimC's generated client tables: spell and effect fields, player spells, the build, and failures on
missing or reshaped files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers.simc.tables import class_spells, effect_row, spec_spells, spell_data, spell_row, traits, write_tables

from khadbot.simc.client_data import (
    ClientData,
    SimcDataError,
    class_names,
    load_client_data,
    parse_player_spells,
    parse_spells,
)

WALL, SHIELD, TALENT, BOSS = range(900001, 900005)


@pytest.fixture
def data() -> ClientData:
    spells = [
        spell_row("Personal Wall", WALL, cooldown=120000, duration=8000),
        spell_row("Ally Shield", SHIELD, charges=1, charge_cd=90000, category_cd=30000),
        spell_row("Talent", TALENT, cooldown=60000, charges=2, charge_cd=45000),
        spell_row("Boss Shield", BOSS, cooldown=120000),
    ]
    effects = [
        effect_row(2, WALL, 1, 6, 108, base=20, misc=7, target_1=1),
        effect_row(1, WALL, 0, 6, 87, base=-30, target_1=1, target_2=21),
        effect_row(3, SHIELD, 0, 64, 0, trigger=WALL),
    ]
    spells_by_id, effects_by_spell = parse_spells(spell_data(spells, effects))
    players = parse_player_spells(
        class_spells([(1, 0, WALL), (5, 256, SHIELD)]),
        spec_spells([(5, 257, SHIELD), (1, 71, WALL)]),
        traits([(7, TALENT, 264), (7, TALENT, 262), (99, BOSS, 0)]),
    )
    return ClientData(build="12.0.0.1", spells=spells_by_id, effects=effects_by_spell, player_spells=players)


def test_spell_fields_parse(data: ClientData) -> None:
    wall = data.spells[WALL]
    assert (wall.name, wall.class_mask, wall.cooldown_ms, wall.duration_ms) == ("Personal Wall", 0x10, 120000, 8000)


@pytest.mark.parametrize(("spell_id", "ms"), [(WALL, 120000), (SHIELD, 90000), (TALENT, 45000), (BOSS, 120000)])
def test_effective_cooldown(data: ClientData, spell_id: int, ms: int) -> None:
    """One charge counts its charge cooldown like a cooldown; several charges count only the charge cooldown."""
    assert data.spells[spell_id].effective_cooldown_ms == ms


def test_effects_parse_in_index_order(data: ClientData) -> None:
    first, second = data.effects[WALL]
    assert (first.index, first.type, first.subtype, first.base_value) == (0, 6, 87, -30)
    assert (first.target_1, first.target_2) == (1, 21)
    assert (second.index, second.subtype, second.misc_value) == (1, 108, 7)  # a spell modifier on P_CRIT
    assert data.effects[SHIELD][0].trigger_spell_id == WALL


def test_player_spells_record_class_and_specs(data: ClientData) -> None:
    assert data.player_spells[WALL] == (1, set())  # class-wide wins over a spec row
    assert data.player_spells[SHIELD] == (5, {256, 257})
    assert data.player_spells[TALENT] == (7, {262, 264})
    assert BOSS not in data.player_spells  # class 99 is not a player class


def test_by_name_indexes_every_spell(data: ClientData) -> None:
    assert data.by_name["Personal Wall"] == [WALL]


def test_class_names_map_mask_bits() -> None:
    assert class_names(0x100) == ["Warlock"]
    assert class_names(0x1003) == ["Warrior", "Paladin", "Evoker"]
    assert class_names(0) == []


def test_spell_name_with_escaped_quote() -> None:
    spells, _ = parse_spells(spell_data([spell_row('Say \\"Hi\\"', 900099)], []))
    assert spells[900099].name == 'Say "Hi"'


@pytest.mark.parametrize(
    "text",
    [
        spell_data([spell_row("Broken", 900099).replace(", 0 },", " },", 1)], []),
        spell_data([], ["  { 1, 2, 3 },"]),
        spell_data(["  { not a row"], []),
    ],
    ids=["spell fields", "effect fields", "unreadable spell"],
)
def test_reshaped_rows_fail_loudly(text: str) -> None:
    with pytest.raises(SimcDataError):
        parse_spells(text)


def test_reshaped_trait_row_fails() -> None:
    with pytest.raises(SimcDataError, match="trait row"):
        parse_player_spells(class_spells([]), spec_spells(), traits([(1, 900001, 0)]).replace(", 0, 0 },", " },"))


def test_missing_table_fails() -> None:
    with pytest.raises(SimcDataError, match="not found"):
        parse_spells("// nothing here\n")


def test_missing_files_fail(tmp_path: Path) -> None:
    with pytest.raises(SimcDataError, match="missing SimC files"):
        load_client_data(tmp_path)


def test_load_client_data_reads_build(tmp_path: Path) -> None:
    write_tables(tmp_path, [spell_row("Personal Wall", WALL)])
    loaded = load_client_data(tmp_path)
    assert loaded.build == "12.1.5.70448"
    assert set(loaded.spells) == {WALL}


def test_load_client_data_needs_a_build(tmp_path: Path) -> None:
    write_tables(tmp_path, [])
    (tmp_path / "client_data_version.inc").write_text("// no version\n")
    with pytest.raises(SimcDataError, match="CLIENT_DATA_WOW_VERSION"):
        load_client_data(tmp_path)
