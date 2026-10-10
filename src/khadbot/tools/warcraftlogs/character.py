"""
WarcraftLogs character report discovery — ``find_character_reports``.

Answers the question "find my recent logs" given only a character name,
realm, and region — no report code required.

The WarcraftLogs ``characterData.character.recentReports`` field returns the
N most recent reports the character appears in, each pre-populated with the
full fight list.  A single GraphQL call is sufficient to go from
(name, realm, region) → (report codes + fight IDs).

The agent uses this tool first when the user hasn't provided a report URL,
then passes the resolved report code and fight ID to ``get_warcraftlogs_report``
or ``get_encounter_analysis`` for the actual analysis.

Realm slug normalisation
------------------------
WarcraftLogs expects ``serverSlug`` in lowercase-hyphenated form (e.g.
``"area-52"``, ``"kel-thuzad"``).  Players say ``"Area 52"`` or
``"Kel'Thuzad"``.  ``_slugify_realm()`` handles the common transforms:

  - lowercase
  - spaces → hyphens
  - apostrophes and other punctuation stripped
  - leading/trailing whitespace stripped

This covers ~95 % of realm names.  The remaining edge cases (a handful of
realms where WarcraftLogs' slug diverges from the display name) surface as a
"character not found" error with a hint to check realm spelling.

Region normalisation
--------------------
Accepts ``"us"``, ``"eu"``, ``"tw"``, ``"kr"``, ``"cn"`` (case-insensitive).
``"na"`` is aliased to ``"us"`` since players frequently use either.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from langchain_core.tools import tool

from khadbot.tools.warcraftlogs._client import (
    WarcraftLogsAPIError,
    WarcraftLogsAuthError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
)
from khadbot.tools.warcraftlogs._queries import GET_CHARACTER_REPORTS

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1_000

VALID_REGIONS = {"us", "eu", "tw", "kr", "cn"}
REGION_ALIASES = {"na": "us"}

# Difficulty ID → human label mapping.
# WarcraftLogs uses numeric difficulty IDs; 5 = Mythic, 4 = Heroic, 3 = Normal.
DIFFICULTY_LABELS: dict[int, str] = {
    5: "Mythic",
    4: "Heroic",
    3: "Normal",
    2: "LFR",
    1: "LFR",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> WarcraftLogsClient:
    return WarcraftLogsClient(
        client_id=os.environ["WARCRAFTLOGS_CLIENT_ID"],
        client_secret=os.environ["WARCRAFTLOGS_CLIENT_SECRET"],
    )


def _slugify_realm(realm: str) -> str:
    """
    Convert a player-facing realm name to a WarcraftLogs server slug.

    Examples
    --------
    "Area 52"      → "area-52"
    "Kel'Thuzad"   → "kelthuzad"
    "Bleeding Hollow" → "bleeding-hollow"
    "stormrage"    → "stormrage"   (already correct)
    " Thrall "     → "thrall"
    """
    slug = realm.strip().lower()
    # Strip apostrophes and other punctuation that never appear in slugs.
    slug = re.sub(r"[''`\u2018\u2019]", "", slug)
    # Replace any run of non-alphanumeric characters with a single hyphen.
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    # Strip leading/trailing hyphens that may result from the above.
    slug = slug.strip("-")
    return slug


def _normalise_region(region: str) -> str | None:
    """
    Normalise a region string to the WarcraftLogs two-letter code.

    Returns ``None`` if the region is unrecognised after aliasing.
    """
    r = region.strip().lower()
    r = REGION_ALIASES.get(r, r)
    return r if r in VALID_REGIONS else None


def _difficulty_label(difficulty: int | None) -> str:
    if difficulty is None:
        return "Unknown"
    return DIFFICULTY_LABELS.get(difficulty, f"Difficulty {difficulty}")


def _format_report_fight_list(reports: list[dict]) -> str:
    """
    Render a list of recent reports + their fights as structured text for the
    agent.  Each fight line includes the fight ID so the agent can pass it
    directly to ``get_warcraftlogs_report`` or ``get_encounter_analysis``.
    """
    if not reports:
        return "No recent reports found for this character."

    lines: list[str] = []
    for i, report in enumerate(reports, start=1):
        code = report.get("code", "")
        title = report.get("title", "Untitled")
        zone = (report.get("zone") or {}).get("name", "Unknown zone")
        start_ms = report.get("startTime", 0)
        start_date = _ms_to_date(start_ms)

        lines.append(f"### Report {i}: {title}")
        lines.append(f"  Code: {code}  |  Zone: {zone}  |  Date: {start_date}")

        fights: list[dict] = report.get("fights") or []
        if fights:
            lines.append("  Fights (use fight ID with get_warcraftlogs_report / get_encounter_analysis):")
            for f in fights:
                result = "✓ Kill" if f.get("kill") else f"✗ Wipe ph{f.get('lastPhase', '?')}"
                diff = _difficulty_label(f.get("difficulty"))
                duration_s = round((f.get("endTime", 0) - f.get("startTime", 0)) / _MS_PER_SECOND, 0)
                lines.append(f"    [{f['id']}] {f['name']} ({diff}) — {duration_s:.0f}s — {result}")
        else:
            lines.append("  No encounter fights recorded in this report.")

        lines.append("")

    return "\n".join(lines).strip()


def _ms_to_date(ms: int) -> str:
    """Convert a Unix-millisecond timestamp to a readable UTC date string."""
    import datetime

    if not ms:
        return "Unknown"
    dt = datetime.datetime.fromtimestamp(ms / 1_000, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Core fetch function
# ---------------------------------------------------------------------------


async def fetch_character_reports(
    character_name: str,
    realm: str,
    region: str,
    reports_limit: int = 5,
    user_token: str | None = None,
) -> dict[str, Any]:
    """
    Fetch recent WarcraftLogs reports for a named character.

    Parameters
    ----------
    character_name:
        In-game character name (case-insensitive on WarcraftLogs' side).
    realm:
        Realm/server name in any common format.  Will be slugified before
        the API call.
    region:
        Region code.  Accepts "us"/"na", "eu", "tw", "kr", "cn"
        (case-insensitive).
    reports_limit:
        Number of recent reports to return (1–5).  Clamped to that range.
    user_token:
        PKCE user access token for private reports.

    Returns
    -------
    dict with keys:
        character_name  — resolved name from API (may differ in capitalisation)
        realm_slug      — slug used in the query
        region          — normalised region code
        reports         — list of report dicts (code, title, zone, fights)
        errors          — list of non-fatal warning strings
    """
    errors: list[str] = []
    qkw: dict[str, Any] = {"user_token": user_token} if user_token else {}

    realm_slug = _slugify_realm(realm)
    norm_region = _normalise_region(region)
    if norm_region is None:
        raise ValueError(
            f"Unrecognised region '{region}'. Valid values: {', '.join(sorted(VALID_REGIONS))} (or 'na' for US)."
        )

    limit = max(1, min(reports_limit, 5))

    async with _make_client() as client:
        data = await client.query(
            GET_CHARACTER_REPORTS,
            {
                "name": character_name,
                "serverSlug": realm_slug,
                "serverRegion": norm_region,
                "reportsLimit": limit,
            },
            **qkw,
        )

    character_node = (data.get("characterData") or {}).get("character")
    if character_node is None:
        raise LookupError(
            f"Character '{character_name}' on {realm} ({region.upper()}) not found. "
            f"Check that the name and realm spelling are correct. "
            f"Realm slug used: '{realm_slug}'."
        )

    recent = character_node.get("recentReports") or {}
    reports: list[dict] = recent.get("data") or []

    if not reports:
        errors.append(
            f"No recent public reports found for {character_name} on {realm}. "
            "The character's logs may all be set to private."
        )

    return {
        "character_name": character_node.get("name", character_name),
        "realm_slug": realm_slug,
        "region": norm_region,
        "reports": reports,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


@tool
async def find_character_reports(
    character_name: str,
    realm: str,
    region: str,
    reports_limit: str = "5",
) -> str:
    """
    Find a character's recent WarcraftLogs reports and their fight IDs.

    Use this tool when the user provides their character name, realm, and
    region but has NOT provided a WarcraftLogs report URL or code.  This
    tool resolves the report code and available fight IDs so you can then
    call get_warcraftlogs_report or get_encounter_analysis.

    Typical trigger phrases:
      "analyse my logs" / "check my recent parses"
      "why did we wipe last night" (without a URL)
      "look at my character on [realm]"

    Do NOT use this tool if the user has already provided a WarcraftLogs URL
    or report code — use get_warcraftlogs_report directly in that case.

    Args:
        character_name: The character's in-game name, e.g. "Thrall".
        realm: The character's realm/server name in any common format,
            e.g. "Area 52", "Kel'Thuzad", "Stormrage". Spaces and
            apostrophes are handled automatically.
        region: The character's region. Accepts: "us" (or "na"), "eu",
            "tw", "kr", "cn" (case-insensitive).
        reports_limit: How many recent reports to return (1–5). Default 5.
            Use a lower number if the user asked about "my most recent" log.

    Returns:
        A structured list of recent reports, each showing the report code,
        date, zone, and a numbered fight list with fight IDs, boss names,
        difficulty, duration, and kill/wipe result.  The agent should use
        the report code and fight ID from this output to call the analysis
        tools.
    """
    try:
        limit = int(reports_limit.strip())
    except (ValueError, AttributeError):
        limit = 5

    try:
        result = await fetch_character_reports(
            character_name=character_name.strip(),
            realm=realm.strip(),
            region=region.strip(),
            reports_limit=limit,
        )
    except ValueError as exc:
        # Invalid region.
        return str(exc)
    except LookupError as exc:
        # Character not found.
        return str(exc)
    except WarcraftLogsPrivateReportError:
        return (
            "This character's logs are private. The player needs to authenticate "
            "via the `/wclauth` command before their reports can be accessed."
        )
    except WarcraftLogsAuthError as exc:
        logger.error("WarcraftLogs auth failure: %s", exc)
        return (
            "Unable to connect to WarcraftLogs — authentication issue. "
            "Check that WARCRAFTLOGS_CLIENT_ID and WARCRAFTLOGS_CLIENT_SECRET "
            "are set correctly."
        )
    except WarcraftLogsAPIError as exc:
        logger.error("WarcraftLogs API error: %s", exc)
        return f"WarcraftLogs returned an error: {exc}"
    except KeyError as exc:
        logger.error("Unexpected WarcraftLogs response shape: missing key %s", exc)
        return "WarcraftLogs returned data in an unexpected format. The API schema may have changed."

    lines: list[str] = []
    char = result["character_name"]
    slug = result["realm_slug"]
    region_out = result["region"].upper()
    lines.append(f"## Recent reports for {char} ({slug} — {region_out})")
    lines.append("")
    lines.append(_format_report_fight_list(result["reports"]))

    if result["errors"]:
        lines.append("\n### Notes")
        for err in result["errors"]:
            lines.append(f"  ⚠ {err}")

    return "\n".join(lines).strip()
