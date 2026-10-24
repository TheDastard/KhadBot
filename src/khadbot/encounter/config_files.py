"""
encounter/config_files.py

Authored configuration for encounter review:

    config/registries/defensive_cooldowns.yaml                                → CooldownRegistry
    config/registries/friendly_damage.yaml                                    → FriendlyDamageRegistry
    config/encounters/<zone_id>-<zone_slug>/<encounter_id>-<encounter_slug>.yaml → EncounterFile

All are validated with Pydantic and fail fast with ``ConfigurationError``. Each has a
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
from typing import Any, Literal, TypeVar

import orjson
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
    model_validator,
)

from khadbot.errors import ConfigurationError

_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"
DEFAULT_DEFENSIVE_COOLDOWNS_PATH = _CONFIG_ROOT / "registries" / "defensive_cooldowns.yaml"
DEFAULT_OFFENSIVE_COOLDOWNS_PATH = _CONFIG_ROOT / "registries" / "offensive_cooldowns.yaml"
DEFAULT_UTILITY_COOLDOWNS_PATH = _CONFIG_ROOT / "registries" / "utility_cooldowns.yaml"
DEFAULT_FRIENDLY_DAMAGE_PATH = _CONFIG_ROOT / "registries" / "friendly_damage.yaml"
DEFAULT_ENCOUNTER_ROOT = _CONFIG_ROOT / "encounters"


class _Strict(BaseModel):
    """Unknown keys are errors, so a typo in an authored file never passes silently."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Cooldown registries
# ---------------------------------------------------------------------------

# Which registry file a cooldown lives in. Defensive cooldowns keep players alive: damage reduction, absorbs,
# immunities and healing cooldowns. Offensive cooldowns raise damage done: haste, damage or stat buffs. Utility
# cooldowns help allies move, cast or come back, or control enemies.
CooldownCategory = Literal["defensive", "offensive", "utility"]
# Who a cooldown's effect lands on: only the caster, one chosen ally, or an area or the whole raid; for utility
# cooldowns also one enemy or the enemies in an area.
CooldownTarget = Literal["raid", "external", "personal", "enemy", "enemies"]
ENEMY_TARGETS: frozenset[str] = frozenset({"enemy", "enemies"})
# What a utility cooldown does: on allies, speed (movement), mana, a battle resurrection, root and snare immunity
# (freedom) or moving an ally (reposition); on enemies, a stun, an incapacitate, a knockback, a grip, a root or a
# slow.
UtilityRole = Literal[
    "resurrect",
    "mana",
    "freedom",
    "reposition",
    "movement",
    "stun",
    "incapacitate",
    "knockback",
    "grip",
    "root",
    "slow",
]


