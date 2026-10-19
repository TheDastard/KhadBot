"""
encounter/models.py

Pydantic models shared across encounter review. Times inside a pull are
fight-relative milliseconds unless a field says otherwise.

Ingest models: WclPhase, Pull, Actor, FetchSpec, IngestResult.
Annotation and analysis models: PhaseSpan, DeathRecord, MechanicStat, MechanicCluster, CooldownUse,
LateCooldown, WindowCoverage, FriendlyDamage, PullDigest.
Analyst contracts: ToolResult, AnalysisRequest, Finding, ProducedBy, AnalysisReport.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Ingest models
# ---------------------------------------------------------------------------

DataType = Literal["Deaths", "DamageTaken", "Casts", "Buffs", "Debuffs", "Interrupts", "Dispels", "Healing", "Threat"]


class WclPhase(BaseModel):
    """One of an encounter's phases as WCL names them (``report.phases``)."""

    id: int
    name: str
    is_intermission: bool = False


class Pull(BaseModel):
    report_code: str
    fight_id: int
    encounter_id: int
    encounter_name: str
    difficulty: int | None
    kill: bool
    start_ms: int  # ms from report start, as WCL reports it
    end_ms: int  # ms from report start
    duration_ms: int
    last_phase: int | None
    boss_pct: float | None  # WCL fightPercentage, 0-100: progress through the whole fight
    boss_hp_pct: float | None = None  # WCL bossPercentage, 0-100: the boss's health
    phase_transitions: list[tuple[int, int]] = []  # (phase id, fight-relative ms), if WCL provides them
    friendly_player_ids: list[int]
    wcl_phases: list[WclPhase] = []  # the encounter's phases from report.phases; names the transition IDs

    @property
    def pull_key(self) -> str:
        return f"{self.report_code}:{self.fight_id}"

    def wcl_phase_reached(self) -> str | None:
        """WCL's name for the last phase transition (its ID as text when unnamed); None without transitions."""
        if not self.phase_transitions:
            return None
        last = self.phase_transitions[-1][0]
        return next((p.name for p in self.wcl_phases if p.id == last), str(last))


class Actor(BaseModel):
    actor_id: int
    name: str
    kind: Literal["player", "pet", "npc", "boss"]
    wcl_type: str | None  # WCL "type" (class for players)
    sub_type: str | None
    owner_id: int | None
    game_id: int | None


class FetchSpec(BaseModel):
    """One WCL events request per data type per pull."""

    data_type: DataType
    hostility: Literal["Friendlies", "Enemies"] = "Friendlies"
    filter_expression: str | None = None
    # Names a second request of the same data type with its own filter (offensive-cooldown Buffs). Its pages share
    # the data type's cache directory, apart by filter hash.
    part: str | None = None

    @property
    def dir_name(self) -> str:
        """Raw-cache directory name; enemy data gets a suffix so it never collides with friendly data."""
        return self.data_type if self.hostility == "Friendlies" else f"{self.data_type}-{self.hostility}"

    @property
    def key(self) -> str:
        """Unique name of this request within a pull: ``dir_name``, plus ``:<part>`` for a second request."""
        return f"{self.dir_name}:{self.part}" if self.part else self.dir_name


class IngestResult(BaseModel):
    report_code: str
    pulls: list[str]  # pull_keys ingested
    skipped: list[str]  # already ingested with the same events_hash, positions_hash and metadata
    failed: dict[str, str]  # pull_key -> error message
    points_spent: float | None  # from WCL rateLimitData, if available


# ---------------------------------------------------------------------------
# Annotation models
# ---------------------------------------------------------------------------


class PhaseSpan(BaseModel):
    phase_id: str
    start_ms: int  # fight-relative, inclusive
    end_ms: int  # fight-relative, exclusive (the next phase's start, or the pull's duration)


# ---------------------------------------------------------------------------
# Analysis records
# ---------------------------------------------------------------------------

Ref = str  # evidence reference: see make_ref here and evidence.parse_ref


def actor_label(names: Mapping[int, str], actor_id: int, instance: int | None = None) -> str:
    """An actor's name, with its WCL instance number when several NPCs share one actor (``Rawling #3``)."""
    name = names.get(actor_id, f"actor-{actor_id}")
    return f"{name} #{instance}" if instance is not None else name


def make_ref(pull_key: str, *parts: object) -> Ref:
    """``ev:<report>:<fight>:<part>:...`` (``pull_key`` is already ``<report>:<fight>``)."""
    return ":".join(["ev", pull_key, *(str(p) for p in parts)])


