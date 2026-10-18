"""
simc/client_data.py

Read SimulationCraft's generated client data: spells, their effects, and which class and spec owns each
player spell.

SimC generates C++ tables from the game client's DB2 files for each build
(``engine/dbc/generated/`` in the SimC repository). This module reads five of them:

    sc_spell_data.inc          spells (cooldowns, charges, durations) and spell effects
    class_spells.inc           active class and spec abilities
    specialization_spells.inc  spec abilities granted with the talent tree
    trait_data.inc             talent tree entries
    client_data_version.inc    the game build the tables come from

SimC's spell table is a subset of the client's: it keeps the spells its simulations reach, so a spell
missing here may still exist in the game.

Field positions follow ``spell_data_t``, ``spelleffect_data_t`` and ``trait_data_t`` in SimC's
``engine/dbc/spell_data.hpp`` and ``trait_data.hpp``. A row with an unexpected field count raises
``SimcDataError``, so a SimC format change fails loudly instead of shifting every column.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Files and classes
# ---------------------------------------------------------------------------

#: Files read from a SimC ``engine/dbc/generated`` directory.
SIMC_FILES = (
    "sc_spell_data.inc",
    "class_spells.inc",
    "specialization_spells.inc",
    "trait_data.inc",
    "client_data_version.inc",
)

#: Class IDs as SimC's ``scaling_class`` maps them; names match ``config/registries/defensive_cooldowns.yaml``.
CLASS_NAMES: dict[int, str] = {
    1: "Warrior",
    2: "Paladin",
    3: "Hunter",
    4: "Rogue",
    5: "Priest",
    6: "Death Knight",
    7: "Shaman",
    8: "Mage",
    9: "Warlock",
    10: "Monk",
    11: "Druid",
    12: "Demon Hunter",
    13: "Evoker",
}

# Field counts after the name (spells, traits) or in total (effects); see the module docstring.
_SPELL_FIELDS = 76
_EFFECT_FIELDS = 34
_TRAIT_FIELDS = 23


def class_names(class_mask: int) -> list[str]:
    """Class names in a SimC class mask, where bit ``n - 1`` is class ``n``."""
    return [name for class_id, name in CLASS_NAMES.items() if class_mask & (1 << (class_id - 1))]


class SimcDataError(Exception):
    """A SimC data file is missing or not in the expected format."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Effect:
    spell_id: int
    index: int
    type: int
    subtype: int
    base_value: float
    trigger_spell_id: int
    target_1: int
    target_2: int


@dataclass(frozen=True)
class Spell:
    spell_id: int
    name: str
    class_mask: int
    cooldown_ms: int
    category_cooldown_ms: int
    charges: int
    charge_cooldown_ms: int
    duration_ms: float

    @property
    def effective_cooldown_ms(self) -> int:
        """Time to get one use back: the charge cooldown for charged spells, else the longer of the two cooldowns."""
        if self.charges > 1 and self.charge_cooldown_ms:
            return self.charge_cooldown_ms
        return max(self.cooldown_ms, self.category_cooldown_ms, self.charge_cooldown_ms)


