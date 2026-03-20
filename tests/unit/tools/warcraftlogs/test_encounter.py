"""
Unit tests for the WarcraftLogs encounter analysis tool.

Coverage targets
----------------
Deterministic helpers (no I/O — pure unit tests)
  _analyze_deaths
    - single death with pre-death window
    - multiple deaths sorted by timestamp
    - death with no pre-death window data
    - overkill amount preserved
    - actor name resolved from roster

  _analyze_avoidable
    - registry populated: only matching spell IDs returned
    - registry empty: all entries returned sorted by damage (prototype mode)
    - None table returns empty list
    - sorted descending by total_damage

  _analyze_cooldowns
    - known CD spell IDs extracted from cast stream
    - unknown abilities excluded
    - sorted by fight-relative timestamp
    - caster name resolved from roster
    - non-cast event types excluded (begincast etc.)

  _analyze_healing
    - overheal_pct computed correctly
    - pressure_signal thresholds (overwhelmed / pressured / comfortable)
    - sorted by effective_healing descending
    - None table returns empty list

  _build_timeline
    - deaths and CDs merged and sorted by time_s
    - correct event types and icons (tested via _format_encounter)
    - empty inputs return empty list

fetch_encounter_events
  - happy path: all six queries succeed, analysis fully populated
  - invalid fight_id raises ValueError with available IDs
  - deaths query failure is non-fatal
  - per-death damage window failure is non-fatal (other deaths still processed)
  - avoidable damage failure is non-fatal
  - healing failure is non-fatal
  - cooldown casts failure is non-fatal
  - truncated death stream adds error
  - truncated cooldown stream adds error
  - user_token forwarded to all queries
  - deduplication: same targetID only queries pre-death window once

get_encounter_analysis (@tool)
  - success returns formatted string with expected sections
  - non-integer fight_id returns guidance message
  - invalid fight_id (not in report) returns ValueError message
  - private report returns friendly message
  - auth error returns friendly message
  - API error returns friendly message

_format_encounter
  - kill vs wipe result string
  - deaths section: player name, time, killing blow, overkill, top hits
  - avoidable section: warning when registry empty
  - healing section: overheal %, pressure signal
  - cooldown timeline section
  - merged fight timeline present
  - non-fatal errors in Notes section
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from khadbot.tools.warcraftlogs._client import (
    WarcraftLogsAPIError,
    WarcraftLogsAuthError,
    WarcraftLogsPrivateReportError,
)
from khadbot.tools.warcraftlogs.encounter import (
    AVOIDABLE_SPELL_IDS,
    MAJOR_COOLDOWN_IDS,
    AvoidableDamageEntry,
    CooldownUsage,
    DeathEvent,
    EncounterAnalysis,
    HealerSummary,
    _analyze_avoidable,
    _analyze_cooldowns,
    _analyze_deaths,
    _analyze_healing,
    _build_timeline,
    _format_encounter,
    fetch_encounter_events,
    get_encounter_analysis,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

ACTORS = [
    {"id": 1, "name": "Thrall", "type": "Player", "subType": "Enhancement"},
    {"id": 2, "name": "Jaina", "type": "Player", "subType": "Frost"},
    {"id": 3, "name": "Anduin", "type": "Player", "subType": "Holy"},
]

FIGHT_START_MS = 100_000  # report-absolute start of this fight

FIGHTS_RESPONSE = {
    "reportData": {
        "report": {
            "title": "Sunday Mythic",
            "startTime": 0,
            "endTime": 10_000_000,
            "zone": {"name": "Amirdrassil"},
            "masterData": {"actors": ACTORS},
            "fights": [
                {
                    "id": 3,
                    "name": "Gnarlroot",
                    "difficulty": 5,
                    "kill": False,
                    "startTime": FIGHT_START_MS,
                    "endTime": FIGHT_START_MS + 240_000,
                    "lastPhase": 1,
                    "friendlyPlayers": [1, 2, 3],
                }
            ],
        }
    }
}

DEATHS_RESPONSE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {
                        "type": "death",
                        "timestamp": FIGHT_START_MS + 45_000,
                        "targetID": 1,
                        "overkill": 12_000,
                        "ability": {"name": "Gnarlroot Stomp", "abilityGameID": 9999},
                    },
                    {
                        "type": "death",
                        "timestamp": FIGHT_START_MS + 120_000,
                        "targetID": 2,
                        "overkill": 5_000,
                        "ability": {"name": "Vine Whip", "abilityGameID": 8888},
                    },
                ],
                "nextPageTimestamp": None,
            }
        }
    }
}

DAMAGE_WINDOW_RESPONSE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {
                        "type": "damage",
                        "timestamp": FIGHT_START_MS + 44_000,
                        "targetID": 1,
                        "sourceID": 99,  # environment / boss
                        "amount": 80_000,
                        "ability": {"name": "Gnarlroot Stomp", "abilityGameID": 9999},
                    },
                    {
                        "type": "damage",
                        "timestamp": FIGHT_START_MS + 43_500,
                        "targetID": 1,
                        "sourceID": 99,
                        "amount": 30_000,
                        "ability": {"name": "Scorching Roots", "abilityGameID": 7777},
                    },
                ],
                "nextPageTimestamp": None,
            }
        }
    }
}

AVOIDABLE_RESPONSE = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "entries": [
                        {
                            "name": "Gnarlroot Stomp",
                            "ability": {"id": 9999, "name": "Gnarlroot Stomp"},
                            "total": 500_000,
                            "hitCount": 8,
                            "sources": 4,
                        },
                        {
                            "name": "Scorching Roots",
                            "ability": {"id": 7777, "name": "Scorching Roots"},
                            "total": 200_000,
                            "hitCount": 4,
                            "sources": 2,
                        },
                    ]
                }
            }
        }
    }
}

HEALING_RESPONSE = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "entries": [
                        {
                            "name": "Anduin",
                            "type": "Holy",
                            "total": 3_000_000,
                            "overheal": 300_000,
                        }
                    ]
                }
            }
        }
    }
}

# Use a known CD spell ID from the registry.
_SAMPLE_CD_ID = 33206  # Pain Suppression
_SAMPLE_CD_NAME = MAJOR_COOLDOWN_IDS[_SAMPLE_CD_ID]

COOLDOWN_RESPONSE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {
                        "type": "cast",
                        "timestamp": FIGHT_START_MS + 30_000,
                        "sourceID": 3,
                        "ability": {"name": _SAMPLE_CD_NAME, "abilityGameID": _SAMPLE_CD_ID},
                    },
                    {
                        # Unknown spell — should be filtered out.
                        "type": "cast",
                        "timestamp": FIGHT_START_MS + 35_000,
                        "sourceID": 1,
                        "ability": {"name": "Stormstrike", "abilityGameID": 17364},
                    },
                ],
                "nextPageTimestamp": None,
            }
        }
    }
}


# ---------------------------------------------------------------------------
# Mock client helper
# ---------------------------------------------------------------------------


def _mock_client(*side_effects):
    mock = AsyncMock()
    mock.query = AsyncMock(side_effect=list(side_effects))
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock


# ===========================================================================
# _analyze_deaths
# ===========================================================================


class TestAnalyzeDeaths:
    def test_single_death_populated(self):
        death_events = [
            {
                "timestamp": FIGHT_START_MS + 45_000,
                "targetID": 1,
                "overkill": 12_000,
                "ability": {"name": "Stomp"},
            }
        ]
        window = {
            1: [
                {
                    "timestamp": FIGHT_START_MS + 44_000,
                    "sourceID": 99,
                    "amount": 80_000,
                    "ability": {"name": "Stomp"},
                }
            ]
        }
        result = _analyze_deaths(death_events, window, ACTORS, FIGHT_START_MS)
        assert len(result) == 1
        d = result[0]
        assert d.target_name == "Thrall"
        assert d.killing_blow == "Stomp"
        assert d.overkill == 12_000
        assert len(d.pre_death_hits) == 1
        assert d.pre_death_hits[0]["amount"] == 80_000

    def test_multiple_deaths_sorted_by_timestamp(self):
        death_events = [
            {"timestamp": FIGHT_START_MS + 120_000, "targetID": 2, "overkill": 0, "ability": {"name": "Vine"}},
            {"timestamp": FIGHT_START_MS + 45_000, "targetID": 1, "overkill": 0, "ability": {"name": "Stomp"}},
        ]
        result = _analyze_deaths(death_events, {}, ACTORS, FIGHT_START_MS)
        assert result[0].target_name == "Thrall"  # 45s comes first
        assert result[1].target_name == "Jaina"  # 120s second

    def test_no_pre_death_window_data(self):
        death_events = [
            {"timestamp": FIGHT_START_MS + 10_000, "targetID": 1, "overkill": 5_000, "ability": {"name": "Hit"}},
        ]
        result = _analyze_deaths(death_events, {}, ACTORS, FIGHT_START_MS)
        assert result[0].pre_death_hits == []

    def test_unknown_actor_gets_fallback_name(self):
        death_events = [
            {"timestamp": FIGHT_START_MS + 10_000, "targetID": 999, "overkill": 0, "ability": {"name": "Hit"}},
        ]
        result = _analyze_deaths(death_events, {}, ACTORS, FIGHT_START_MS)
        assert "Actor#999" in result[0].target_name

    def test_fight_relative_timestamp_computed(self):
        death_events = [
            {"timestamp": FIGHT_START_MS + 60_000, "targetID": 1, "overkill": 0, "ability": {"name": "Hit"}},
        ]
        result = _analyze_deaths(death_events, {}, ACTORS, FIGHT_START_MS)
        assert result[0].timestamp_ms == 60_000


# ===========================================================================
# _analyze_avoidable
# ===========================================================================


class TestAnalyzeAvoidable:
    def _table(self, entries):
        return {"data": {"entries": entries}}

    def test_empty_registry_returns_all_sorted_by_damage(self):
        entries = [
            {"name": "A", "ability": {"id": 1}, "total": 100, "hitCount": 1, "sources": 1},
            {"name": "B", "ability": {"id": 2}, "total": 500, "hitCount": 2, "sources": 2},
        ]
        result = _analyze_avoidable(self._table(entries), avoidable_ids=frozenset())
        assert result[0].spell_name == "B"
        assert result[1].spell_name == "A"

    def test_populated_registry_filters_correctly(self):
        entries = [
            {"name": "Avoidable", "ability": {"id": 999}, "total": 300, "hitCount": 3, "sources": 2},
            {"name": "Unavoidable", "ability": {"id": 111}, "total": 900, "hitCount": 9, "sources": 5},
        ]
        result = _analyze_avoidable(self._table(entries), avoidable_ids=frozenset({999}))
        assert len(result) == 1
        assert result[0].spell_name == "Avoidable"

    def test_none_table_returns_empty(self):
        assert _analyze_avoidable(None) == []

    def test_sorted_descending_by_total_damage(self):
        entries = [
            {"name": "C", "ability": {"id": 1}, "total": 10, "hitCount": 1, "sources": 1},
            {"name": "A", "ability": {"id": 2}, "total": 300, "hitCount": 3, "sources": 2},
            {"name": "B", "ability": {"id": 3}, "total": 150, "hitCount": 2, "sources": 1},
        ]
        result = _analyze_avoidable(self._table(entries), avoidable_ids=frozenset())
        totals = [e.total_damage for e in result]
        assert totals == sorted(totals, reverse=True)


# ===========================================================================
# _analyze_cooldowns
# ===========================================================================


class TestAnalyzeCooldowns:
    def test_known_cd_extracted(self):
        cast_events = [
            {
                "type": "cast",
                "timestamp": FIGHT_START_MS + 30_000,
                "sourceID": 3,
                "ability": {"name": _SAMPLE_CD_NAME, "abilityGameID": _SAMPLE_CD_ID},
            }
        ]
        result = _analyze_cooldowns(cast_events, ACTORS, FIGHT_START_MS)
        assert len(result) == 1
        assert result[0].spell_name == _SAMPLE_CD_NAME
        assert result[0].caster_name == "Anduin"
        assert result[0].fight_relative_seconds == 30.0

    def test_unknown_spell_excluded(self):
        cast_events = [
            {
                "type": "cast",
                "timestamp": FIGHT_START_MS + 5_000,
                "sourceID": 1,
                "ability": {"name": "Stormstrike", "abilityGameID": 17364},
            }
        ]
        result = _analyze_cooldowns(cast_events, ACTORS, FIGHT_START_MS)
        assert result == []

    def test_begincast_events_excluded(self):
        cast_events = [
            {
                "type": "begincast",
                "timestamp": FIGHT_START_MS + 5_000,
                "sourceID": 3,
                "ability": {"name": _SAMPLE_CD_NAME, "abilityGameID": _SAMPLE_CD_ID},
            }
        ]
        result = _analyze_cooldowns(cast_events, ACTORS, FIGHT_START_MS)
        assert result == []

    def test_sorted_by_timestamp(self):
        cast_events = [
            {
                "type": "cast",
                "timestamp": FIGHT_START_MS + 90_000,
                "sourceID": 3,
                "ability": {"name": _SAMPLE_CD_NAME, "abilityGameID": _SAMPLE_CD_ID},
            },
            {
                "type": "cast",
                "timestamp": FIGHT_START_MS + 30_000,
                "sourceID": 3,
                "ability": {"name": _SAMPLE_CD_NAME, "abilityGameID": _SAMPLE_CD_ID},
            },
        ]
        result = _analyze_cooldowns(cast_events, ACTORS, FIGHT_START_MS)
        assert result[0].fight_relative_seconds < result[1].fight_relative_seconds


# ===========================================================================
# _analyze_healing
# ===========================================================================


class TestAnalyzeHealing:
    def test_overheal_pct_computed_correctly(self):
        table = {"data": {"entries": [{"name": "Anduin", "type": "Holy", "total": 900, "overheal": 100}]}}
        result = _analyze_healing(table, ACTORS)
        assert result[0].overheal_pct == 10.0

    def test_pressure_signal_overwhelmed(self):
        h = HealerSummary("A", "Holy", effective_healing=1000, overhealing=100)
        assert h.pressure_signal == "overwhelmed (very low overheal)"

    def test_pressure_signal_pressured(self):
        h = HealerSummary("A", "Holy", effective_healing=800, overhealing=200)
        # overheal = 200/1000 = 20% → pressured
        assert h.pressure_signal == "pressured"

    def test_pressure_signal_comfortable(self):
        h = HealerSummary("A", "Holy", effective_healing=600, overhealing=400)
        # overheal = 400/1000 = 40% → comfortable
        assert h.pressure_signal == "comfortable"

    def test_sorted_descending_by_effective_healing(self):
        table = {
            "data": {
                "entries": [
                    {"name": "A", "type": "Holy", "total": 100, "overheal": 10},
                    {"name": "B", "type": "Resto", "total": 500, "overheal": 50},
                ]
            }
        }
        result = _analyze_healing(table, ACTORS)
        assert result[0].effective_healing > result[1].effective_healing

    def test_none_table_returns_empty(self):
        assert _analyze_healing(None, ACTORS) == []

    def test_zero_total_no_division_error(self):
        h = HealerSummary("A", "Holy", effective_healing=0, overhealing=0)
        assert h.overheal_pct == 0.0


# ===========================================================================
# _build_timeline
# ===========================================================================


class TestBuildTimeline:
    def test_deaths_and_cds_merged_sorted(self):
        deaths = [
            DeathEvent(
                timestamp_ms=60_000,
                target_id=1,
                target_name="Thrall",
                killing_blow="Stomp",
                overkill=5000,
            )
        ]
        cds = [
            CooldownUsage(
                spell_id=_SAMPLE_CD_ID,
                spell_name=_SAMPLE_CD_NAME,
                caster_id=3,
                caster_name="Anduin",
                timestamp_ms=30_000,
                fight_relative_seconds=30.0,
            )
        ]
        timeline = _build_timeline(deaths, cds, 240.0)
        assert len(timeline) == 2
        assert timeline[0]["time_s"] == 30.0  # CD first
        assert timeline[1]["time_s"] == 60.0  # death second
        assert timeline[0]["type"] == "cooldown"
        assert timeline[1]["type"] == "death"

    def test_empty_inputs_return_empty(self):
        assert _build_timeline([], [], 240.0) == []


# ===========================================================================
# fetch_encounter_events
# ===========================================================================


def _make_full_mock(monkeypatch):
    """Patch _make_client to return a mock cycling through all six responses."""
    # fetch_encounter_events makes: fights, deaths, N×damage_window, avoidable,
    # healing, cooldowns.  With 2 deaths → 2 damage window queries → 6 total.
    cm, mock = _mock_client(
        FIGHTS_RESPONSE,
        DEATHS_RESPONSE,
        DAMAGE_WINDOW_RESPONSE,  # window for targetID=1
        DAMAGE_WINDOW_RESPONSE,  # window for targetID=2
        AVOIDABLE_RESPONSE,
        HEALING_RESPONSE,
        COOLDOWN_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)
    return cm, mock


@pytest.mark.asyncio
async def test_fetch_happy_path(monkeypatch):
    _, mock = _make_full_mock(monkeypatch)
    result = await fetch_encounter_events("abc123", fight_id=3)

    assert result.fight_name == "Gnarlroot"
    assert result.fight_id == 3
    assert result.kill is False
    assert len(result.deaths) == 2
    assert result.deaths[0].target_name == "Thrall"
    assert result.deaths[1].target_name == "Jaina"
    assert len(result.avoidable_damage) == 2
    assert len(result.healers) == 1
    assert len(result.cooldown_timeline) == 1  # Stormstrike filtered out
    assert result.errors == []


@pytest.mark.asyncio
async def test_fetch_invalid_fight_id_raises(monkeypatch):
    cm, _ = _mock_client(FIGHTS_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)

    with pytest.raises(ValueError, match="Fight ID 99 not found"):
        await fetch_encounter_events("abc123", fight_id=99)


@pytest.mark.asyncio
async def test_fetch_deaths_failure_is_non_fatal(monkeypatch):
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        WarcraftLogsAPIError("deaths down"),
        AVOIDABLE_RESPONSE,
        HEALING_RESPONSE,
        COOLDOWN_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)

    result = await fetch_encounter_events("abc123", fight_id=3)
    assert result.deaths == []
    assert any("Death events" in e for e in result.errors)


@pytest.mark.asyncio
async def test_fetch_avoidable_failure_is_non_fatal(monkeypatch):
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        DEATHS_RESPONSE,
        DAMAGE_WINDOW_RESPONSE,
        DAMAGE_WINDOW_RESPONSE,
        WarcraftLogsAPIError("avoidable down"),
        HEALING_RESPONSE,
        COOLDOWN_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)

    result = await fetch_encounter_events("abc123", fight_id=3)
    assert result.avoidable_damage == []
    assert any("Avoidable" in e for e in result.errors)
    assert result.healers != []  # healing still populated


@pytest.mark.asyncio
async def test_fetch_healing_failure_is_non_fatal(monkeypatch):
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        DEATHS_RESPONSE,
        DAMAGE_WINDOW_RESPONSE,
        DAMAGE_WINDOW_RESPONSE,
        AVOIDABLE_RESPONSE,
        WarcraftLogsAPIError("healing down"),
        COOLDOWN_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)

    result = await fetch_encounter_events("abc123", fight_id=3)
    assert result.healers == []
    assert any("Healing" in e for e in result.errors)


@pytest.mark.asyncio
async def test_fetch_cooldowns_failure_is_non_fatal(monkeypatch):
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        DEATHS_RESPONSE,
        DAMAGE_WINDOW_RESPONSE,
        DAMAGE_WINDOW_RESPONSE,
        AVOIDABLE_RESPONSE,
        HEALING_RESPONSE,
        WarcraftLogsAPIError("casts down"),
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)

    result = await fetch_encounter_events("abc123", fight_id=3)
    assert result.cooldown_timeline == []
    assert any("Cooldown" in e for e in result.errors)


@pytest.mark.asyncio
async def test_fetch_pre_death_window_failure_is_non_fatal(monkeypatch):
    """One player's window fails — other deaths still processed."""
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        DEATHS_RESPONSE,
        WarcraftLogsAPIError("window failed"),  # Thrall's window
        DAMAGE_WINDOW_RESPONSE,  # Jaina's window succeeds
        AVOIDABLE_RESPONSE,
        HEALING_RESPONSE,
        COOLDOWN_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)

    result = await fetch_encounter_events("abc123", fight_id=3)
    # Both deaths still present — window failure is non-fatal.
    assert len(result.deaths) == 2
    assert any("Pre-death window" in e for e in result.errors)
    # Thrall's pre-death hits will be empty; Jaina's will be populated.
    thrall = next(d for d in result.deaths if d.target_name == "Thrall")
    jaina = next(d for d in result.deaths if d.target_name == "Jaina")
    assert thrall.pre_death_hits == []
    assert len(jaina.pre_death_hits) > 0


