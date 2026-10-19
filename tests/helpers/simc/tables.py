"""
tests/helpers/simc/tables.py

Build SimC ``engine/dbc/generated`` tables in SimC's row format, for tests. Use fake spell IDs from 900001
upward.
"""

from __future__ import annotations

from pathlib import Path

# Flat field positions in a spell row (after the name), from spell_data_t.
_ID, _CLASS_MASK, _COOLDOWN, _CATEGORY_CD, _CHARGES, _CHARGE_CD, _DURATION = 0, 6, 15, 17, 19, 20, 27

#: Class mask spell_row writes unless told otherwise (Priest).
DEFAULT_CLASS_MASK = "0x00000010"


def spell_row(
    name: str,
    spell_id: int,
    *,
    cooldown: int = 0,
    category_cd: int = 0,
    charges: int = 0,
    charge_cd: int = 0,
    duration: int = 0,
    class_mask: str = DEFAULT_CLASS_MASK,
) -> str:
    fields = ["0"] * 76
    fields[_ID] = str(spell_id)
    fields[_CLASS_MASK] = class_mask
    fields[_COOLDOWN], fields[_CATEGORY_CD] = str(cooldown), str(category_cd)
    fields[_CHARGES], fields[_CHARGE_CD] = str(charges), str(charge_cd)
    fields[_DURATION] = f"{duration:.6f}"
    # spell_data_t groups attributes (17), class flags (4), aura and channel interrupts (2 each) in braces.
    flat = fields[:38]
    groups = [fields[38:55], fields[55:59]]
    rest = fields[59:]
    body = ", ".join(flat) + ", { " + " }, { ".join(", ".join(g) for g in groups) + " }, " + rest[0]
    body += ", { " + ", ".join(rest[1:3]) + " }, { " + ", ".join(rest[3:5]) + " }, " + ", ".join(rest[5:])
    return f'  {{ "{name}", {body} }}, /* comment */'


def effect_row(
    effect_id: int,
    spell_id: int,
    index: int,
    type_: int,
    subtype: int,
    *,
    base: float = 0.0,
    misc: int = 0,
    trigger: int = 0,
    target_1: int = 0,
    target_2: int = 0,
    mechanic: int = 0,
) -> str:
    f = ["0"] * 34
    f[0], f[1], f[2], f[3], f[4] = str(effect_id), str(spell_id), str(index), str(type_), str(subtype)
    f[15], f[16], f[22] = f"{base:.4f}", str(misc), str(trigger)
    f[26], f[28], f[29] = str(mechanic), str(target_1), str(target_2)
    return "  { " + ", ".join(f[:18]) + ", { " + ", ".join(f[18:22]) + " }, " + ", ".join(f[22:]) + " },"


def spell_data(spells: list[str], effects: list[str]) -> str:
    return (
        f"// {len(spells)} spells\nstatic spell_data_t __spell_data[{len(spells)}] = {{\n"
        + "\n".join(spells)
        + "\n};\n\n"
        + f"static spelleffect_data_t __spelleffect_data[{len(effects)}] = {{\n"
        + "\n".join(effects)
        + "\n};\n"
    )


def class_spells(rows: list[tuple[int, int, int]]) -> str:
    """(class, spec, spell) rows; spec 0 is class-wide."""
    body = "\n".join(f'  {{ {c}, {s}, {sid}, 0, "x" }},' for c, s, sid in rows)
    return (
        f"static constexpr std::array<active_class_spell_t, {len(rows)}> __active_spells_data {{ {{\n{body}\n}} }};\n"
    )


def spec_spells(rows: list[tuple[int, int, int]] | None = None) -> str:
    """(class, spec, spell) rows."""
    rows = rows or []
    body = "\n".join(f'  {{ {c}, {s}, {sid}, 0, "x", 0 }},' for c, s, sid in rows)
    return (
        f"static constexpr std::array<specialization_spell_entry_t, {len(rows)}> __specialization_spell_data {{ {{\n"
        f"{body}\n}} }};\n"
    )


def traits(rows: list[tuple[int, int, int]] | None = None) -> str:
    """(class, spell, spec) talent rows."""
    rows = rows or []
    body = "\n".join(
        f'  {{ 1, {c}, 1, 1, 1, 0, 1, {sid}, 0, 0, 1, 1, 0, "Talent", {{ {spec}, 0, 0, 0 }}, {{ 0, 0, 0, 0 }}, 0, 0 }},'
        for c, sid, spec in rows
    )
    return f"static constexpr std::array<trait_data_t, {len(rows)}> __trait_data_data {{ {{\n{body}\n}} }};\n"


def write_tables(root: Path, spells: list[str], *, build: str = "12.1.5.70448") -> None:
    """Write a minimal set of every SimC file ``load_client_data`` reads."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "sc_spell_data.inc").write_text(spell_data(spells, []))
    (root / "class_spells.inc").write_text(class_spells([]))
    (root / "specialization_spells.inc").write_text(spec_spells())
    (root / "trait_data.inc").write_text(traits())
    (root / "client_data_version.inc").write_text(f'#define CLIENT_DATA_WOW_VERSION "{build}"\n')