class Hit(BaseModel):
    t_ms: int
    ability_id: int | None
    ability_name: str | None
    amount: int
    source_name: str | None


class DeathRecord(BaseModel):
    ref: Ref
    actor_id: int
    name: str
    t_ms: int
    phase_id: str | None
    killing_ability_id: int | None
    killing_ability_name: str | None
    overkill: int | None
    time_to_die_ms: int | None  # death t_ms minus last t_ms with hp_pct >= 90; None without hp data
    window_ms: int = 10_000
    window_damage: int  # friendly damage taken by the actor in the window
    window_healing: int | None  # None if Healing was not ingested
    window_self_damage: int = 0  # of window_damage, from self_inflicted friendly-damage registry abilities
    top_hits: list[Hit]  # max 5, by amount desc
    mechanic_ids: list[str]  # mechanics that hit the actor in the window
    active_defensives: list[int]  # registry spell IDs whose aura was active on the actor at death
    after_cutoff: bool


# Where the player a ``per_cluster`` failure is charged to was when the occurrence's first hit landed
# (annotation column ``blame_state``).
BlameState = Literal["beyond_reach", "in_reach", "dead", "alive", "no_position", "no_holder"]


def _empty(value: object) -> bool:
    return not value


def _none(value: object) -> bool:
    return value is None


class MechanicStat(BaseModel):
    ref: Ref
    mechanic_id: str
    clusters: int
    hits: int
    failures: int
    failures_by_player: dict[str, int]  # name -> count, top 10
    damage_taken: int
    deaths_in_window: int  # deaths whose mechanic_ids include this one
    # per_cluster mechanics only, left out of dumps when empty: failures per blame state, and the range of the
    # blamed players' distances from the NPC (yards, [min, max]) per state that has distances.
    blame_states: dict[BlameState, int] = Field(default_factory=dict, exclude_if=_empty)
    blame_dist_yd: dict[BlameState, tuple[float, float]] = Field(default_factory=dict, exclude_if=_empty)


class MechanicCluster(BaseModel):
    """One cluster of a mechanic's hits; resolved as part of the mechanic's ``mech:<id>`` ref."""

    cluster: int  # 1-based cluster_id
    t_ms: int  # first hit
    end_ms: int  # last hit
    phase_id: str | None
    hits: int
    players: int  # distinct players hit
    damage: int
    failures: int  # failure rows among the cluster's hits
    failed_players: list[str]
    # per_cluster mechanics only (left out of dumps elsewhere): the player charged with the cluster's failure,
    # where they were at its first hit, their distance in yards from the NPC, and the NPC's reach.
    blamed: str | None = Field(None, exclude_if=_none)
    blame_state: BlameState | None = Field(None, exclude_if=_none)
    blame_dist_yd: float | None = Field(None, exclude_if=_none)
    blame_reach_yd: float | None = Field(None, exclude_if=_none)


class CooldownUse(BaseModel):
    ref: Ref
    spell_id: int
    spell_name: str
    category: Literal["defensive", "offensive", "utility"]  # the cooldown file the spell is in
    caster: str
    t_ms: int
    phase_id: str | None


class LateCooldown(BaseModel):
    spell_id: int
    late_ms: int  # first aura start minus the window occurrence's first hit


class WindowCoverage(BaseModel):
    ref: Ref
    window_id: str
    occurrence: int  # 1-based
    t_ms: int
    defensive_cooldowns_active: list[int]  # defensive raid cooldown spell IDs whose aura was active at t_ms
    covered: bool  # len(defensive_cooldowns_active) >= expected_defensive_cooldowns
    # Defensive raid cooldowns not active at t_ms whose aura started during the occurrence (see window_coverage).
    defensive_cooldowns_late: list[LateCooldown] = Field(default_factory=list)


class UnregisteredAbility(BaseModel):
    ability_id: int
    name: str | None
    damage: int
    share_pct: float  # of all friendly damage taken in the pull


class AbilityDamage(BaseModel):
    ability_id: int
    name: str | None
    damage: int


class PlayerSelfDamage(BaseModel):
    name: str
    damage: int  # self_inflicted damage the player took
    abilities: list[AbilityDamage]  # top 3 by damage


