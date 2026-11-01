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
from khadbot.api.schemas.jobs import (
    IngestRequest,
    IngestResponse,
    Job,
    JobErrorCode,
    JobFailure,
    JobPull,
    JobState,
    JobStep,
    PullStatus,
    StepName,
    StepState,
)
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
    CooldownEvidence,
    CooldownMarker,
    DeathEvidence,
    DeathMarker,
    EventEvidence,
    EventsPage,
    Evidence,
    FindingsList,
    MechanicEvidence,
    MechanicMarker,
    PhaseMarker,
    PullEvidence,
    PullTimeline,
    WindowEvidence,
    WindowMarker,
)
from khadbot.api.schemas.raids import (
    BudgetWaitBody,
    CharacterSearch,
    GuildSearch,
    LookupGroup,
    LookupPull,
    LookupZone,
    ReportLookup,
    SearchCharacter,
    SearchGuild,
    ServerEntry,
    ServerList,
    ServerRegion,
)
from khadbot.api.schemas.reports import GroupInfo, PullSummary, ReportDetail, ReportGroup, ReportInfo, ReportList

__all__ = [
    "API_VERSION",
    "BestPull",
    "BriefingLine",
    "BudgetWaitBody",
    "CachedFindings",
    "CharacterSearch",
    "CooldownEvidence",
    "CooldownList",
    "CooldownMarker",
    "CooldownName",
    "Coverage",
    "DeathEvidence",
    "DeathMarker",
    "DifficultyName",
    "EncounterNames",
    "ErrorBody",
    "EventEvidence",
    "EventsPage",
    "Evidence",
    "Features",
    "FindingsList",
    "FirstDeath",
    "GroupInfo",
    "GuildSearch",
    "Health",
    "IngestRequest",
    "IngestResponse",
    "Job",
    "JobErrorCode",
    "JobFailure",
    "JobPull",
    "JobState",
    "JobStep",
    "LookupGroup",
    "LookupPull",
    "LookupZone",
    "MechanicEvidence",
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
    "PullEvidence",
    "PullStatus",
    "PullSummary",
    "PullTimeline",
    "ReportDetail",
    "ReportGroup",
    "ReportInfo",
    "ReportList",
    "ReportLookup",
    "SearchCharacter",
    "SearchGuild",
    "ServerEntry",
    "ServerList",
    "ServerRegion",
    "StepName",
    "StepState",
    "TrendRow",
    "WindowEvidence",
    "WindowMarker",
]
