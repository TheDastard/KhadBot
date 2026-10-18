"""
encounter/config_files.py

Authored configuration for encounter review:

    config/registries/cooldowns.yaml                                          → CooldownRegistry
    config/encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml → EncounterFile

Both are validated with Pydantic and fail fast with ``ConfigurationError``. Each has a
content hash (first 12 hex of SHA-256 over the canonical JSON dump) that keys derived data,
so editing a file invalidates annotations and digests built from it.

Every encounter-file entry (phase, mechanic, damage window, ignored ability) lists the
difficulties it applies to. Analysis works on one difficulty's view of the file
(``EncounterFile.for_difficulty``), and that view is what gets hashed, so editing the Mythic
entries does not invalidate Heroic annotations.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, TypeVar

import orjson
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from khadbot.agent.agent_config import ConfigurationError

_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"
DEFAULT_REGISTRY_PATH = _CONFIG_ROOT / "registries" / "cooldowns.yaml"
DEFAULT_ENCOUNTER_ROOT = _CONFIG_ROOT / "encounters"


class _Strict(BaseModel):
    """Unknown keys are errors, so a typo in an authored file never passes silently."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Cooldown registry
# ---------------------------------------------------------------------------

CooldownKind = Literal["raid", "external", "personal"]


class CooldownEntry(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    spell_id: int
    name: str
    class_name: str = Field(alias="class")
    kind: CooldownKind
    cooldown_s: float | None = None
    duration_s: float | None = None
    verified: bool = False


class CooldownRegistry(_Strict):
    cooldowns: list[CooldownEntry]

    @model_validator(mode="after")
    def _unique_spell_ids(self) -> CooldownRegistry:
        seen: set[int] = set()
        for entry in self.cooldowns:
            if entry.spell_id in seen:
                raise ValueError(f"duplicate spell_id {entry.spell_id} in cooldown registry")
            seen.add(entry.spell_id)
        return self

    def get(self, spell_id: int) -> CooldownEntry | None:
        return next((c for c in self.cooldowns if c.spell_id == spell_id), None)

    def spell_ids(self, kinds: Sequence[CooldownKind] | None = None) -> list[int]:
        return [c.spell_id for c in self.cooldowns if kinds is None or c.kind in kinds]


# ---------------------------------------------------------------------------
# Encounter file
# ---------------------------------------------------------------------------

TriggerName = Literal["pull", "boss_cast", "boss_aura_applied", "boss_hp_below", "time", "wcl_phase"]
RuleName = Literal[
    "any_hit",
    "hits_per_cluster_gt",
    "not_interrupted",
    "debuff_duration_gt",
    "soak_count_lt",
    "hit_amount_gt",
    "aura_applied",
    "hits_on_non_tank",
    "per_cluster",
    "debuff_stacks_gt",
    "hit_while_aura",
]
BlameBy = Literal["cast_target", "aggro_holder"]
MechanicKind = Literal["avoidable", "soak", "interrupt", "dispel", "debuff", "unavoidable"]
Difficulty = Literal["lfr", "normal", "heroic", "mythic"]

# WCL fight ``difficulty`` codes. Heroic (4) and Mythic (5) are confirmed on recorded reports (docs/wcl-api.md).
WCL_DIFFICULTY: dict[int, Difficulty] = {1: "lfr", 3: "normal", 4: "heroic", 5: "mythic"}
_DIFFICULTY_ORDER: list[Difficulty] = ["lfr", "normal", "heroic", "mythic"]

# Required parameter per trigger / failure rule (None = no parameter).
_TRIGGER_PARAM: dict[str, str | None] = {
    "pull": None,
    "boss_cast": "ability_id",
    "boss_aura_applied": "ability_id",
    "boss_hp_below": "pct",
    "time": "t_ms",
    "wcl_phase": "phase",
}
_RULE_PARAM: dict[str, str | None] = {
    "any_hit": None,
    "hits_per_cluster_gt": "value",
    "not_interrupted": None,
    "debuff_duration_gt": "ms",
    "soak_count_lt": "value",
    "hit_amount_gt": "value",
    "aura_applied": None,
    "hits_on_non_tank": "ms",
    "per_cluster": None,
    "debuff_stacks_gt": "value",
    "hit_while_aura": "aura_ids",
}
_BLAME_PARAM: dict[str, str] = {"cast_target": "ability_id", "aggro_holder": "npc_game_id"}


class _Entry(_Strict):
    """An encounter-file entry that applies to the listed difficulties only."""

    difficulties: list[Difficulty] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_difficulties(self) -> _Entry:
        if len(set(self.difficulties)) != len(self.difficulties):
            raise ValueError(f"duplicate difficulty in {self.difficulties}")
        return self


_E = TypeVar("_E", bound=_Entry)


class PhaseTrigger(_Strict):
    trigger: TriggerName
    ability_id: int | None = None
    pct: float | None = None
    t_ms: int | None = None
    phase: int | None = None

    @model_validator(mode="after")
    def _required_param(self) -> PhaseTrigger:
        param = _TRIGGER_PARAM[self.trigger]
        if param is not None and getattr(self, param) is None:
            raise ValueError(f"trigger '{self.trigger}' requires '{param}'")
        return self


class Phase(_Entry):
    id: str
    name: str
    start: PhaseTrigger


class Blame(_Strict):
    """
    Who a ``per_cluster`` failure is charged to, looked up at the occurrence's first hit.

    ``cast_target``: the friendly target of the latest enemy cast of ``ability_id`` in the ``ms`` before.
    ``aggro_holder``: the player holding the NPC with ``npc_game_id`` (game ID, since a boss can have two
    actors with one name): the latest melee swing or taunt on it in the ``ms`` before, from the Threat data.

    ``reach_yd`` is the NPC's swing reach in yards: the caster of the cast for ``cast_target``, the NPC with
    ``npc_game_id`` for ``aggro_holder``. With it, annotation judges a living blamed player as in or beyond
    reach at the hit; without it, only as alive.
    """

    by: BlameBy
    ability_id: int | None = None
    npc_game_id: int | None = None
    ms: int = Field(10_000, gt=0)
    reach_yd: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _required_param(self) -> Blame:
        param = _BLAME_PARAM[self.by]
        if getattr(self, param) is None:
            raise ValueError(f"blame '{self.by}' requires '{param}'")
        return self


class FailureRule(_Strict):
    rule: RuleName
    value: int | None = None
    ms: int | None = None
    aura_ids: list[int] | None = Field(None, min_length=1)
    blame: Blame | None = None

    @model_validator(mode="after")
    def _required_param(self) -> FailureRule:
        param = _RULE_PARAM[self.rule]
        if param is not None and getattr(self, param) is None:
            raise ValueError(f"failure rule '{self.rule}' requires '{param}'")
        if self.rule == "debuff_stacks_gt" and (self.value or 0) < 0:
            raise ValueError("failure rule 'debuff_stacks_gt' requires 'value' >= 0")
        if (self.rule == "hit_while_aura") != (self.aura_ids is not None):
            raise ValueError("'aura_ids' is required by failure rule 'hit_while_aura' and allowed on no other rule")
        if (self.rule == "per_cluster") != (self.blame is not None):
            raise ValueError("'blame' is required by failure rule 'per_cluster' and allowed on no other rule")
        return self


class Mechanic(_Entry):
    id: str
    name: str
    ability_ids: list[int] = Field(min_length=1)
    kind: MechanicKind
    failure: FailureRule | None = None
    cluster_window_ms: int = 1000
    severity: Literal["low", "medium", "high"] = "medium"


class DamageWindow(_Entry):
    id: str
    name: str
    ability_ids: list[int] = Field(min_length=1)
    cluster_window_ms: int = 2000
    expected_cooldowns: int = 1


class IgnoredAbility(_Entry):
    """Known noise, excluded from unregistered damage."""

    ability_id: int
    name: str


class Zone(_Strict):
    """The WCL zone (raid instance) the encounter belongs to."""

    id: int
    name: str


class EncounterFile(_Strict):
    encounter_id: int
    name: str
    zone: Zone
    file_version: int
    phases: list[Phase] = Field(min_length=1)
    mechanics: list[Mechanic] = []
    damage_windows: list[DamageWindow] = []
    ignored_abilities: list[IgnoredAbility] = []

    @model_validator(mode="after")
    def _consistent(self) -> EncounterFile:
        for difficulty in self.difficulties():
            _check_view(self, difficulty)
        return self

    def difficulties(self) -> list[Difficulty]:
        """Every difficulty any entry applies to, easiest first."""
        entries: list[_Entry] = [*self.phases, *self.mechanics, *self.damage_windows, *self.ignored_abilities]
        used = {d for e in entries for d in e.difficulties}
        return [d for d in _DIFFICULTY_ORDER if d in used]

    def for_difficulty(self, difficulty: int | str | None) -> EncounterFile | None:
        """
        The entries for one difficulty (a WCL code or a name), each listing only that difficulty.
        None when no phase covers it, which includes an unknown or missing difficulty.
        """
        name = WCL_DIFFICULTY.get(difficulty) if isinstance(difficulty, int) else difficulty
        if name is None or not any(name in p.difficulties for p in self.phases):
            return None

        def pick(entries: Sequence[_E]) -> list[_E]:
            return [e.model_copy(update={"difficulties": [name]}) for e in entries if name in e.difficulties]

        return self.model_copy(
            update={
                "phases": pick(self.phases),
                "mechanics": pick(self.mechanics),
                "damage_windows": pick(self.damage_windows),
                "ignored_abilities": pick(self.ignored_abilities),
            }
        )

    def ability_ids(self) -> list[int]:
        """Every ability ID the file refers to (mechanics, windows, phase triggers), sorted."""
        ids = {i for m in self.mechanics for i in m.ability_ids}
        ids |= {i for w in self.damage_windows for i in w.ability_ids}
        ids |= {p.start.ability_id for p in self.phases if p.start.ability_id is not None}
        return sorted(ids)

    def registered_ability_ids(self) -> set[int]:
        """Abilities that do not count as unregistered damage."""
        return set(self.ability_ids()) | {a.ability_id for a in self.ignored_abilities}


def _check_view(enc: EncounterFile, difficulty: Difficulty) -> None:
    """Rules that hold within one difficulty. IDs may repeat across entries whose difficulties don't overlap."""

    def on(entries: Sequence[_E]) -> list[_E]:
        return [e for e in entries if difficulty in e.difficulties]

    phases, mechanics = on(enc.phases), on(enc.mechanics)
    if not phases:
        raise ValueError(f"{difficulty}: entries apply to it but no phase does")
    _unique("phase", [p.id for p in phases], difficulty)
    _unique("mechanic", [m.id for m in mechanics], difficulty)
    _unique("damage window", [w.id for w in on(enc.damage_windows)], difficulty)
    if phases[0].start.trigger != "pull":
        raise ValueError(f"{difficulty}: first phase '{phases[0].id}' must use trigger: pull")
    for phase in phases[1:]:
        if phase.start.trigger == "pull":
            raise ValueError(f"{difficulty}: only the first phase may use trigger: pull (phase '{phase.id}')")
    owner: dict[int, str] = {}
    for mech in mechanics:
        for ability_id in mech.ability_ids:
            if ability_id in owner:
                raise ValueError(
                    f"{difficulty}: ability {ability_id} is in mechanics '{owner[ability_id]}' and '{mech.id}'"
                )
            owner[ability_id] = mech.id


def _unique(label: str, ids: list[str], difficulty: Difficulty) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate {label} id(s) on {difficulty}: {', '.join(dupes)}")


# ---------------------------------------------------------------------------
# Loading and hashing
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigurationError(f"YAML parse error in {path}:\n{e}") from e


def parse_registry(data: object, label: str = "cooldown registry") -> CooldownRegistry:
    try:
        return CooldownRegistry.model_validate(data)
    except ValidationError as e:
        raise ConfigurationError(f"Schema validation failed for {label}:\n{e}") from e


def parse_encounter(data: object, label: str = "encounter file") -> EncounterFile:
    try:
        return EncounterFile.model_validate(data)
    except ValidationError as e:
        raise ConfigurationError(f"Schema validation failed for {label}:\n{e}") from e


def load_registry(path: Path | None = None) -> CooldownRegistry:
    path = Path(path) if path else DEFAULT_REGISTRY_PATH
    return parse_registry(_read_yaml(path), str(path))


def load_encounter_file(path: Path) -> EncounterFile:
    return parse_encounter(_read_yaml(path), str(path))


def slugify(name: str) -> str:
    """Lowercase ASCII words joined by underscores; apostrophes are dropped (``Ula'tek`` -> ``ulatek``)."""
    return re.sub(r"[^a-z0-9]+", "_", re.sub(r"['’]", "", name.lower())).strip("_")


def encounter_relpath(enc: EncounterFile) -> str:
    """Where ``enc`` lives under the encounter root: ``<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml``."""
    return f"{enc.zone.id}-{slugify(enc.zone.name)}/{enc.encounter_id}-{slugify(enc.name)}.yaml"


def _load_at(path: Path, root: Path) -> EncounterFile:
    """Load an encounter file and check that its path matches the zone and encounter it declares."""
    enc = load_encounter_file(path)
    expected = encounter_relpath(enc)
    if path.relative_to(root).as_posix() != expected:
        raise ConfigurationError(f"{path} declares {enc.name} ({enc.encounter_id}); it must live at {root / expected}")
    return enc


def load_encounter(encounter_id: int, root: Path | None = None) -> EncounterFile | None:
    """The whole authored file for ``encounter_id``, every difficulty; None when none is authored."""
    root = Path(root or DEFAULT_ENCOUNTER_ROOT)
    paths = sorted(root.glob(f"*/{encounter_id}-*.yaml"))
    if not paths:
        return None
    if len(paths) > 1:
        raise ConfigurationError(f"encounter {encounter_id} has several files: {', '.join(map(str, paths))}")
    return _load_at(paths[0], root)


def load_encounter_view(encounter_id: int, difficulty: int | None, root: Path | None = None) -> EncounterFile | None:
    """One difficulty's view of the encounter file; None when no file covers that encounter and difficulty."""
    enc = load_encounter(encounter_id, root)
    return enc.for_difficulty(difficulty) if enc is not None else None


def load_all_encounters(root: Path | None = None) -> dict[int, EncounterFile]:
    """Every encounter file under ``root``, all difficulties. Misplaced files and duplicate IDs raise."""
    root = Path(root or DEFAULT_ENCOUNTER_ROOT)
    if not root.is_dir():
        return {}
    found: dict[int, EncounterFile] = {}
    for path in sorted(root.rglob("*.yaml")):
        enc = _load_at(path, root)
        if enc.encounter_id in found:
            raise ConfigurationError(f"encounter {enc.encounter_id} has several files under {root}")
        found[enc.encounter_id] = enc
    return found


def _content_hash(model: BaseModel) -> str:
    canonical = orjson.dumps(model.model_dump(mode="json", by_alias=True), option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(canonical).hexdigest()[:12]


def encfile_hash(enc: EncounterFile | None) -> str:
    return _content_hash(enc) if enc is not None else "none"


def registry_hash(registry: CooldownRegistry) -> str:
    return _content_hash(registry)


def fetch_filter_inputs(
    registry: CooldownRegistry | None, encounters: dict[int, EncounterFile]
) -> tuple[list[int] | None, dict[int, list[int]]]:
    """The ``registry_spell_ids`` / ``enemy_cast_ids`` arguments ``ingest_report`` expects."""
    spell_ids = registry.spell_ids() if registry is not None else None
    return spell_ids, {eid: enc.ability_ids() for eid, enc in encounters.items()}
