"""
tests/unit/tools/test_raiderio_tool.py

Unit tests for tools/raiderio.py.

Covers:
  RaiderIOClient.get_character_profile  — HTTP layer, status code handling
  normalize_profile                     — data transformation correctness
  get_character_raiderio (@tool)        — region validation, error surfacing

No live network calls. All HTTP is mocked via respx.
"""

import httpx
import pytest
import respx
from fixtures.raiderio_payloads import (
    CHARACTER_NOT_FOUND_BODY,
    MAGE_PROFILE_RAW,
    SERVER_ERROR_BODY,
    SPARSE_PROFILE_RAW,
    WARRIOR_NO_PLUS_RAW,
)

# ---------------------------------------------------------------------------
# Import the module under test.
# Adjust the import path to match your project layout.
# ---------------------------------------------------------------------------
from khadbot.tools.raiderio import (
    BASE_URL,
    VALID_REGIONS,
    CharacterNotFoundError,
    RaiderIOClient,
    RaiderIOError,
    get_character_raiderio,
    normalize_profile,
)

# ===========================================================================
# RaiderIOClient — HTTP layer
# ===========================================================================


class TestRaiderIOClientHappyPath:
    @respx.mock
    def test_returns_parsed_json_on_200(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(200, json=MAGE_PROFILE_RAW))
        with RaiderIOClient() as client:
            result = client.get_character_profile("Pyroblastus", "area-52", "us")
        assert result["name"] == "Pyroblastus"
        assert result["class"] == "Mage"

    @respx.mock
    def test_lowercases_region_and_realm_in_params(self):
        """Verify params are normalized before hitting the API."""
        route = respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(200, json=MAGE_PROFILE_RAW)
        )
        with RaiderIOClient() as client:
            client.get_character_profile("Pyroblastus", "Area-52", "US")

        sent_params = dict(route.calls[0].request.url.params)
        assert sent_params["region"] == "us"
        assert sent_params["realm"] == "area-52"

    @respx.mock
    def test_fields_param_is_sent(self):
        route = respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(200, json=MAGE_PROFILE_RAW)
        )
        with RaiderIOClient() as client:
            client.get_character_profile("Pyroblastus", "area-52", "us")

        sent_params = dict(route.calls[0].request.url.params)
        assert "fields" in sent_params
        assert "gear" in sent_params["fields"]
        assert "raid_progression" in sent_params["fields"]


class TestRaiderIOClientErrorHandling:
    @respx.mock
    def test_404_raises_character_not_found(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(404, json=CHARACTER_NOT_FOUND_BODY)
        )
        with RaiderIOClient() as client:
            with pytest.raises(CharacterNotFoundError) as exc_info:
                client.get_character_profile("NoSuchChar", "area-52", "us")
        assert "NoSuchChar" in str(exc_info.value)
        assert "area-52" in str(exc_info.value)

    @respx.mock
    def test_400_raises_character_not_found(self):
        """Raider.IO returns 400 (not 404) for unknown characters."""
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(400, json=CHARACTER_NOT_FOUND_BODY)
        )
        with RaiderIOClient() as client:
            with pytest.raises(CharacterNotFoundError):
                client.get_character_profile("BadName", "area-52", "us")

    @respx.mock
    def test_400_message_included_in_exception(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(400, json=CHARACTER_NOT_FOUND_BODY)
        )
        with RaiderIOClient() as client:
            with pytest.raises(CharacterNotFoundError) as exc_info:
                client.get_character_profile("BadName", "area-52", "us")
        # The API's message should propagate
        assert "Could not find character" in str(exc_info.value)

    @respx.mock
    def test_500_raises_raiderio_error(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(500, json=SERVER_ERROR_BODY))
        with RaiderIOClient() as client:
            with pytest.raises(RaiderIOError) as exc_info:
                client.get_character_profile("Pyroblastus", "area-52", "us")
        assert "500" in str(exc_info.value)

    @respx.mock
    def test_500_is_not_character_not_found(self):
        """500 should raise RaiderIOError, not the more specific subclass."""
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(500, json=SERVER_ERROR_BODY))
        with RaiderIOClient() as client:
            with pytest.raises(RaiderIOError) as exc_info:
                client.get_character_profile("Pyroblastus", "area-52", "us")
        assert not isinstance(exc_info.value, CharacterNotFoundError)

    @respx.mock
    def test_network_error_raises_raiderio_error(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(side_effect=httpx.ConnectError("Connection refused"))
        with RaiderIOClient() as client:
            with pytest.raises(RaiderIOError) as exc_info:
                client.get_character_profile("Pyroblastus", "area-52", "us")
        assert "Network error" in str(exc_info.value)

    @respx.mock
    def test_400_with_malformed_json_body_still_raises(self):
        """If the error body isn't valid JSON, should still raise CharacterNotFoundError."""
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(400, content=b"not json at all"))
        with RaiderIOClient() as client:
            with pytest.raises(CharacterNotFoundError):
                client.get_character_profile("BadName", "area-52", "us")


