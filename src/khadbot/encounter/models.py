"""
encounter/models.py

Pydantic models shared across encounter review. Times inside a pull are
fight-relative milliseconds unless a field says otherwise.

Ingest models: Pull, Actor, FetchSpec, IngestResult.
Annotation and analysis models: PhaseSpan, DeathRecord, MechanicStat, CooldownUse, WindowCoverage, PullDigest.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Ingest models
# ---------------------------------------------------------------------------

DataType = Literal["Deaths", "DamageTaken", "Casts", "Buffs", "Debuffs", "Interrupts", "Dispels", "Healing"]


class Pull(BaseModel):
    report_code: str
    fight_id: int
    encounter_id: int
    encounter_name: str
    difficulty: int | None
    kill: bool
    start_ms: int  # report-relative, as WCL reports it
    end_ms: int  # report-relative
    duration_ms: int
    last_phase: int | None
    boss_pct: float | None  # WCL fightPercentage, 0-100
    phase_transitions: list[tuple[int, int]] = []  # (phase id, fight-relative ms), if WCL provides them
    friendly_player_ids: list[int]

    @property
    def pull_key(self) -> str:
        return f"{self.report_code}:{self.fight_id}"


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

    @property
    def dir_name(self) -> str:
        """Raw-cache directory name; enemy data gets a suffix so it never collides with friendly data."""
        return self.data_type if self.hostility == "Friendlies" else f"{self.data_type}-{self.hostility}"


class IngestResult(BaseModel):
    report_code: str
    pulls: list[str]  # pull_keys ingested
    skipped: list[str]  # already ingested with the same events_hash
    failed: dict[str, str]  # pull_key -> error message
    points_spent: float | None  # from WCL rateLimitData, if available


# ---------------------------------------------------------------------------
# Annotation models
# ---------------------------------------------------------------------------


class PhaseSpan(BaseModel):
    phase_id: str
    start_ms: int  # fight-relative, inclusive
    end_ms: int  # fight-relative, exclusive (the next phase's start, or the pull's duration)