class FriendlyDamage(BaseModel):
    """
    Damage players took from their own side: friendly-damage registry abilities (whatever the source) and any
    other friendly-source damage the encounter file doesn't name. None of it counts toward the unregistered share.
    """

    damage: int = 0
    share_pct: float = 0.0  # of all friendly damage taken in the pull
    # redistribution / delayed_boss / self_inflicted / involuntary / unclassified -> damage; no zeros
    by_class: dict[str, int] = {}
    self_inflicted: list[PlayerSelfDamage] = []  # cap 5, by damage desc
    unclassified: list[UnregisteredAbility] = []  # friendly-source abilities missing from the registry; cap 5


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


class AggroTarget(BaseModel):
    name: str
    swings: int
    share_pct: float  # of the NPC's melee swings, 1 decimal


class AggroBand(BaseModel):
    """A span during which one NPC instance was attacking one player (or pet), from the Threat data."""

    npc_id: int
    npc_instance: int | None
    player_id: int
    start_ms: int
    end_ms: int
    start: Literal["melee", "taunt"]  # what gave the player aggro


class AggroSummary(BaseModel):
    """Who one boss NPC attacked and who taunted it, from the Threat data."""

    npc: str
    swings: int  # melee swing attempts, including misses
    targets: list[AggroTarget]  # cap 3, by swings desc
    taunts: dict[str, int]  # player name -> taunts that landed on the NPC


class PullDigest(BaseModel):
    pull_key: str
    encounter_name: str
    outcome: Literal["kill", "wipe"]
    duration_ms: int
    phase_reached: str | None
    boss_pct: float | None  # fight progress (fightPercentage)
    boss_hp_pct: float | None = None  # boss health (bossPercentage)
    cutoff_ms: int | None
    deaths: list[DeathRecord]  # cap 10; before-cutoff first
    mechanics: list[MechanicStat]  # cap 8; by failures desc, then damage
    defensive_cooldowns: list[CooldownUse]  # cap 30
    offensive_cooldowns: list[CooldownUse] = []  # cap 15; raid, then external, then personal, kept in time order
    utility_cooldowns: list[CooldownUse] = []  # see UTILITY_COOLDOWN_CAP; raid and enemy-side first, time order
    coverage_windows: list[WindowCoverage]
    unregistered: list[UnregisteredAbility]  # cap 5; boss damage only, friendly damage is in friendly_damage
    friendly_damage: FriendlyDamage = Field(default_factory=FriendlyDamage)
    aggro: list[AggroSummary] = []  # one per boss NPC that swung at a player
    coverage: Literal["full", "partial", "none"]
    anomalies: list[str]
    # events_hash, positions_hash, encfile_hash, defensive_cooldowns_hash, offensive_cooldowns_hash,
    # utility_cooldowns_hash, friendly_damage_hash
    produced_from: dict[str, str]


# ---------------------------------------------------------------------------
# Analyst contracts
# ---------------------------------------------------------------------------

Depth = Literal["quick", "standard", "deep"]
Confidence = Literal["low", "medium", "high"]


class ToolResult(BaseModel):
    """Envelope every analyst query tool returns (as JSON)."""

    ok: bool
    data: list[dict] | dict
    refs: list[Ref]
    truncated: bool = False
    total: int | None = None  # rows before capping
    note: str | None = None  # e.g. "narrow t_from_ms/t_to_ms to see more"


class AnalysisRequest(BaseModel):
    request_id: str  # uuid4 hex
    question: str
    pulls: list[str]  # pull_keys
    depth: Depth = "standard"  # step budget 4 / 8 / 16
    plan_id: str | None = None


class Finding(BaseModel):
    id: str  # f"{request_id[:8]}-{n}"
    claim: str
    refs: list[Ref] = Field(min_length=1)
    confidence: Confidence
    scope: Literal["pull", "cross_pull"]


class ProducedBy(BaseModel):
    model_id: str
    prompt_version: str
    graph_version: str
    encfile_hash: str | None
    events_hashes: dict[str, str]  # pull_key -> events_hash


class AnalysisReport(BaseModel):
    request_id: str
    findings: list[Finding]
    unknowns: list[str]  # questions the data could not answer, with reason
    partial: bool  # step budget exhausted
    rejected: list[str] = []  # findings dropped by verify, with reason
    produced_by: ProducedBy


# ---------------------------------------------------------------------------
# Findings cache
# ---------------------------------------------------------------------------


class PullFindings(BaseModel):
    """The verified findings of one pull's standard review, as cached."""

    pull_key: str
    findings: list[Finding]
    unknowns: list[str]
    produced_by: ProducedBy
    cache_key: str
    created_at: datetime
    flagged: bool = False  # thumbs-down; excluded from synthesis and regenerated on the next review