class CooldownEntry(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    spell_id: int
    name: str
    class_name: str = Field(alias="class")
    target: CooldownTarget
    cooldown_s: float | None = None
    duration_s: float | None = None
    verified: bool = False
    # Spells the cooldown's aura is applied under when they differ from the cast (Rallying Cry casts 97462 and
    # buffs players with 97463). Buffs rows under these IDs count as this entry's aura.
    aura_spell_ids: list[int] = Field(default_factory=list)
    # What a utility cooldown does; only utility entries have one.
    role: UtilityRole | None = None

    @model_serializer(mode="wrap")
    def _drop_unset_role(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Leaving an unset role out keeps the defensive and offensive files' content hashes what they were before
        # utility cooldowns existed.
        data: dict[str, Any] = handler(self)
        if data.get("role") is None:
            data.pop("role", None)
        return data


class CooldownRegistry(_Strict):
    """One category's cooldown file. Every category shares this schema; the file names its own ``category``."""

    category: CooldownCategory
    cooldowns: list[CooldownEntry]

    @model_validator(mode="after")
    def _roles_and_targets_fit_the_category(self) -> CooldownRegistry:
        for entry in self.cooldowns:
            if self.category == "utility" and entry.role is None:
                raise ValueError(f"utility cooldown {entry.spell_id} has no role")
            if self.category != "utility" and entry.role is not None:
                raise ValueError(f"{self.category} cooldown {entry.spell_id} has a role; only utility ones do")
            if self.category != "utility" and entry.target in ENEMY_TARGETS:
                raise ValueError(f"{self.category} cooldown {entry.spell_id} targets enemies; only utility ones can")
        return self

    @model_validator(mode="after")
    def _unique_spell_ids(self) -> CooldownRegistry:
        seen: set[int] = set()
        for entry in self.cooldowns:
            if entry.spell_id in seen:
                raise ValueError(f"duplicate spell_id {entry.spell_id} in {self.category} cooldowns")
            seen.add(entry.spell_id)
        # An aura ID must name one entry, so an aura interval maps back to a single cooldown.
        owner = {entry.spell_id: entry.spell_id for entry in self.cooldowns}
        for entry in self.cooldowns:
            for aura in entry.aura_spell_ids:
                if aura in owner:
                    raise ValueError(
                        f"aura_spell_id {aura} of cooldown {entry.spell_id} is already used by cooldown {owner[aura]}"
                    )
                owner[aura] = entry.spell_id
        return self

    def get(self, spell_id: int) -> CooldownEntry | None:
        return next((c for c in self.cooldowns if c.spell_id == spell_id), None)

    def spell_ids(self, targets: Sequence[CooldownTarget] | None = None) -> list[int]:
        """Cast spell IDs of the entries with these ``targets`` (all entries when None)."""
        return [c.spell_id for c in self.cooldowns if targets is None or c.target in targets]

    def aura_ids(self, targets: Sequence[CooldownTarget] | None = None) -> dict[int, int]:
        """Every spell ID a Buffs row of these entries can carry (cast and ``aura_spell_ids``) -> its entry's spell_id."""
        return {
            aura: c.spell_id
            for c in self.cooldowns
            if targets is None or c.target in targets
            for aura in (c.spell_id, *c.aura_spell_ids)
        }


# ---------------------------------------------------------------------------
# Friendly-damage registry
# ---------------------------------------------------------------------------

FriendlyDamageClass = Literal["redistribution", "delayed_boss", "self_inflicted", "involuntary"]
# Where the ability comes from: a class spell or talent, an expansion-wide borrowed power, or an item (trinkets,
# embellishments), which players may keep using past the season that added it.
FriendlyDamageOrigin = Literal["class", "borrowed_power", "item"]


class FriendlyDamageEntry(_Strict):
    """
    An ability whose damage lands on players: shared out (``redistribution``), boss damage taken late
    (``delayed_boss``), a cost the player chose to pay (``self_inflicted``), or self-damage the player can't
    control, such as a trinket proc (``involuntary``). Matched by ability ID whatever the
    damage row's source, since some are recorded with the Environment as source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    ability_id: int
    name: str
    class_name: FriendlyDamageClass = Field(alias="class")
    origin: FriendlyDamageOrigin | None = None  # None when the records don't show where it comes from
    verified: bool = False


class FriendlyDamageRegistry(_Strict):
    abilities: list[FriendlyDamageEntry]

    @model_validator(mode="after")
    def _unique_ability_ids(self) -> FriendlyDamageRegistry:
        seen: set[int] = set()
        for entry in self.abilities:
            if entry.ability_id in seen:
                raise ValueError(f"duplicate ability_id {entry.ability_id} in friendly-damage registry")
            seen.add(entry.ability_id)
        return self

    def get(self, ability_id: int) -> FriendlyDamageEntry | None:
        return next((e for e in self.abilities if e.ability_id == ability_id), None)

    def ability_ids(self, classes: Sequence[FriendlyDamageClass] | None = None) -> list[int]:
        return [e.ability_id for e in self.abilities if classes is None or e.class_name in classes]


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
    "aura_removed_by",
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
    "aura_removed_by": "ability_ids",
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
    # aura_removed_by: what breaks the aura (may be other mechanics' IDs), auras that confirm the break, and
    # whether a removal at the player's death counts (charged to no one)
    ability_ids: list[int] | None = Field(None, min_length=1)
    followed_by_ids: list[int] | None = Field(None, min_length=1)
    on_death: bool = False
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
        if (self.rule == "aura_removed_by") != (self.ability_ids is not None):
            raise ValueError("'ability_ids' is required by failure rule 'aura_removed_by' and allowed on no other rule")
        if self.rule != "aura_removed_by" and (self.followed_by_ids is not None or self.on_death):
            raise ValueError("'followed_by_ids' and 'on_death' are allowed on failure rule 'aura_removed_by' only")
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
    expected_defensive_cooldowns: int = 1  # defensive raid cooldowns a covered occurrence needs


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


def parse_cooldowns(data: object, category: CooldownCategory, label: str | None = None) -> CooldownRegistry:
    """Validate one cooldown file; its ``category`` must be the one the caller asked for."""
    label = label or f"{category} cooldowns"
    try:
        registry = CooldownRegistry.model_validate(data)
    except ValidationError as e:
        raise ConfigurationError(f"Schema validation failed for {label}:\n{e}") from e
    if registry.category != category:
        raise ConfigurationError(f"{label} is a {registry.category} cooldown file, expected {category}")
    return registry


def parse_friendly_damage(data: object, label: str = "friendly-damage registry") -> FriendlyDamageRegistry:
    try:
        return FriendlyDamageRegistry.model_validate(data)
    except ValidationError as e:
        raise ConfigurationError(f"Schema validation failed for {label}:\n{e}") from e


def parse_encounter(data: object, label: str = "encounter file") -> EncounterFile:
    try:
        return EncounterFile.model_validate(data)
    except ValidationError as e:
        raise ConfigurationError(f"Schema validation failed for {label}:\n{e}") from e


def load_cooldowns(category: CooldownCategory, path: Path | None = None) -> CooldownRegistry:
    """One category's cooldown file: ``config/registries/<category>_cooldowns.yaml`` unless ``path`` is given."""
    defaults = {
        "defensive": DEFAULT_DEFENSIVE_COOLDOWNS_PATH,
        "offensive": DEFAULT_OFFENSIVE_COOLDOWNS_PATH,
        "utility": DEFAULT_UTILITY_COOLDOWNS_PATH,
    }
    path = Path(path) if path else defaults[category]
    return parse_cooldowns(_read_yaml(path), category, str(path))


def check_cooldown_ids(*registries: CooldownRegistry) -> None:
    """
    Raise ``ConfigurationError`` when a cast or aura ID appears in more than one cooldown file.

    A ``cd:`` ref and an aura interval each name one cooldown, and the Buffs filters of different files must not
    fetch the same aura twice, so every ID belongs to exactly one category.
    """
    owner: dict[int, str] = {}
    for registry in registries:
        for aura in registry.aura_ids():
            if aura in owner and owner[aura] != registry.category:
                raise ConfigurationError(
                    f"spell {aura} is in both the {owner[aura]} and the {registry.category} cooldowns"
                )
            owner[aura] = registry.category


def load_friendly_damage(path: Path | None = None) -> FriendlyDamageRegistry:
    path = Path(path) if path else DEFAULT_FRIENDLY_DAMAGE_PATH
    return parse_friendly_damage(_read_yaml(path), str(path))


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


def cooldowns_hash(registry: CooldownRegistry) -> str:
    return _content_hash(registry)


def friendly_damage_hash(registry: FriendlyDamageRegistry) -> str:
    return _content_hash(registry)


def fetch_filter_inputs(
    defensive: CooldownRegistry | None,
    offensive: CooldownRegistry | None,
    encounters: dict[int, EncounterFile],
    *,
    utility: CooldownRegistry | None = None,
) -> dict[str, Any]:
    """
    The ``defensive_spell_ids`` / ``offensive_spell_ids`` / ``enemy_cast_ids`` arguments ``ingest_report``
    expects. Each cooldown file's IDs are one Buffs filter, so they include each entry's ``aura_spell_ids`` as
    well as its cast ID. Raises ``ConfigurationError`` when any two files share an ID.

    The utility file gets no filter: its casts come from the unfiltered friendly Casts request, and nothing reads
    its auras, so it is only checked against the other files.
    """
    check_cooldown_ids(*(r for r in (defensive, offensive, utility) if r is not None))
    return {
        "defensive_spell_ids": list(defensive.aura_ids()) if defensive is not None else None,
        "offensive_spell_ids": list(offensive.aura_ids()) if offensive is not None else None,
        "enemy_cast_ids": {eid: enc.ability_ids() for eid, enc in encounters.items()},
    }
