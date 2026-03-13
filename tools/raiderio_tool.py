"""
tools/raiderio.py

Raider.IO API client and LangChain tool for KhadBot.

Structure:
  RaiderIOClient   — thin HTTP client wrapping the public REST API
  normalize_profile — maps raw API response to the flat shape the agent expects
  get_character_raiderio — @tool entry point called by the agent
"""

import httpx
from pydantic import BaseModel, Field
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://raider.io/api/v1"

# Fields we request from the profile endpoint.
# Each comma-separated value maps to a top-level key in the response.
PROFILE_FIELDS = ",".join([
    "gear",
    "mythic_plus_scores_by_season:current",
    "mythic_plus_best_runs:all",
    "mythic_plus_highest_level_runs",
    "raid_progression",
])

# Valid regions accepted by the API
VALID_REGIONS = {"us", "eu", "kr", "tw"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RaiderIOError(Exception):
    """Raised when the Raider.IO API returns an error or unexpected shape."""
    pass

class CharacterNotFoundError(RaiderIOError):
    """Raised when the character doesn't exist or has no Raider.IO profile."""
    pass


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class RaiderIOClient:
    """
    Thin synchronous client for the Raider.IO public REST API.
    No API key required for the endpoints we use.
    Uses httpx so we stay consistent with the rest of the project's HTTP layer.
    """

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._client.close()

    def get_character_profile(
        self,
        name: str,
        realm: str,
        region: str,
        fields: str = PROFILE_FIELDS,
    ) -> dict:
        """
        GET /characters/profile
        Returns the raw Raider.IO API response dict.
        Raises CharacterNotFoundError on 400/404, RaiderIOError on other failures.
        """
        params = {
            "region": region.lower(),
            "realm": realm.lower(),
            "name": name,
            "fields": fields,
        }

        try:
            resp = self._client.get("/characters/profile", params=params)
        except httpx.RequestError as exc:
            raise RaiderIOError(f"Network error contacting Raider.IO: {exc}") from exc

        if resp.status_code in (400, 404):
            # Raider.IO returns 400 with a message for unknown characters
            try:
                msg = resp.json().get("message", "Character not found")
            except Exception:
                msg = "Character not found"
            raise CharacterNotFoundError(
                f"Character '{name}-{realm}' ({region.upper()}) not found on Raider.IO. "
                f"Make sure the name/realm/region are correct and the character has been "
                f"logged in recently. API message: {msg}"
            )

        if not resp.is_success:
            raise RaiderIOError(
                f"Raider.IO API error {resp.status_code}: {resp.text[:200]}"
            )

        return resp.json()


# ---------------------------------------------------------------------------
# Response normalizer
# ---------------------------------------------------------------------------

def normalize_profile(raw: dict) -> dict:
    """
    Map the raw Raider.IO API response to the flat, agent-friendly shape
    that the rest of KhadBot expects. Handles missing optional fields gracefully.
    """
    # --- M+ scores ---
    scores_by_season = raw.get("mythic_plus_scores_by_season") or []
    current_scores = scores_by_season[0].get("scores", {}) if scores_by_season else {}
    overall_score = current_scores.get("all", 0)

    # --- Highest key completed (from highest_level_runs, sorted by level desc) ---
    highest_runs = raw.get("mythic_plus_highest_level_runs") or []
    if highest_runs:
        top_run = max(highest_runs, key=lambda r: r.get("mythic_level", 0))
        highest_key = {
            "dungeon": top_run.get("dungeon", "Unknown"),
            "level": top_run.get("mythic_level", 0),
            "timed": top_run.get("num_keystone_upgrades", 0) > 0,
        }
    else:
        highest_key = None

    # --- Best runs summary (top 5 for context) ---
    best_runs = raw.get("mythic_plus_best_runs") or []
    best_runs_summary = [
        {
            "dungeon": r.get("dungeon", "Unknown"),
            "level": r.get("mythic_level", 0),
            "score": round(r.get("score", 0), 1),
            "timed": r.get("num_keystone_upgrades", 0) > 0,
        }
        for r in sorted(best_runs, key=lambda r: r.get("score", 0), reverse=True)[:5]
    ]

    # --- Raid progression ---
    raid_progression = raw.get("raid_progression") or {}

    # --- Gear ---
    gear = raw.get("gear") or {}
    item_level_equipped = gear.get("item_level_equipped", 0)
    item_level_total = gear.get("item_level_total", 0)

    return {
        "name": raw.get("name", "Unknown"),
        "realm": raw.get("realm", "Unknown"),
        "region": raw.get("region", "Unknown"),
        "class": raw.get("class", "Unknown"),
        "spec": raw.get("active_spec_name", "Unknown"),
        "race": raw.get("race", "Unknown"),
        "faction": raw.get("faction", "Unknown"),
        "item_level_equipped": item_level_equipped,
        "item_level_total": item_level_total,
        "profile_url": raw.get("profile_url", ""),
        "thumbnail_url": raw.get("thumbnail_url", ""),
        "mythic_plus_score": overall_score,
        "mythic_plus_score_breakdown": {
            role: current_scores.get(role, 0)
            for role in ("all", "dps", "healer", "tank", "spec_0", "spec_1", "spec_2", "spec_3")
            if role in current_scores
        },
        "highest_key_completed": highest_key,
        "best_runs": best_runs_summary,
        "raid_progression": raid_progression,
    }


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class CharacterInput(BaseModel):
    name: str = Field(description="Character name, e.g. 'Thralladin'")
    realm: str = Field(description="Realm slug, e.g. 'area-52'")
    region: str = Field(description="Region code: 'us', 'eu', 'kr', 'tw'")


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------

@tool("get_character_raiderio", args_schema=CharacterInput)
def get_character_raiderio(name: str, realm: str, region: str) -> dict:
    """
    Fetch a character's Raider.IO profile: Mythic+ score, highest key completed,
    best dungeon runs this season, raid progression, and equipped item level.
    Use this to understand a player's overall progression and experience level
    before diving into detailed performance analysis.
    """
    region = region.lower().strip()
    if region not in VALID_REGIONS:
        return {
            "error": True,
            "message": f"Invalid region '{region}'. Must be one of: {', '.join(sorted(VALID_REGIONS))}",
        }

    try:
        with RaiderIOClient() as client:
            raw = client.get_character_profile(name=name, realm=realm, region=region)
        return normalize_profile(raw)

    except CharacterNotFoundError as exc:
        return {"error": True, "not_found": True, "message": str(exc)}

    except RaiderIOError as exc:
        return {"error": True, "message": str(exc)}
