"""
encounter/config_files.py

Authored configuration for encounter review (plan section 6.2):

    config/registries/cooldowns.yaml       → CooldownRegistry
    config/encounters/<encounter_id>.yaml  → EncounterFile

Both are validated with Pydantic and fail fast with ``ConfigurationError``. Each has a
content hash (first 12 hex of SHA-256 over the canonical JSON dump) that keys derived data,
so editing a file invalidates annotations and digests built from it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

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
RuleName = Literal["any_hit", "hits_per_cluster_gt", "not_interrupted", "debuff_duration_gt", "soak_count_lt"]
MechanicKind = Literal["avoidable", "soak", "interrupt", "dispel", "debuff", "unavoidable"]

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
}


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


class Phase(_Strict):
    id: str
    name: str
    start: PhaseTrigger


class FailureRule(_Strict):
    rule: RuleName
    value: int | None = None
    ms: int | None = None

    @model_validator(mode="after")
    def _required_param(self) -> FailureRule:
        param = _RULE_PARAM[self.rule]
        if param is not None and getattr(self, param) is None:
            raise ValueError(f"failure rule '{self.rule}' requires '{param}'")
        return self


class Mechanic(_Strict):
    id: str
    name: str
    ability_ids: list[int] = Field(min_length=1)
    kind: MechanicKind
    failure: FailureRule | None = None
    cluster_window_ms: int = 1000
    severity: Literal["low", "medium", "high"] = "medium"


class DamageWindow(_Strict):
    id: str
    name: str
    ability_ids: list[int] = Field(min_length=1)
    cluster_window_ms: int = 2000
    expected_cooldowns: int = 1


class EncounterFile(_Strict):
    encounter_id: int
    name: str
    file_version: int
    phases: list[Phase] = Field(min_length=1)
    mechanics: list[Mechanic] = []
    damage_windows: list[DamageWindow] = []
    ignore_ability_ids: list[int] = []

    @model_validator(mode="after")
    def _consistent(self) -> EncounterFile:
        _unique("phase", [p.id for p in self.phases])
        _unique("mechanic", [m.id for m in self.mechanics])
        _unique("damage window", [w.id for w in self.damage_windows])
        if self.phases[0].start.trigger != "pull":
            raise ValueError(f"first phase '{self.phases[0].id}' must use trigger: pull")
        for phase in self.phases[1:]:
            if phase.start.trigger == "pull":
                raise ValueError(f"only the first phase may use trigger: pull (phase '{phase.id}')")
        owner: dict[int, str] = {}
        for mech in self.mechanics:
            for ability_id in mech.ability_ids:
                if ability_id in owner:
                    raise ValueError(f"ability {ability_id} is in mechanics '{owner[ability_id]}' and '{mech.id}'")
                owner[ability_id] = mech.id
        return self

    def ability_ids(self) -> list[int]:
        """Every ability ID the file refers to (mechanics, windows, phase triggers), sorted."""
        ids = {i for m in self.mechanics for i in m.ability_ids}
        ids |= {i for w in self.damage_windows for i in w.ability_ids}
        ids |= {p.start.ability_id for p in self.phases if p.start.ability_id is not None}
        return sorted(ids)

    def registered_ability_ids(self) -> set[int]:
        """Abilities that do not count as unregistered damage."""
        return set(self.ability_ids()) | set(self.ignore_ability_ids)


def _unique(label: str, ids: list[str]) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate {label} id(s): {', '.join(dupes)}")


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


def load_encounter(encounter_id: int, root: Path | None = None) -> EncounterFile | None:
    """Load ``<root>/<encounter_id>.yaml``; None when no file is authored for the encounter."""
    path = Path(root or DEFAULT_ENCOUNTER_ROOT) / f"{encounter_id}.yaml"
    if not path.exists():
        return None
    enc = load_encounter_file(path)
    if enc.encounter_id != encounter_id:
        raise ConfigurationError(f"{path} declares encounter_id {enc.encounter_id}, expected {encounter_id}")
    return enc


def load_all_encounters(root: Path | None = None) -> dict[int, EncounterFile]:
    root = Path(root or DEFAULT_ENCOUNTER_ROOT)
    if not root.is_dir():
        return {}
    return {enc.encounter_id: enc for enc in (load_encounter_file(p) for p in sorted(root.glob("*.yaml")))}


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