@dataclass
class ClientData:
    build: str
    spells: dict[int, Spell]
    effects: dict[int, list[Effect]]
    #: spell_id -> (class_id, spec IDs; empty means every spec of the class)
    player_spells: dict[int, tuple[int, set[int]]]
    #: spell name -> spell IDs with that name
    by_name: dict[str, list[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.by_name:
            for spell in self.spells.values():
                self.by_name.setdefault(spell.name, []).append(spell.spell_id)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_NAMED_ROW = re.compile(r'^\s*\{\s*"((?:[^"\\]|\\.)*)"\s*,(.*)\}\s*,?\s*(?:/\*.*\*/)?\s*$')
_PLAIN_ROW = re.compile(r"^\s*\{(.*)\}\s*,?\s*(?:/\*.*\*/)?\s*$")
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[-+\w.]+')
_BUILD = re.compile(r'#define CLIENT_DATA_WOW_VERSION "([^"]+)"')


def _num(token: str) -> float:
    return float(int(token, 16)) if token.lower().startswith("0x") else float(token)


def _int(token: str) -> int:
    return int(token, 16) if token.lower().startswith("0x") else int(float(token))


def _table_lines(text: str, table: str) -> Iterator[str]:
    """Row lines of the C++ array named ``table``."""
    start = text.find(table)
    if start < 0:
        raise SimcDataError(f"table {table} not found")
    body_start = text.index("\n", start) + 1
    end = text.find("\n}", body_start)
    for line in text[body_start:end].splitlines():
        if line.lstrip().startswith("{"):
            yield line


def parse_spells(text: str) -> tuple[dict[int, Spell], dict[int, list[Effect]]]:
    """Spells and their effects (by spell ID) from ``sc_spell_data.inc``."""
    spells: dict[int, Spell] = {}
    for line in _table_lines(text, "__spell_data["):
        m = _NAMED_ROW.match(line)
        if not m:
            raise SimcDataError(f"unreadable spell row: {line[:80]}")
        t = _TOKEN.findall(m.group(2))
        if len(t) != _SPELL_FIELDS:
            raise SimcDataError(f"spell row has {len(t)} fields, expected {_SPELL_FIELDS}: {line[:80]}")
        spell = Spell(
            spell_id=_int(t[0]),
            name=m.group(1).replace('\\"', '"'),
            class_mask=_int(t[6]),
            cooldown_ms=_int(t[15]),
            category_cooldown_ms=_int(t[17]),
            charges=_int(t[19]),
            charge_cooldown_ms=_int(t[20]),
            duration_ms=_num(t[27]),
        )
        spells[spell.spell_id] = spell
    effects: dict[int, list[Effect]] = {}
    for line in _table_lines(text, "__spelleffect_data["):
        m = _PLAIN_ROW.match(line)
        t = _TOKEN.findall(m.group(1)) if m else []
        if len(t) != _EFFECT_FIELDS:
            raise SimcDataError(f"effect row has {len(t)} fields, expected {_EFFECT_FIELDS}: {line[:80]}")
        effect = Effect(
            spell_id=_int(t[1]),
            index=_int(t[2]),
            type=_int(t[3]),
            subtype=_int(t[4]),
            base_value=_num(t[15]),
            trigger_spell_id=_int(t[22]),
            target_1=_int(t[28]),
            target_2=_int(t[29]),
        )
        effects.setdefault(effect.spell_id, []).append(effect)
    for rows in effects.values():
        rows.sort(key=lambda e: e.index)
    return spells, effects


def _add(player_spells: dict[int, tuple[int, set[int]]], spell_id: int, class_id: int, spec_ids: Iterable[int]) -> None:
    if not spell_id or class_id not in CLASS_NAMES:
        return
    specs = {s for s in spec_ids if s}
    if spell_id in player_spells:
        known_class, known_specs = player_spells[spell_id]
        # Class-wide (empty) wins over a spec list; otherwise the spec lists merge.
        merged = set() if not known_specs or not specs else known_specs | specs
        player_spells[spell_id] = (known_class, merged)
    else:
        player_spells[spell_id] = (class_id, specs)


def parse_player_spells(class_spells: str, spec_spells: str, traits: str) -> dict[int, tuple[int, set[int]]]:
    """Every class ability, spec ability and talent spell, with its class and specs."""
    out: dict[int, tuple[int, set[int]]] = {}
    for line in _table_lines(class_spells, "__active_spells_data"):
        t = _TOKEN.findall(line)  # class, spec, spell, replaced spell, "name"
        _add(out, _int(t[2]), _int(t[0]), [_int(t[1])])
    for line in _table_lines(spec_spells, "__specialization_spell_data"):
        t = _TOKEN.findall(line)  # class, spec, spell, replaced spell, "name", flag
        _add(out, _int(t[2]), _int(t[0]), [_int(t[1])])
    for line in _table_lines(traits, "__trait_data_data"):
        t = [x for x in _TOKEN.findall(line) if not x.startswith('"')]
        # tree, class, node entry, node, ranks, points, definition, spell, replaces, overridden by,
        # row, col, selection, spec x4, starter spec x4, hero tree, node type
        if len(t) != _TRAIT_FIELDS:
            raise SimcDataError(f"trait row has {len(t)} fields, expected {_TRAIT_FIELDS}: {line[:80]}")
        class_id, specs = _int(t[1]), [_int(x) for x in t[13:17]]
        _add(out, _int(t[7]), class_id, specs)
        _add(out, _int(t[9]), class_id, specs)
    return out


def load_client_data(simc_dir: Path) -> ClientData:
    """Read the SimC tables from ``simc_dir`` (a copy of ``engine/dbc/generated``)."""
    missing = [f for f in SIMC_FILES if not (simc_dir / f).is_file()]
    if missing:
        raise SimcDataError(f"missing SimC files in {simc_dir}: {', '.join(missing)}")

    def read(name: str) -> str:
        return (simc_dir / name).read_text(encoding="utf-8", errors="replace")

    build = _BUILD.search(read("client_data_version.inc"))
    if not build:
        raise SimcDataError("no CLIENT_DATA_WOW_VERSION in client_data_version.inc")
    spells, effects = parse_spells(read("sc_spell_data.inc"))
    players = parse_player_spells(read("class_spells.inc"), read("specialization_spells.inc"), read("trait_data.inc"))
    return ClientData(build=build.group(1), spells=spells, effects=effects, player_spells=players)
