"""
api/schemas/

Response models of the dashboard API, one module per area. They are the source of ``contracts/openapi.json``, from
which the frontend's types are generated, so a field change here is a contract change: regenerate it with
``uv run python scripts/dev/gen_contracts.py``.

Records the encounter pipeline already defines (``PullDigest``, ``PullFindings``) are returned as built. Times are
fight-relative milliseconds, pulls are addressed by WCL fight ID, and every ``ref`` is an evidence ref
(``ev:<report>:<fight>:...``) that ``GET /v1/evidence/{ref}`` resolves.

Every model is re-exported here, so callers import from ``khadbot.api.schemas`` whatever module holds it.
"""

from __future__ import annotations

from khadbot.api.schemas.common import API_VERSION, Coverage, DifficultyName, ErrorBody, Outcome
from khadbot.api.schemas.health import Features, Health
from khadbot.api.schemas.jobs import IngestRequest, Job, JobErrorCode, JobFailure, JobPull, JobState, PullStatus
from khadbot.api.schemas.names import (
    CooldownList,
    CooldownName,
    EncounterNames,
    PhaseName,
    Player,
    PlayerClass,
    PlayerList,
)
from khadbot.api.schemas.night import (
    BestPull,
    BriefingLine,
    FirstDeath,
    MostFailedMechanic,
    NightMechanic,
    NightSection,
    NightSummary,
    NightTotals,
    TrendRow,
)
from khadbot.api.schemas.pulls import (
    CachedFindings,
    CooldownMarker,
    DeathMarker,
    EventsPage,
    Evidence,
    FindingsList,
    MechanicMarker,
    PhaseMarker,
    PullTimeline,
    RefKind,
    WindowMarker,
)
from khadbot.api.schemas.raids import BudgetWaitBody, LookupGroup, LookupPull, LookupZone, ReportLookup
from khadbot.api.schemas.reports import GroupInfo, PullSummary, ReportDetail, ReportGroup, ReportInfo, ReportList

__all__ = [
    "API_VERSION",
    "BestPull",
    "BriefingLine",
    "BudgetWaitBody",
    "CachedFindings",
    "CooldownList",
    "CooldownMarker",
    "CooldownName",
    "Coverage",
    "DeathMarker",
    "DifficultyName",
    "EncounterNames",
    "ErrorBody",
    "EventsPage",
    "Evidence",
    "Features",
    "FindingsList",
    "FirstDeath",
    "GroupInfo",
    "Health",
    "IngestRequest",
    "Job",
    "JobErrorCode",
    "JobFailure",
    "JobPull",
    "JobState",
    "LookupGroup",
    "LookupPull",
    "LookupZone",
    "MechanicMarker",
    "MostFailedMechanic",
    "NightMechanic",
    "NightSection",
    "NightSummary",
    "NightTotals",
    "Outcome",
    "PhaseMarker",
    "PhaseName",
    "Player",
    "PlayerClass",
    "PlayerList",
    "PullStatus",
    "PullSummary",
    "PullTimeline",
    "RefKind",
    "ReportDetail",
    "ReportGroup",
    "ReportInfo",
    "ReportList",
    "ReportLookup",
    "TrendRow",
    "WindowMarker",
]
