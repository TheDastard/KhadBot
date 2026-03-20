"""
Unit tests for the WarcraftLogs character report discovery tool.

All monkeypatch targets use the full khadbot.tools.warcraftlogs_character
import path, matching the installed package structure.

Coverage
--------
_slugify_realm
  - plain realm name lowercased
  - spaces converted to hyphens
  - apostrophes stripped (Kel'Thuzad → kelthuzad)
  - mixed punctuation stripped
  - leading/trailing whitespace trimmed
  - already-correct slug unchanged
  - hyphenated realm preserved (Bleeding-Hollow)

_normalise_region
  - valid codes accepted case-insensitively (us, EU, Tw, KR, cn)
  - "na" aliased to "us"
  - unknown region returns None

_difficulty_label
  - known IDs return correct strings
  - unknown ID returns fallback string
  - None returns "Unknown"

_ms_to_date
  - known timestamp returns expected date string
  - zero returns "Unknown"

_format_report_fight_list
  - empty list returns no-reports message
  - report code and title present
  - fight IDs present in output
  - kill/wipe result shown correctly
  - difficulty label shown
  - report with no fights shows appropriate message

fetch_character_reports
  - happy path: character found, reports returned
  - invalid region raises ValueError before any API call
  - character not found (None node) raises LookupError
  - empty recentReports adds error string, returns empty reports list
  - reports_limit clamped to 1–5
  - realm slug forwarded correctly to query
  - region normalised before query
  - user_token forwarded to client.query
  - API error propagates as WarcraftLogsAPIError

find_character_reports (@tool)
  - success: formatted string contains character name and report code
  - success: fight IDs visible in output
  - invalid region returns guidance string
  - character not found returns LookupError message
  - non-integer reports_limit falls back to 5 gracefully
  - private report returns friendly message
  - auth error returns friendly message
  - API error returns friendly message
  - character_name and realm whitespace stripped before fetch
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from khadbot.tools.warcraftlogs._client import (
    WarcraftLogsAPIError,
    WarcraftLogsAuthError,
    WarcraftLogsPrivateReportError,
)
from khadbot.tools.warcraftlogs.character import (
    _difficulty_label,
    _format_report_fight_list,
    _ms_to_date,
    _normalise_region,
    _slugify_realm,
    fetch_character_reports,
    find_character_reports,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_REPORT_CODE = "xKBzTQ1NjAHpGrCa"

CHARACTER_RESPONSE = {
    "characterData": {
        "character": {
            "name": "Thrall",
            "classID": 7,
            "recentReports": {
                "data": [
                    {
                        "code": _REPORT_CODE,
                        "title": "Sunday Mythic Split",
                        "startTime": 1_700_000_000_000,
                        "endTime": 1_700_010_000_000,
                        "zone": {"name": "Amirdrassil"},
                        "fights": [
                            {
                                "id": 1,
                                "name": "Gnarlroot",
                                "difficulty": 5,
                                "kill": True,
                                "startTime": 1_700_000_100_000,
                                "endTime": 1_700_000_340_000,
                                "lastPhase": 2,
                            },
                            {
                                "id": 2,
                                "name": "Igira the Cruel",
                                "difficulty": 5,
                                "kill": False,
                                "startTime": 1_700_000_400_000,
                                "endTime": 1_700_000_580_000,
                                "lastPhase": 1,
                            },
                        ],
                    }
                ]
            },
        }
    }
}

CHARACTER_NOT_FOUND_RESPONSE = {"characterData": {"character": None}}

NO_REPORTS_RESPONSE = {
    "characterData": {
        "character": {
            "name": "Thrall",
            "classID": 7,
            "recentReports": {"data": []},
        }
    }
}


def _mock_client(response):
    """Return a _make_client replacement whose query() returns response."""
    mock = AsyncMock()
    mock.query = AsyncMock(return_value=response)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock


# ===========================================================================
# _slugify_realm
# ===========================================================================


class TestSlugifyRealm:
    def test_plain_name_lowercased(self):
        assert _slugify_realm("Stormrage") == "stormrage"

    def test_spaces_to_hyphens(self):
        assert _slugify_realm("Area 52") == "area-52"

    def test_apostrophe_stripped(self):
        assert _slugify_realm("Kel'Thuzad") == "kelthuzad"

    def test_curly_apostrophe_stripped(self):
        assert _slugify_realm("Kel\u2019Thuzad") == "kelthuzad"

    def test_mixed_punctuation(self):
        assert _slugify_realm("Kel'Thuzad") == "kelthuzad"

    def test_leading_trailing_whitespace(self):
        assert _slugify_realm("  Thrall  ") == "thrall"

    def test_already_correct_slug_unchanged(self):
        assert _slugify_realm("area-52") == "area-52"

    def test_hyphenated_realm_preserved(self):
        assert _slugify_realm("Bleeding Hollow") == "bleeding-hollow"

    def test_multiple_spaces_collapse_to_one_hyphen(self):
        assert _slugify_realm("some  realm") == "some-realm"


# ===========================================================================
# _normalise_region
# ===========================================================================


class TestNormaliseRegion:
    @pytest.mark.parametrize("region", ["us", "US", "Us"])
    def test_us_variants(self, region):
        assert _normalise_region(region) == "us"

    @pytest.mark.parametrize("region", ["eu", "EU"])
    def test_eu_variants(self, region):
        assert _normalise_region(region) == "eu"

    @pytest.mark.parametrize("region", ["tw", "kr", "cn"])
    def test_other_valid_regions(self, region):
        assert _normalise_region(region) == region

    def test_na_aliased_to_us(self):
        assert _normalise_region("na") == "us"

    def test_NA_aliased_to_us(self):
        assert _normalise_region("NA") == "us"

    def test_unknown_region_returns_none(self):
        assert _normalise_region("xyz") is None

    def test_whitespace_stripped(self):
        assert _normalise_region("  us  ") == "us"


# ===========================================================================
# _difficulty_label
# ===========================================================================


class TestDifficultyLabel:
    def test_mythic(self):
        assert _difficulty_label(5) == "Mythic"

    def test_heroic(self):
        assert _difficulty_label(4) == "Heroic"

    def test_normal(self):
        assert _difficulty_label(3) == "Normal"

    def test_unknown_id_fallback(self):
        assert "99" in _difficulty_label(99)

    def test_none_returns_unknown(self):
        assert _difficulty_label(None) == "Unknown"


# ===========================================================================
# _ms_to_date
# ===========================================================================


class TestMsToDate:
    def test_known_timestamp(self):
        # 2023-11-14 UTC
        ms = 1_700_000_000_000
        result = _ms_to_date(ms)
        assert result.startswith("2023-11-")

    def test_zero_returns_unknown(self):
        assert _ms_to_date(0) == "Unknown"


# ===========================================================================
# _format_report_fight_list
# ===========================================================================


class TestFormatReportFightList:
    def test_empty_list_returns_no_reports_message(self):
        result = _format_report_fight_list([])
        assert "no recent reports" in result.lower()

    def test_report_code_present(self):
        reports = CHARACTER_RESPONSE["characterData"]["character"]["recentReports"]["data"]
        result = _format_report_fight_list(reports)
        assert _REPORT_CODE in result

    def test_report_title_present(self):
        reports = CHARACTER_RESPONSE["characterData"]["character"]["recentReports"]["data"]
        result = _format_report_fight_list(reports)
        assert "Sunday Mythic Split" in result

    def test_fight_ids_present(self):
        reports = CHARACTER_RESPONSE["characterData"]["character"]["recentReports"]["data"]
        result = _format_report_fight_list(reports)
        assert "[1]" in result
        assert "[2]" in result

    def test_kill_indicator_shown(self):
        reports = CHARACTER_RESPONSE["characterData"]["character"]["recentReports"]["data"]
        result = _format_report_fight_list(reports)
        assert "✓ Kill" in result

    def test_wipe_indicator_shown(self):
        reports = CHARACTER_RESPONSE["characterData"]["character"]["recentReports"]["data"]
        result = _format_report_fight_list(reports)
        assert "✗ Wipe" in result

    def test_difficulty_label_shown(self):
        reports = CHARACTER_RESPONSE["characterData"]["character"]["recentReports"]["data"]
        result = _format_report_fight_list(reports)
        assert "Mythic" in result

    def test_report_with_no_fights(self):
        reports = [{"code": "abc", "title": "Empty", "startTime": 0, "zone": {"name": "Z"}, "fights": []}]
        result = _format_report_fight_list(reports)
        assert "no encounter fights" in result.lower()


# ===========================================================================
# fetch_character_reports
# ===========================================================================


@pytest.mark.asyncio
async def test_fetch_happy_path(monkeypatch):
    cm, mock = _mock_client(CHARACTER_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    result = await fetch_character_reports("Thrall", "Stormrage", "us")

    assert result["character_name"] == "Thrall"
    assert result["region"] == "us"
    assert result["realm_slug"] == "stormrage"
    assert len(result["reports"]) == 1
    assert result["reports"][0]["code"] == _REPORT_CODE
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_fetch_invalid_region_raises_before_api_call(monkeypatch):
    cm, mock = _mock_client(CHARACTER_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    with pytest.raises(ValueError, match="Unrecognised region"):
        await fetch_character_reports("Thrall", "Stormrage", "xx")

    mock.query.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_character_not_found_raises_lookup_error(monkeypatch):
    cm, _ = _mock_client(CHARACTER_NOT_FOUND_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    with pytest.raises(LookupError, match="not found"):
        await fetch_character_reports("NoOne", "Stormrage", "us")


@pytest.mark.asyncio
async def test_fetch_empty_reports_adds_error(monkeypatch):
    cm, _ = _mock_client(NO_REPORTS_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    result = await fetch_character_reports("Thrall", "Stormrage", "us")

    assert result["reports"] == []
    assert len(result["errors"]) == 1
    assert "no recent" in result["errors"][0].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_limit, expected",
    [
        (0, 1),  # clamped to minimum
        (1, 1),
        (3, 3),
        (5, 5),
        (9, 5),  # clamped to maximum
    ],
)
async def test_fetch_reports_limit_clamped(raw_limit, expected, monkeypatch):
    cm, mock = _mock_client(CHARACTER_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    await fetch_character_reports("Thrall", "Stormrage", "us", reports_limit=raw_limit)

    call_vars = mock.query.call_args[0][1]  # positional arg: variables dict
    assert call_vars["reportsLimit"] == expected


@pytest.mark.asyncio
async def test_fetch_realm_slugified_in_query(monkeypatch):
    cm, mock = _mock_client(CHARACTER_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    await fetch_character_reports("Thrall", "Area 52", "us")

    call_vars = mock.query.call_args[0][1]
    assert call_vars["serverSlug"] == "area-52"


@pytest.mark.asyncio
async def test_fetch_region_normalised_in_query(monkeypatch):
    cm, mock = _mock_client(CHARACTER_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    await fetch_character_reports("Thrall", "Stormrage", "NA")

    call_vars = mock.query.call_args[0][1]
    assert call_vars["serverRegion"] == "us"


@pytest.mark.asyncio
async def test_fetch_user_token_forwarded(monkeypatch):
    cm, mock = _mock_client(CHARACTER_RESPONSE)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    await fetch_character_reports("Thrall", "Stormrage", "us", user_token="tok-xyz")

    call_kwargs = mock.query.call_args[1]
    assert call_kwargs.get("user_token") == "tok-xyz"


@pytest.mark.asyncio
async def test_fetch_api_error_propagates(monkeypatch):
    mock = AsyncMock()
    mock.query = AsyncMock(side_effect=WarcraftLogsAPIError("upstream down"))
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("khadbot.tools.warcraftlogs.character._make_client", lambda: cm)

    with pytest.raises(WarcraftLogsAPIError):
        await fetch_character_reports("Thrall", "Stormrage", "us")


# ===========================================================================
# find_character_reports (@tool)
# ===========================================================================


@pytest.mark.asyncio
async def test_tool_success_contains_character_and_report_code(monkeypatch):
    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        return {
            "character_name": "Thrall",
            "realm_slug": "stormrage",
            "region": "us",
            "reports": CHARACTER_RESPONSE["characterData"]["character"]["recentReports"]["data"],
            "errors": [],
        }

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    result = await find_character_reports.ainvoke(
        {
            "character_name": "Thrall",
            "realm": "Stormrage",
            "region": "us",
            "reports_limit": "5",
        }
    )

    assert "Thrall" in result
    assert _REPORT_CODE in result


@pytest.mark.asyncio
async def test_tool_success_fight_ids_visible(monkeypatch):
    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        return {
            "character_name": "Thrall",
            "realm_slug": "stormrage",
            "region": "us",
            "reports": CHARACTER_RESPONSE["characterData"]["character"]["recentReports"]["data"],
            "errors": [],
        }

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    result = await find_character_reports.ainvoke(
        {
            "character_name": "Thrall",
            "realm": "Stormrage",
            "region": "us",
        }
    )

    assert "[1]" in result
    assert "[2]" in result
    assert "Gnarlroot" in result
    assert "Igira the Cruel" in result


@pytest.mark.asyncio
async def test_tool_invalid_region_returns_guidance(monkeypatch):
    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        raise ValueError("Unrecognised region 'xx'.")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    result = await find_character_reports.ainvoke(
        {
            "character_name": "Thrall",
            "realm": "Stormrage",
            "region": "xx",
        }
    )

    assert "unrecognised region" in result.lower()


@pytest.mark.asyncio
async def test_tool_character_not_found_returns_message(monkeypatch):
    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        raise LookupError("Character 'NoOne' on Stormrage (US) not found.")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    result = await find_character_reports.ainvoke(
        {
            "character_name": "NoOne",
            "realm": "Stormrage",
            "region": "us",
        }
    )

    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_tool_non_integer_limit_falls_back_to_five(monkeypatch):
    received: dict = {}

    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        received["reports_limit"] = reports_limit
        return {
            "character_name": "Thrall",
            "realm_slug": "stormrage",
            "region": "us",
            "reports": [],
            "errors": [],
        }

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    await find_character_reports.ainvoke(
        {
            "character_name": "Thrall",
            "realm": "Stormrage",
            "region": "us",
            "reports_limit": "all",  # non-integer
        }
    )

    assert received["reports_limit"] == 5


@pytest.mark.asyncio
async def test_tool_private_report_friendly_message(monkeypatch):
    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        raise WarcraftLogsPrivateReportError("private")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    result = await find_character_reports.ainvoke(
        {
            "character_name": "Thrall",
            "realm": "Stormrage",
            "region": "us",
        }
    )

    assert "private" in result.lower()


@pytest.mark.asyncio
async def test_tool_auth_error_friendly_message(monkeypatch):
    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        raise WarcraftLogsAuthError("bad token")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    result = await find_character_reports.ainvoke(
        {
            "character_name": "Thrall",
            "realm": "Stormrage",
            "region": "us",
        }
    )

    assert "authentication" in result.lower()


@pytest.mark.asyncio
async def test_tool_api_error_friendly_message(monkeypatch):
    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        raise WarcraftLogsAPIError("503 upstream")

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    result = await find_character_reports.ainvoke(
        {
            "character_name": "Thrall",
            "realm": "Stormrage",
            "region": "us",
        }
    )

    assert "503" in result


@pytest.mark.asyncio
async def test_tool_strips_whitespace_from_inputs(monkeypatch):
    received: dict = {}

    async def fake_fetch(character_name, realm, region, reports_limit=5, user_token=None):
        received["character_name"] = character_name
        received["realm"] = realm
        return {
            "character_name": character_name,
            "realm_slug": "stormrage",
            "region": "us",
            "reports": [],
            "errors": [],
        }

    monkeypatch.setattr("khadbot.tools.warcraftlogs.character.fetch_character_reports", fake_fetch)

    await find_character_reports.ainvoke(
        {
            "character_name": "  Thrall  ",
            "realm": "  Stormrage  ",
            "region": "us",
        }
    )

    assert received["character_name"] == "Thrall"
    assert received["realm"] == "Stormrage"