# ===========================================================================
# normalize_profile — data transformation
# ===========================================================================


class TestNormalizeProfileFields:
    def test_basic_fields_extracted(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        assert result["name"] == "Pyroblastus"
        assert result["realm"] == "Area 52"
        assert result["region"] == "us"
        assert result["class"] == "Mage"
        assert result["spec"] == "Fire"

    def test_item_level_extracted(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        assert result["item_level_equipped"] == 639
        assert result["item_level_total"] == 641

    def test_overall_mplus_score(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        assert result["mythic_plus_score"] == pytest.approx(2847.3, abs=0.1)

    def test_score_breakdown_contains_dps(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        breakdown = result["mythic_plus_score_breakdown"]
        assert "dps" in breakdown
        assert breakdown["dps"] == pytest.approx(2847.3, abs=0.1)

    def test_highest_key_is_highest_level(self):
        """Should pick the run with the highest mythic_level across all dungeons."""
        result = normalize_profile(MAGE_PROFILE_RAW)
        assert result["highest_key_completed"]["level"] == 12
        assert result["highest_key_completed"]["dungeon"] == "Ara-Kara, City of Echoes"

    def test_highest_key_timed_flag(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        # Ara-Kara +12 has num_keystone_upgrades=1 → timed=True
        assert result["highest_key_completed"]["timed"] is True

    def test_best_runs_capped_at_five(self):
        """MAGE_PROFILE_RAW has 6 best_runs entries; output should be ≤5."""
        result = normalize_profile(MAGE_PROFILE_RAW)
        assert len(result["best_runs"]) <= 5

    def test_best_runs_sorted_by_score_descending(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        scores = [r["score"] for r in result["best_runs"]]
        assert scores == sorted(scores, reverse=True)

    def test_best_run_score_rounded(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        # All scores should be floats with at most 1 decimal place
        for run in result["best_runs"]:
            assert run["score"] == round(run["score"], 1)

    def test_raid_progression_passed_through(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        assert "nerub-ar-palace" in result["raid_progression"]
        assert result["raid_progression"]["nerub-ar-palace"]["summary"] == "9/8M"

    def test_profile_url_present(self):
        result = normalize_profile(MAGE_PROFILE_RAW)
        assert result["profile_url"].startswith("https://raider.io")


class TestNormalizeProfileEdgeCases:
    def test_no_mplus_activity_returns_zero_score(self):
        result = normalize_profile(WARRIOR_NO_PLUS_RAW)
        assert result["mythic_plus_score"] == 0
        assert result["highest_key_completed"] is None
        assert result["best_runs"] == []

    def test_no_mplus_activity_score_breakdown_is_empty(self):
        result = normalize_profile(WARRIOR_NO_PLUS_RAW)
        assert result["mythic_plus_score_breakdown"] == {}

    def test_null_gear_defaults_to_zero_ilvl(self):
        result = normalize_profile(SPARSE_PROFILE_RAW)
        assert result["item_level_equipped"] == 0
        assert result["item_level_total"] == 0

    def test_null_scores_defaults_gracefully(self):
        result = normalize_profile(SPARSE_PROFILE_RAW)
        assert result["mythic_plus_score"] == 0

    def test_null_runs_fields_default_gracefully(self):
        result = normalize_profile(SPARSE_PROFILE_RAW)
        assert result["highest_key_completed"] is None
        assert result["best_runs"] == []

    def test_missing_class_defaults_to_unknown(self):
        result = normalize_profile(SPARSE_PROFILE_RAW)
        assert result["class"] == "Unknown"

    def test_missing_spec_defaults_to_unknown(self):
        result = normalize_profile(SPARSE_PROFILE_RAW)
        assert result["spec"] == "Unknown"

    def test_null_raid_progression_defaults_to_empty_dict(self):
        result = normalize_profile(SPARSE_PROFILE_RAW)
        assert result["raid_progression"] == {}

    def test_all_required_keys_present(self):
        """normalize_profile must always return all expected keys, even with sparse input."""
        result = normalize_profile(SPARSE_PROFILE_RAW)
        required_keys = {
            "name",
            "realm",
            "region",
            "class",
            "spec",
            "race",
            "faction",
            "item_level_equipped",
            "item_level_total",
            "profile_url",
            "thumbnail_url",
            "mythic_plus_score",
            "mythic_plus_score_breakdown",
            "highest_key_completed",
            "best_runs",
            "raid_progression",
        }
        assert required_keys.issubset(result.keys())


# ===========================================================================
# get_character_raiderio (@tool entry point)
# Tests the LangChain tool wrapper — region validation, exception-to-dict
# conversion, and that errors are returned as dicts (not raised).
# ===========================================================================


class TestGetCharacterRaiderioTool:
    @respx.mock
    def test_valid_call_returns_normalized_profile(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(200, json=MAGE_PROFILE_RAW))
        result = get_character_raiderio.invoke({"name": "Pyroblastus", "realm": "area-52", "region": "us"})
        assert isinstance(result, dict)
        assert result.get("error") is not True
        assert result["name"] == "Pyroblastus"
        assert result["mythic_plus_score"] > 0

    @pytest.mark.parametrize("region", ["xx", "na", "EU ", "USA", "", "  "])
    def test_invalid_region_returns_error_dict(self, region):
        """Invalid regions must return an error dict — never raise."""
        result = get_character_raiderio.invoke({"name": "Pyroblastus", "realm": "area-52", "region": region})
        assert result.get("error") is True
        assert "region" in result["message"].lower()

    @pytest.mark.parametrize("region", list(VALID_REGIONS))
    @respx.mock
    def test_all_valid_regions_accepted(self, region):
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(200, json=MAGE_PROFILE_RAW))
        result = get_character_raiderio.invoke({"name": "Pyroblastus", "realm": "area-52", "region": region})
        assert result.get("error") is not True

    @pytest.mark.parametrize("region", ["US", "EU", "KR", "TW"])
    @respx.mock
    def test_uppercase_regions_are_accepted(self, region):
        """Region matching must be case-insensitive."""
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(200, json=MAGE_PROFILE_RAW))
        result = get_character_raiderio.invoke({"name": "Pyroblastus", "realm": "area-52", "region": region})
        assert result.get("error") is not True

    @respx.mock
    def test_character_not_found_returns_error_dict_not_exception(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(404, json=CHARACTER_NOT_FOUND_BODY)
        )
        result = get_character_raiderio.invoke({"name": "NoSuchChar", "realm": "area-52", "region": "us"})
        assert result.get("error") is True
        assert result.get("not_found") is True
        assert "NoSuchChar" in result["message"]

    @respx.mock
    def test_server_error_returns_error_dict_not_exception(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(500, json=SERVER_ERROR_BODY))
        result = get_character_raiderio.invoke({"name": "Pyroblastus", "realm": "area-52", "region": "us"})
        assert result.get("error") is True
        assert result.get("not_found") is not True  # 500 ≠ not found

    @respx.mock
    def test_network_error_returns_error_dict_not_exception(self):
        respx.get(f"{BASE_URL}/characters/profile").mock(side_effect=httpx.ConnectError("Connection refused"))
        result = get_character_raiderio.invoke({"name": "Pyroblastus", "realm": "area-52", "region": "us"})
        assert result.get("error") is True

    @respx.mock
    def test_error_dict_always_has_message_key(self):
        """Every error path must return a dict with a 'message' key for agent reasoning."""
        respx.get(f"{BASE_URL}/characters/profile").mock(
            return_value=httpx.Response(404, json=CHARACTER_NOT_FOUND_BODY)
        )
        result = get_character_raiderio.invoke({"name": "NoSuchChar", "realm": "area-52", "region": "us"})
        assert "message" in result
        assert isinstance(result["message"], str)
        assert len(result["message"]) > 0

    @respx.mock
    def test_realm_with_spaces_handled(self):
        """Realm names like 'Burning Legion' must not break URL encoding."""
        respx.get(f"{BASE_URL}/characters/profile").mock(return_value=httpx.Response(200, json=MAGE_PROFILE_RAW))
        # Should not raise
        result = get_character_raiderio.invoke({"name": "Pyroblastus", "realm": "Burning Legion", "region": "eu"})
        # Either succeeds or returns an error dict — but never raises
        assert isinstance(result, dict)
