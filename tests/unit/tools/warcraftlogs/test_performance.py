"""
Unit tests for the WarcraftLogs client and tool.

Coverage targets
----------------
Client
  - Client-credentials token fetch, caching, and expiry refresh
  - User-token routing to /user endpoint vs /client endpoint
  - Private report → WarcraftLogsPrivateReportError
  - Auth failure → WarcraftLogsAuthError
  - GraphQL error → WarcraftLogsAPIError
  - HTTP 500 → WarcraftLogsAPIError
  - Network error on token fetch → WarcraftLogsAuthError

Helpers
  - _find_actor: found, not found, case-insensitive
  - _summarise_casts: normal counts, cancelled detection, empty input

fetch_report_summary
  - Happy path: all five queries succeed
  - fight_ids filter applied to fight list
  - player_name resolves source_id and triggers ability/cast queries
  - player_name not found adds error, ability/cast queries skipped
  - damage table failure is non-fatal
  - rankings failure is non-fatal
  - cast events truncated → truncated flag + error
  - user_token forwarded to all queries

get_warcraftlogs_report (@tool)
  - Success returns formatted string
  - Private report → friendly message
  - Auth error → friendly message
  - Invalid fight_ids → guidance message
  - Malformed response → friendly message
  - player_name and user_token passed through correctly

_format_summary
  - Kill / wipe indicators
  - Damage table truncated to 15 entries
  - Ability breakdown section present when resolved_player set
  - Cast summary section present
  - Non-fatal errors surfaced
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from khadbot.tools.warcraftlogs._client import (
    GRAPHQL_CLIENT_URL,
    GRAPHQL_USER_URL,
    TOKEN_URL,
    WarcraftLogsAPIError,
    WarcraftLogsAuthError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
)
from khadbot.tools.warcraftlogs.performance import (
    _find_actor,
    _format_summary,
    _summarise_casts,
    fetch_report_summary,
    get_warcraftlogs_report,
)

# ---------------------------------------------------------------------------
# Shared fixtures / response stubs
# ---------------------------------------------------------------------------

TOKEN_RESPONSE = {"access_token": "test-token", "expires_in": 3600, "token_type": "bearer"}

FIGHTS_RESPONSE = {
    "reportData": {
        "report": {
            "title": "Sunday Mythic Split",
            "startTime": 1_700_000_000_000,
            "endTime": 1_700_010_000_000,
            "zone": {"name": "Amirdrassil"},
            "masterData": {
                "actors": [
                    {"id": 1, "name": "Thrall", "type": "Player", "subType": "Enhancement"},
                    {"id": 2, "name": "Jaina", "type": "Player", "subType": "Frost"},
                ]
            },
            "fights": [
                {
                    "id": 1,
                    "name": "Gnarlroot",
                    "difficulty": 5,
                    "kill": True,
                    "startTime": 100_000,
                    "endTime": 340_000,
                    "lastPhase": 2,
                    "friendlyPlayers": [1, 2],
                },
                {
                    "id": 2,
                    "name": "Igira the Cruel",
                    "difficulty": 5,
                    "kill": False,
                    "startTime": 400_000,
                    "endTime": 580_000,
                    "lastPhase": 1,
                    "friendlyPlayers": [1, 2],
                },
            ],
        }
    }
}

DAMAGE_TABLE_RESPONSE = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "entries": [
                        {
                            "name": "Thrall",
                            "type": "Enhancement",
                            "total": 5_000_000,
                            "activeTime": 230_000,
                            "pct": 18.4,
                        },
                        {
                            "name": "Jaina",
                            "type": "Frost",
                            "total": 4_500_000,
                            "activeTime": 235_000,
                            "pct": 16.5,
                        },
                    ]
                }
            }
        }
    }
}

ABILITY_TABLE_RESPONSE = {
    "reportData": {
        "report": {
            "table": {
                "data": {
                    "entries": [
                        {
                            "name": "Stormstrike",
                            "total": 2_000_000,
                            "uses": 24,
                            "hitCount": 48,
                        },
                        {
                            "name": "Lava Lash",
                            "total": 1_200_000,
                            "uses": 18,
                            "hitCount": 18,
                        },
                    ]
                }
            }
        }
    }
}

RANKINGS_RESPONSE = {
    "reportData": {
        "report": {
            "rankings": {
                "data": [
                    {
                        "name": "Thrall",
                        "spec": "Enhancement",
                        "rankPercent": 83.4,
                        "allStars": {"points": 42.1},
                    },
                    {
                        "name": "Jaina",
                        "spec": "Frost",
                        "rankPercent": 71.0,
                        "allStars": None,
                    },
                ]
            }
        }
    }
}

CAST_EVENTS_RESPONSE = {
    "reportData": {
        "report": {
            "events": {
                "data": [
                    {"type": "cast", "ability": {"name": "Stormstrike"}},
                    {"type": "cast", "ability": {"name": "Stormstrike"}},
                    {"type": "begincast", "ability": {"name": "Healing Surge"}},
                    {"type": "cast", "ability": {"name": "Lava Lash"}},
                    # begincast without matching cast → cancelled
                    {"type": "begincast", "ability": {"name": "Healing Surge"}},
                ],
                "nextPageTimestamp": None,
            }
        }
    }
}

PRIVATE_REPORT_RESPONSE_HTTP = {"errors": [{"message": "You do not have permission to view this private log."}]}

GRAPHQL_ERROR_RESPONSE_HTTP = {"errors": [{"message": "Report not found."}]}


# ---------------------------------------------------------------------------
# Helper: build a mock WarcraftLogsClient context manager
# ---------------------------------------------------------------------------


def _mock_client(*side_effects):
    """
    Return an async context manager whose .query() raises or returns each
    value in side_effects in order.
    """
    mock = AsyncMock()
    mock.query = AsyncMock(side_effect=list(side_effects))
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock


# ===========================================================================
# WarcraftLogsClient tests
# ===========================================================================


class TestClientCredentialsFlow:
    @respx.mock
    @pytest.mark.asyncio
    async def test_token_fetched_on_first_query(self):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json={"data": FIGHTS_RESPONSE}))
        async with WarcraftLogsClient("id", "secret") as client:
            data = await client.query('{ reportData { report(code: "abc") { title } } }')
        assert data == FIGHTS_RESPONSE

    @respx.mock
    @pytest.mark.asyncio
    async def test_token_cached_across_calls(self):
        token_route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        async with WarcraftLogsClient("id", "secret") as client:
            await client.query("q1")
            await client.query("q2")
        assert token_route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_expired_token_is_refreshed(self):
        token_route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        async with WarcraftLogsClient("id", "secret") as client:
            await client.query("q1")
            client._token_cache.expires_at = time.time() - 1  # force expiry
            await client.query("q2")
        assert token_route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_bad_credentials_raises_auth_error(self):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(WarcraftLogsAuthError):
            async with WarcraftLogsClient("bad", "creds") as client:
                await client.query("q")

    @respx.mock
    @pytest.mark.asyncio
    async def test_network_error_on_token_raises_auth_error(self):
        respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(WarcraftLogsAuthError, match="Network error"):
            async with WarcraftLogsClient("id", "secret") as client:
                await client.query("q")


class TestUserTokenRouting:
    @respx.mock
    @pytest.mark.asyncio
    async def test_user_token_hits_user_endpoint(self):
        """When user_token is supplied the request must go to /user, not /client."""
        user_route = respx.post(GRAPHQL_USER_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        client_route = respx.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        # No token endpoint needed — client creds not used.
        async with WarcraftLogsClient("id", "secret") as client:
            # Inject a fake valid client token so _ensure_client_token isn't called.
            client._token_cache.access_token = "existing"
            client._token_cache.expires_at = time.time() + 3600
            await client.query("q", user_token="user-abc")

        assert user_route.call_count == 1
        assert client_route.call_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_user_token_hits_client_endpoint(self):
        """Without user_token the request must go to /client."""
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        client_route = respx.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json={"data": {}}))
        async with WarcraftLogsClient("id", "secret") as client:
            await client.query("q")
        assert client_route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_user_token_used_as_bearer(self):
        """The Authorization header must carry the supplied user token."""
        captured_headers: dict = {}

        def capture(request, **_):
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={"data": {}})

        respx.post(GRAPHQL_USER_URL).mock(side_effect=capture)
        async with WarcraftLogsClient("id", "secret") as client:
            client._token_cache.access_token = "client-token"
            client._token_cache.expires_at = time.time() + 3600
            await client.query("q", user_token="my-user-token")

        assert captured_headers.get("authorization") == "Bearer my-user-token"


class TestGraphQLErrors:
    @respx.mock
    @pytest.mark.asyncio
    async def test_private_report_raises_private_error(self):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json=PRIVATE_REPORT_RESPONSE_HTTP))
        with pytest.raises(WarcraftLogsPrivateReportError):
            async with WarcraftLogsClient("id", "secret") as client:
                await client.query("q")

    @respx.mock
    @pytest.mark.asyncio
    async def test_generic_graphql_error_raises_api_error(self):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(200, json=GRAPHQL_ERROR_RESPONSE_HTTP))
        with pytest.raises(WarcraftLogsAPIError, match="Report not found"):
            async with WarcraftLogsClient("id", "secret") as client:
                await client.query("q")

    @respx.mock
    @pytest.mark.asyncio
    async def test_http_500_raises_api_error(self):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(GRAPHQL_CLIENT_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(WarcraftLogsAPIError, match="500"):
            async with WarcraftLogsClient("id", "secret") as client:
                await client.query("q")


# ===========================================================================
# Helper function tests
# ===========================================================================


class TestFindActor:
    def test_found_exact(self):
        actors = [{"id": 1, "name": "Thrall"}, {"id": 2, "name": "Jaina"}]
        assert _find_actor(actors, "Thrall")["id"] == 1

    def test_found_case_insensitive(self):
        actors = [{"id": 1, "name": "Thrall"}]
        assert _find_actor(actors, "thrall") is not None

    def test_not_found_returns_none(self):
        actors = [{"id": 1, "name": "Thrall"}]
        assert _find_actor(actors, "Arthas") is None

    def test_empty_roster(self):
        assert _find_actor([], "anyone") is None


class TestSummariseCasts:
    def test_counts_completed_casts(self):
        events = [
            {"type": "cast", "ability": {"name": "Stormstrike"}},
            {"type": "cast", "ability": {"name": "Stormstrike"}},
            {"type": "cast", "ability": {"name": "Lava Lash"}},
        ]
        result = _summarise_casts(events)
        assert result["cast_counts"]["Stormstrike"] == 2
        assert result["cast_counts"]["Lava Lash"] == 1
        assert result["total_casts"] == 3

    def test_detects_cancelled_casts(self):
        events = [
            {"type": "begincast", "ability": {"name": "Healing Surge"}},
            {"type": "begincast", "ability": {"name": "Healing Surge"}},
            # Only one completion:
            {"type": "cast", "ability": {"name": "Healing Surge"}},
        ]
        result = _summarise_casts(events)
        assert result["cancelled_casts"].get("Healing Surge") == 1

    def test_no_cancelled_when_all_complete(self):
        events = [
            {"type": "begincast", "ability": {"name": "Fireball"}},
            {"type": "cast", "ability": {"name": "Fireball"}},
        ]
        result = _summarise_casts(events)
        assert result["cancelled_casts"] == {}

    def test_empty_events(self):
        result = _summarise_casts([])
        assert result["total_casts"] == 0
        assert result["cast_counts"] == {}
        assert result["cancelled_casts"] == {}

    def test_cast_counts_sorted_descending(self):
        events = [
            {"type": "cast", "ability": {"name": "B"}},
            {"type": "cast", "ability": {"name": "A"}},
            {"type": "cast", "ability": {"name": "A"}},
            {"type": "cast", "ability": {"name": "A"}},
        ]
        result = _summarise_casts(events)
        keys = list(result["cast_counts"].keys())
        assert keys[0] == "A"


# ===========================================================================
# fetch_report_summary tests
# ===========================================================================


@pytest.mark.asyncio
async def test_happy_path_all_queries_succeed(monkeypatch):
    cm, mock = _mock_client(
        FIGHTS_RESPONSE,
        DAMAGE_TABLE_RESPONSE,
        ABILITY_TABLE_RESPONSE,
        RANKINGS_RESPONSE,
        CAST_EVENTS_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance._make_client", lambda: cm)

    summary = await fetch_report_summary("abc123", player_name="Thrall")

    assert summary["title"] == "Sunday Mythic Split"
    assert summary["zone"] == "Amirdrassil"
    assert len(summary["fights"]) == 2
    assert summary["resolved_player"]["name"] == "Thrall"
    assert summary["damage_table"] is not None
    assert summary["ability_breakdown"] is not None
    assert summary["cast_summary"] is not None
    assert summary["rankings"] is not None
    assert summary["errors"] == []


@pytest.mark.asyncio
async def test_fight_ids_filter_applied(monkeypatch):
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        DAMAGE_TABLE_RESPONSE,
        RANKINGS_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance._make_client", lambda: cm)

    summary = await fetch_report_summary("abc123", fight_ids=[1])

    assert len(summary["fights"]) == 1
    assert summary["fights"][0]["id"] == 1


@pytest.mark.asyncio
async def test_player_not_found_adds_error_and_skips_player_queries(monkeypatch):
    cm, mock = _mock_client(
        FIGHTS_RESPONSE,
        DAMAGE_TABLE_RESPONSE,
        RANKINGS_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance._make_client", lambda: cm)

    summary = await fetch_report_summary("abc123", player_name="Arthas")

    assert summary["ability_breakdown"] is None
    assert summary["cast_summary"] is None
    assert any("Arthas" in e for e in summary["errors"])
    # query should only have been called 3 times (fights, dmg, rankings)
    assert mock.query.call_count == 3


@pytest.mark.asyncio
async def test_damage_table_failure_is_non_fatal(monkeypatch):
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        WarcraftLogsAPIError("upstream 503"),
        RANKINGS_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance._make_client", lambda: cm)

    summary = await fetch_report_summary("abc123")

    assert summary["damage_table"] is None
    assert any("Damage table" in e for e in summary["errors"])
    assert summary["rankings"] is not None


@pytest.mark.asyncio
async def test_rankings_failure_is_non_fatal(monkeypatch):
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        DAMAGE_TABLE_RESPONSE,
        WarcraftLogsAPIError("rankings down"),
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance._make_client", lambda: cm)

    summary = await fetch_report_summary("abc123")

    assert summary["rankings"] is None
    assert any("Rankings" in e for e in summary["errors"])
    assert summary["damage_table"] is not None


@pytest.mark.asyncio
async def test_cast_events_truncated_flag(monkeypatch):
    truncated_cast_response = {
        "reportData": {
            "report": {
                "events": {
                    "data": [{"type": "cast", "ability": {"name": "Stormstrike"}}],
                    "nextPageTimestamp": 1_700_001_000_000,  # signals more pages
                }
            }
        }
    }
    cm, _ = _mock_client(
        FIGHTS_RESPONSE,
        DAMAGE_TABLE_RESPONSE,
        ABILITY_TABLE_RESPONSE,
        RANKINGS_RESPONSE,
        truncated_cast_response,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance._make_client", lambda: cm)

    summary = await fetch_report_summary("abc123", player_name="Thrall")

    assert summary["cast_summary"]["truncated"] is True
    assert any("truncated" in e.lower() for e in summary["errors"])


@pytest.mark.asyncio
async def test_user_token_forwarded_to_all_queries(monkeypatch):
    cm, mock = _mock_client(
        FIGHTS_RESPONSE,
        DAMAGE_TABLE_RESPONSE,
        RANKINGS_RESPONSE,
    )
    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance._make_client", lambda: cm)

    await fetch_report_summary("abc123", user_token="bearer-xyz")

    for call in mock.query.call_args_list:
        kwargs = call.kwargs
        assert kwargs.get("user_token") == "bearer-xyz", (
            f"Expected user_token='bearer-xyz' in query call kwargs, got: {kwargs}"
        )


# ===========================================================================
# get_warcraftlogs_report (@tool) tests
# ===========================================================================


@pytest.mark.asyncio
async def test_tool_success_returns_formatted_string(monkeypatch):
    async def fake_fetch(code, fight_ids=None, player_name=None, user_token=None):
        return {
            "title": "Test Report",
            "zone": "Nerub-ar Palace",
            "fights": [
                {"id": 1, "name": "Ulgrax", "difficulty": 5, "kill": True, "duration_seconds": 220.0, "last_phase": 2}
            ],
            "actors": [],
            "resolved_player": None,
            "damage_table": None,
            "ability_breakdown": None,
            "cast_summary": None,
            "rankings": None,
            "errors": [],
        }

    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance.fetch_report_summary", fake_fetch)

    result = await get_warcraftlogs_report.ainvoke(
        {"report_id": "abc123", "fight_ids": "", "player_name": "", "user_token": ""}
    )

    assert "Test Report" in result
    assert "Nerub-ar Palace" in result
    assert "✓ Kill" in result


@pytest.mark.asyncio
async def test_tool_player_name_and_user_token_passed_through(monkeypatch):
    received: dict = {}

    async def fake_fetch(code, fight_ids=None, player_name=None, user_token=None):
        received["player_name"] = player_name
        received["user_token"] = user_token
        return {
            "title": "T",
            "zone": "Z",
            "fights": [],
            "actors": [],
            "resolved_player": None,
            "damage_table": None,
            "ability_breakdown": None,
            "cast_summary": None,
            "rankings": None,
            "errors": [],
        }

    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance.fetch_report_summary", fake_fetch)

    await get_warcraftlogs_report.ainvoke(
        {"report_id": "abc", "fight_ids": "", "player_name": "Thrall", "user_token": "tok123"}
    )

    assert received["player_name"] == "Thrall"
    assert received["user_token"] == "tok123"


@pytest.mark.asyncio
async def test_tool_private_report_friendly_message(monkeypatch):
    async def fake_fetch(*_a, **_kw):
        raise WarcraftLogsPrivateReportError("private")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance.fetch_report_summary", fake_fetch)

    result = await get_warcraftlogs_report.ainvoke({"report_id": "priv"})
    assert "private" in result.lower()
    assert "wclauth" in result.lower()


@pytest.mark.asyncio
async def test_tool_auth_error_friendly_message(monkeypatch):
    async def fake_fetch(*_a, **_kw):
        raise WarcraftLogsAuthError("bad token")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance.fetch_report_summary", fake_fetch)

    result = await get_warcraftlogs_report.ainvoke({"report_id": "abc"})
    assert "authentication" in result.lower()


@pytest.mark.asyncio
async def test_tool_invalid_fight_ids_returns_guidance(monkeypatch):
    result = await get_warcraftlogs_report.ainvoke({"report_id": "abc", "fight_ids": "one,two"})
    assert "couldn't parse" in result.lower()


@pytest.mark.asyncio
async def test_tool_malformed_response_returns_friendly_message(monkeypatch):
    async def fake_fetch(*_a, **_kw):
        raise KeyError("reportData")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.performance.fetch_report_summary", fake_fetch)

    result = await get_warcraftlogs_report.ainvoke({"report_id": "abc"})
    assert "unexpected format" in result.lower()


# ===========================================================================
# _format_summary tests
# ===========================================================================


def _base_summary(**overrides) -> dict:
    base = {
        "title": "My Report",
        "zone": "Amirdrassil",
        "fights": [],
        "actors": [],
        "resolved_player": None,
        "damage_table": None,
        "ability_breakdown": None,
        "cast_summary": None,
        "rankings": None,
        "errors": [],
    }
    base.update(overrides)
    return base


def test_format_kill_indicator():
    s = _base_summary(
        fights=[
            {"id": 1, "name": "Gnarlroot", "difficulty": 5, "kill": True, "duration_seconds": 240.0, "last_phase": 2}
        ]
    )
    assert "✓ Kill" in _format_summary(s)


def test_format_wipe_indicator():
    s = _base_summary(
        fights=[
            {"id": 1, "name": "Gnarlroot", "difficulty": 5, "kill": False, "duration_seconds": 120.0, "last_phase": 1}
        ]
    )
    assert "✗ Wipe" in _format_summary(s)


def test_format_damage_table_capped_at_15():
    entries = [
        {"name": f"Player{i}", "type": "Spec", "total": 1_000_000, "activeTime": 200_000, "pct": 5.0} for i in range(20)
    ]
    s = _base_summary(damage_table={"data": {"entries": entries}})
    text = _format_summary(s)
    assert "Player14" in text
    assert "Player15" not in text


def test_format_ability_breakdown_shown_when_player_resolved():
    entries = [{"name": "Stormstrike", "total": 2_000_000, "uses": 24, "hitCount": 48}]
    s = _base_summary(
        resolved_player={"id": 1, "name": "Thrall"},
        ability_breakdown={"data": {"entries": entries}},
    )
    text = _format_summary(s)
    assert "Ability breakdown — Thrall" in text
    assert "Stormstrike" in text


def test_format_cast_summary_section():
    cs = {"total_casts": 120, "cast_counts": {"Stormstrike": 24}, "cancelled_casts": {}}
    s = _base_summary(
        resolved_player={"id": 1, "name": "Thrall"},
        cast_summary=cs,
    )
    text = _format_summary(s)
    assert "Cast summary — Thrall" in text
    assert "120" in text
    assert "Stormstrike: 24×" in text


def test_format_cancelled_casts_shown():
    cs = {
        "total_casts": 10,
        "cast_counts": {},
        "cancelled_casts": {"Healing Surge": 3},
    }
    s = _base_summary(
        resolved_player={"id": 1, "name": "Thrall"},
        cast_summary=cs,
    )
    text = _format_summary(s)
    assert "Healing Surge: 3× cancelled" in text


def test_format_non_fatal_errors_shown():
    s = _base_summary(errors=["Rankings unavailable: 503"])
    text = _format_summary(s)
    assert "⚠" in text
    assert "503" in text


def test_format_truncated_cast_warning():
    cs = {"total_casts": 2000, "cast_counts": {}, "cancelled_casts": {}, "truncated": True}
    s = _base_summary(
        resolved_player={"id": 1, "name": "Thrall"},
        cast_summary=cs,
    )
    text = _format_summary(s)
    assert "truncated" in text.lower()