@pytest.mark.asyncio
async def test_fetch_deduplicates_damage_windows(monkeypatch):
    """Same targetID appearing twice should only trigger one window query."""
    duplicate_deaths = {
        "reportData": {
            "report": {
                "events": {
                    "data": [
                        # Thrall dies twice.
                        {
                            "timestamp": FIGHT_START_MS + 45_000,
                            "targetID": 1,
                            "overkill": 5000,
                            "ability": {"name": "Hit"},
                        },
                        {
                            "timestamp": FIGHT_START_MS + 180_000,
                            "targetID": 1,
                            "overkill": 8000,
                            "ability": {"name": "Hit2"},
                        },
                    ],
                    "nextPageTimestamp": None,
                }
            }
        }
    }
    cm, mock = _mock_client(
        FIGHTS_RESPONSE,
        duplicate_deaths,
        DAMAGE_WINDOW_RESPONSE,  # only one window query for Thrall
        AVOIDABLE_RESPONSE,
        HEALING_RESPONSE,
        COOLDOWN_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)

    await fetch_encounter_events("abc123", fight_id=3)

    # query called: fights + deaths + 1 window (not 2) + avoidable + healing + cds
    assert mock.query.call_count == 6


@pytest.mark.asyncio
async def test_fetch_truncated_death_stream_adds_error(monkeypatch):
    truncated_deaths = {
        "reportData": {
            "report": {
                "events": {
                    "data": [],
                    "nextPageTimestamp": 999_999,
                }
            }
        }
    }
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        truncated_deaths,
        AVOIDABLE_RESPONSE,
        HEALING_RESPONSE,
        COOLDOWN_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter._make_client", lambda: cm)

    result = await fetch_encounter_events("abc123", fight_id=3)
    assert any("truncated" in e.lower() for e in result.errors)


@pytest.mark.asyncio
async def test_fetch_user_token_forwarded(monkeypatch):
    cm, mock = _make_full_mock(monkeypatch)

    await fetch_encounter_events("abc123", fight_id=3, user_token="my-token")

    for call in mock.query.call_args_list:
        assert call.kwargs.get("user_token") == "my-token", f"Expected user_token in call kwargs: {call.kwargs}"


# ===========================================================================
# get_encounter_analysis (@tool)
# ===========================================================================


def _bare_analysis(**overrides) -> EncounterAnalysis:
    base = EncounterAnalysis(
        fight_id=3,
        fight_name="Gnarlroot",
        fight_duration_seconds=240.0,
        kill=False,
        last_phase=1,
        deaths=[],
        avoidable_damage=[],
        cooldown_timeline=[],
        healers=[],
        errors=[],
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


@pytest.mark.asyncio
async def test_tool_success_returns_formatted_string(monkeypatch):
    async def fake_fetch(code, fight_id, user_token=None):
        return _bare_analysis(
            deaths=[
                DeathEvent(
                    timestamp_ms=45_000,
                    target_id=1,
                    target_name="Thrall",
                    killing_blow="Stomp",
                    overkill=12_000,
                )
            ],
        )

    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter.fetch_encounter_events", fake_fetch)

    result = await get_encounter_analysis.ainvoke({"report_id": "abc", "fight_id": "3", "user_token": ""})
    assert "Gnarlroot" in result
    assert "Thrall" in result
    assert "Stomp" in result


@pytest.mark.asyncio
async def test_tool_non_integer_fight_id_returns_guidance(monkeypatch):
    result = await get_encounter_analysis.ainvoke({"report_id": "abc", "fight_id": "last", "user_token": ""})
    assert "single integer" in result.lower()


@pytest.mark.asyncio
async def test_tool_invalid_fight_id_returns_value_error_message(monkeypatch):
    async def fake_fetch(code, fight_id, user_token=None):
        raise ValueError("Fight ID 99 not found in report 'abc'. Available: 3")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter.fetch_encounter_events", fake_fetch)

    result = await get_encounter_analysis.ainvoke({"report_id": "abc", "fight_id": "99"})
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_tool_private_report_friendly_message(monkeypatch):
    async def fake_fetch(code, fight_id, user_token=None):
        raise WarcraftLogsPrivateReportError("private")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter.fetch_encounter_events", fake_fetch)

    result = await get_encounter_analysis.ainvoke({"report_id": "abc", "fight_id": "3"})
    assert "private" in result.lower()


@pytest.mark.asyncio
async def test_tool_auth_error_friendly_message(monkeypatch):
    async def fake_fetch(code, fight_id, user_token=None):
        raise WarcraftLogsAuthError("bad token")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.encounter.fetch_encounter_events", fake_fetch)

    result = await get_encounter_analysis.ainvoke({"report_id": "abc", "fight_id": "3"})
    assert "authentication" in result.lower()


# ===========================================================================
# _format_encounter
# ===========================================================================


class TestFormatEncounter:
    def test_kill_result_string(self):
        a = _bare_analysis(kill=True)
        assert "✓ Kill" in _format_encounter(a)

    def test_wipe_result_string(self):
        a = _bare_analysis(kill=False, last_phase=2)
        assert "✗ Wipe (phase 2)" in _format_encounter(a)

    def test_death_section_contains_player_and_killing_blow(self):
        a = _bare_analysis(
            deaths=[
                DeathEvent(
                    timestamp_ms=45_000,
                    target_id=1,
                    target_name="Thrall",
                    killing_blow="Gnarlroot Stomp",
                    overkill=12_000,
                )
            ]
        )
        text = _format_encounter(a)
        assert "Thrall" in text
        assert "Gnarlroot Stomp" in text
        assert "12,000" in text

    def test_pre_death_hits_shown(self):
        d = DeathEvent(
            timestamp_ms=45_000,
            target_id=1,
            target_name="Thrall",
            killing_blow="Stomp",
            overkill=0,
            pre_death_hits=[
                {"ability": "Scorching Roots", "amount": 80_000, "ts_relative_s": 44.5, "source": "Gnarlroot"},
            ],
        )
        a = _bare_analysis(deaths=[d])
        text = _format_encounter(a)
        assert "Scorching Roots" in text
        assert "80,000" in text

    def test_avoidable_registry_empty_warning_shown(self):
        a = _bare_analysis(avoidable_damage=[AvoidableDamageEntry(1, "Stomp", 500_000, 8, 4)])
        text = _format_encounter(a)
        # Registry is empty in prototype → warning should appear.
        if not AVOIDABLE_SPELL_IDS:
            assert "registry is empty" in text.lower()

    def test_healing_pressure_signal_shown(self):
        a = _bare_analysis(healers=[HealerSummary("Anduin", "Holy", 3_000_000, 100_000)])
        text = _format_encounter(a)
        assert "Anduin" in text
        assert "overheal" in text.lower()

    def test_cooldown_timeline_shown(self):
        a = _bare_analysis(
            cooldown_timeline=[
                CooldownUsage(
                    spell_id=_SAMPLE_CD_ID,
                    spell_name=_SAMPLE_CD_NAME,
                    caster_id=3,
                    caster_name="Anduin",
                    timestamp_ms=30_000,
                    fight_relative_seconds=30.0,
                )
            ]
        )
        text = _format_encounter(a)
        assert _SAMPLE_CD_NAME in text
        assert "Anduin" in text

    def test_merged_timeline_present(self):
        d = DeathEvent(
            timestamp_ms=60_000,
            target_id=1,
            target_name="Thrall",
            killing_blow="Stomp",
            overkill=0,
        )
        cd = CooldownUsage(
            spell_id=_SAMPLE_CD_ID,
            spell_name=_SAMPLE_CD_NAME,
            caster_id=3,
            caster_name="Anduin",
            timestamp_ms=30_000,
            fight_relative_seconds=30.0,
        )
        a = _bare_analysis(deaths=[d], cooldown_timeline=[cd])
        text = _format_encounter(a)
        assert "Fight timeline" in text
        assert "💀" in text
        assert "🛡" in text

    def test_non_fatal_errors_shown(self):
        a = _bare_analysis(errors=["Pre-death window unavailable for Thrall: 503"])
        text = _format_encounter(a)
        assert "⚠" in text
        assert "Thrall" in text
