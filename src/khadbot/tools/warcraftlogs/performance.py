"""
WarcraftLogs tool — ``get_warcraftlogs_report``.

Exposes report analysis to the LangChain agent as a structured tool.
All external I/O is isolated inside ``WarcraftLogsClient``; this module
shapes inputs, orchestrates the query sequence, and formats output for the
agent context window.

Data fetched per invocation
---------------------------
1. Fight list + actor roster          (always)
2. Per-player damage totals           (always)
3. Per-ability damage breakdown       (when player_name resolves to a source ID)
4. Parse rankings                     (always, non-fatal on failure)
5. Raw cast event stream              (when player_name provided, non-fatal)

PKCE / private reports
----------------------
Pass ``user_token`` to ``fetch_report_summary()`` (or as a tool arg) to use a
WarcraftLogs user-scoped token.  The token is forwarded to every GraphQL call
so private reports are fully accessible.  The tool surfaces a clear
prompt-to-authenticate message when a private report is hit without a token.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any

from langchain_core.tools import tool

from khadbot.tools.warcraftlogs._client import (
    WarcraftLogsAPIError,
    WarcraftLogsAuthError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
)
from khadbot.tools.warcraftlogs._queries import (
    GET_ABILITY_DAMAGE_TABLE,
    GET_PLAYER_CASTS,
    GET_PLAYER_DAMAGE_TABLE,
    GET_PLAYER_RANKINGS,
    GET_REPORT_FIGHTS,
)

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _duration_seconds(start_ms: int, end_ms: int) -> float:
    return round((end_ms - start_ms) / _MS_PER_SECOND, 1)


def _make_client() -> WarcraftLogsClient:
    return WarcraftLogsClient(
        client_id=os.environ["WARCRAFTLOGS_CLIENT_ID"],
        client_secret=os.environ["WARCRAFTLOGS_CLIENT_SECRET"],
    )


def _find_actor(actors: list[dict], name: str) -> dict | None:
    """Case-insensitive name lookup in the report's actor roster."""
    name_lower = name.lower()
    for actor in actors:
        if actor.get("name", "").lower() == name_lower:
            return actor
    return None


def _summarise_casts(cast_events: list[dict]) -> dict[str, Any]:
    """
    Collapse a raw cast event stream into a per-ability summary.

    Returns cast counts per ability (sorted descending) and a dict of
    cancelled casts — abilities where begincast firings outnumber completions,
    a proxy for movement-interrupted or manually-cancelled casts.
    """
    cast_counts: Counter = Counter()
    begin_counts: Counter = Counter()

    for event in cast_events:
        ability = (event.get("ability") or {}).get("name", "Unknown")
        if event.get("type") == "cast":
            cast_counts[ability] += 1
        elif event.get("type") == "begincast":
            begin_counts[ability] += 1

    cancelled: dict[str, int] = {
        ability: begin_counts[ability] - cast_counts.get(ability, 0)
        for ability in begin_counts
        if begin_counts[ability] > cast_counts.get(ability, 0)
    }

    return {
        "cast_counts": dict(cast_counts.most_common(30)),
        "cancelled_casts": cancelled,
        "total_casts": sum(cast_counts.values()),
    }


# ---------------------------------------------------------------------------
# Core fetch function (testable independently of the @tool decorator)
# ---------------------------------------------------------------------------


async def fetch_report_summary(
    code: str,
    fight_ids: list[int] | None = None,
    player_name: str | None = None,
    user_token: str | None = None,
) -> dict[str, Any]:
    """
    Fetch and assemble a structured report summary.

    Parameters
    ----------
    code:
        WarcraftLogs report code (alphanumeric slug from the URL).
    fight_ids:
        Restrict analysis to these fight IDs.  ``None`` means the full report.
    player_name:
        If provided, ability breakdown and cast analysis are fetched for this
        player.  Must match the character name exactly (case-insensitive).
    user_token:
        PKCE user access token.  Required for private reports.

    Returns
    -------
    dict with keys: title, zone, fights, actors, resolved_player,
    damage_table, ability_breakdown, cast_summary, rankings, errors.
    """
    errors: list[str] = []
    query_kwargs: dict[str, Any] = {"user_token": user_token} if user_token else {}

    async with _make_client() as client:
        # ------------------------------------------------------------------
        # 1. Fight list + actor roster
        # ------------------------------------------------------------------
        fights_data = await client.query(GET_REPORT_FIGHTS, {"code": code}, **query_kwargs)
        report_node = fights_data["reportData"]["report"]

        actors: list[dict] = (report_node.get("masterData") or {}).get("actors") or []
        all_fights: list[dict] = report_node.get("fights") or []
        target_fights = [f for f in all_fights if f["id"] in fight_ids] if fight_ids else all_fights

        fight_summaries = [
            {
                "id": f["id"],
                "name": f["name"],
                "difficulty": f.get("difficulty"),
                "kill": f.get("kill", False),
                "duration_seconds": _duration_seconds(f["startTime"], f["endTime"]),
                "last_phase": f.get("lastPhase"),
            }
            for f in target_fights
        ]

        base_vars: dict[str, Any] = {"code": code}
        if fight_ids:
            base_vars["fightIDs"] = fight_ids

        # Resolve player name → source ID.
        source_id: int | None = None
        resolved_actor: dict | None = None
        if player_name:
            resolved_actor = _find_actor(actors, player_name)
            if resolved_actor:
                source_id = resolved_actor["id"]
            else:
                available = ", ".join(a["name"] for a in actors[:20])
                errors.append(f"Player '{player_name}' not found in this report's roster. Available: {available}")

        # ------------------------------------------------------------------
        # 2. Per-player damage totals
        # ------------------------------------------------------------------
        damage_table: dict | None = None
        try:
            dmg_data = await client.query(GET_PLAYER_DAMAGE_TABLE, base_vars, **query_kwargs)
            damage_table = dmg_data["reportData"]["report"].get("table")
        except WarcraftLogsAPIError as exc:
            logger.warning("Damage table fetch failed: %s", exc)
            errors.append(f"Damage table unavailable: {exc}")

        # ------------------------------------------------------------------
        # 3. Per-ability breakdown (scoped to resolved player)
        # ------------------------------------------------------------------
        ability_breakdown: dict | None = None
        if source_id is not None:
            try:
                ab_data = await client.query(
                    GET_ABILITY_DAMAGE_TABLE,
                    {**base_vars, "sourceID": source_id},
                    **query_kwargs,
                )
                ability_breakdown = ab_data["reportData"]["report"].get("table")
            except WarcraftLogsAPIError as exc:
                logger.warning("Ability breakdown fetch failed: %s", exc)
                errors.append(f"Ability breakdown unavailable: {exc}")

        # ------------------------------------------------------------------
        # 4. Parse rankings
        # ------------------------------------------------------------------
        rankings: dict | None = None
        try:
            rank_data = await client.query(
                GET_PLAYER_RANKINGS,
                {**base_vars, "playerMetric": "dps"},
                **query_kwargs,
            )
            rankings = rank_data["reportData"]["report"].get("rankings")
        except WarcraftLogsAPIError as exc:
            logger.warning("Rankings fetch failed: %s", exc)
            errors.append(f"Rankings unavailable: {exc}")

        # ------------------------------------------------------------------
        # 5. Raw cast events (scoped to resolved player)
        # ------------------------------------------------------------------
        cast_summary: dict | None = None
        if source_id is not None:
            try:
                cast_vars: dict[str, Any] = {**base_vars, "sourceID": source_id}
                if target_fights:
                    cast_vars["startTime"] = float(min(f["startTime"] for f in target_fights))
                    cast_vars["endTime"] = float(max(f["endTime"] for f in target_fights))

                cast_data = await client.query(GET_PLAYER_CASTS, cast_vars, **query_kwargs)
                events_node = cast_data["reportData"]["report"]["events"]
                raw_events: list[dict] = events_node.get("data", [])
                next_page = events_node.get("nextPageTimestamp")

                cast_summary = _summarise_casts(raw_events)
                if next_page:
                    cast_summary["truncated"] = True
                    errors.append(
                        "Cast event stream was truncated (>2000 events). Cast counts reflect the first page only."
                    )
            except WarcraftLogsAPIError as exc:
                logger.warning("Cast events fetch failed: %s", exc)
                errors.append(f"Cast analysis unavailable: {exc}")

    return {
        "title": report_node.get("title", ""),
        "zone": (report_node.get("zone") or {}).get("name", "Unknown"),
        "fights": fight_summaries,
        "actors": actors,
        "resolved_player": resolved_actor,
        "damage_table": damage_table,
        "ability_breakdown": ability_breakdown,
        "cast_summary": cast_summary,
        "rankings": rankings,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


@tool
async def get_warcraftlogs_report(
    report_id: str,
    fight_ids: str = "",
    player_name: str = "",
    user_token: str = "",
) -> str:
    """
    Fetch performance data from a WarcraftLogs report.

    Use this tool when the user shares a WarcraftLogs report URL or code and
    wants to understand how they or their group performed on a boss encounter.

    Args:
        report_id: The WarcraftLogs report code — the alphanumeric slug at the
            end of a warcraftlogs.com URL, e.g. ``"abc123XYZ"`` from
            ``https://www.warcraftlogs.com/reports/abc123XYZ``.
        fight_ids: Optional comma-separated fight IDs to restrict analysis to
            specific pulls, e.g. ``"3,4,5"``. Leave empty for the full report.
        player_name: Optional character name to focus ability breakdown and
            cast analysis on a specific player (case-insensitive). Leave empty
            for group-level analysis only.
        user_token: Optional WarcraftLogs user OAuth token. Required when the
            report is set to private. Leave empty for public/unlisted reports.

    Returns:
        Structured text covering fight results, damage rankings, ability
        breakdown (if player scoped), cast analysis (if player scoped), and
        parse percentiles. Returns a user-friendly error string when the report
        is private without a token, not found, or the API is unavailable.
    """
    parsed_fight_ids: list[int] | None = None
    if fight_ids.strip():
        try:
            parsed_fight_ids = [int(x.strip()) for x in fight_ids.split(",") if x.strip()]
        except ValueError:
            return (
                "I couldn't parse the fight IDs you provided. "
                "Please supply a comma-separated list of integers, e.g. '3,4,5'."
            )

    try:
        summary = await fetch_report_summary(
            code=report_id,
            fight_ids=parsed_fight_ids,
            player_name=player_name.strip() or None,
            user_token=user_token.strip() or None,
        )
    except WarcraftLogsPrivateReportError:
        return (
            "This WarcraftLogs report is private. To analyze private logs, the log owner "
            "needs to authenticate with WarcraftLogs via the `/wclauth` command so their "
            "user token can be used."
        )
    except WarcraftLogsAuthError as exc:
        logger.error("WarcraftLogs auth failure: %s", exc)
        return (
            "I'm unable to connect to WarcraftLogs due to an authentication issue. "
            "Please verify that WARCRAFTLOGS_CLIENT_ID and WARCRAFTLOGS_CLIENT_SECRET "
            "are set correctly in the environment."
        )
    except WarcraftLogsAPIError as exc:
        logger.error("WarcraftLogs API error: %s", exc)
        return f"WarcraftLogs returned an error: {exc}"
    except KeyError as exc:
        logger.error("Unexpected WarcraftLogs response shape — missing key: %s", exc)
        return (
            "WarcraftLogs returned data in an unexpected format. "
            "The report code may be invalid, or the API schema may have changed."
        )

    return _format_summary(summary)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_summary(summary: dict[str, Any]) -> str:
    lines: list[str] = []

    lines.append(f"## WarcraftLogs Report: {summary['title']}")
    lines.append(f"Zone: {summary['zone']}")
    lines.append("")

    # Fights
    if summary["fights"]:
        lines.append("### Pulls analyzed")
        for f in summary["fights"]:
            result = "✓ Kill" if f["kill"] else f"✗ Wipe (phase {f['last_phase'] or '?'})"
            diff = f.get("difficulty") or "Normal"
            lines.append(f"  Fight {f['id']}: {f['name']} ({diff}) — {f['duration_seconds']}s — {result}")
        lines.append("")

    # Per-player damage totals
    if summary["damage_table"]:
        table = summary["damage_table"]
        entries = table.get("data", {}).get("entries", []) if isinstance(table, dict) else []
        if entries:
            lines.append("### Damage done (sorted by total)")
            for entry in entries[:15]:
                name = entry.get("name", "Unknown")
                spec = entry.get("type", "")
                total = entry.get("total", 0)
                active_ms = max(entry.get("activeTime", 1), 1)
                dps = round(total / (active_ms / _MS_PER_SECOND))
                active_pct = entry.get("activeTimeReduced", entry.get("pct", 0))
                lines.append(f"  {name} ({spec}): {total:,} total — ~{dps:,} DPS — {active_pct:.1f}% active")
            lines.append("")

    # Ability breakdown (player-scoped)
    if summary.get("ability_breakdown"):
        ab = summary["ability_breakdown"]
        ab_entries = ab.get("data", {}).get("entries", []) if isinstance(ab, dict) else []
        player_label = (summary.get("resolved_player") or {}).get("name", "Player")
        if ab_entries:
            lines.append(f"### Ability breakdown — {player_label}")
            for entry in ab_entries[:20]:
                ability_name = entry.get("name", "Unknown")
                total = entry.get("total", 0)
                casts = entry.get("uses", entry.get("casts", 0))
                hit_count = entry.get("hitCount", 0)
                avg = round(total / max(casts, 1))
                lines.append(f"  {ability_name}: {total:,} total — {casts} casts ({hit_count} hits) — ~{avg:,} avg")
            lines.append("")

    # Cast summary (player-scoped)
    if summary.get("cast_summary"):
        cs = summary["cast_summary"]
        player_label = (summary.get("resolved_player") or {}).get("name", "Player")
        lines.append(f"### Cast summary — {player_label}")
        lines.append(f"  Total completed casts: {cs.get('total_casts', 0)}")
        if cs.get("cast_counts"):
            lines.append("  Top abilities by cast count:")
            for ability, count in list(cs["cast_counts"].items())[:10]:
                lines.append(f"    {ability}: {count}×")
        if cs.get("cancelled_casts"):
            lines.append("  Cancelled / interrupted casts:")
            for ability, count in cs["cancelled_casts"].items():
                lines.append(f"    {ability}: {count}× cancelled")
        if cs.get("truncated"):
            lines.append("  ⚠ Cast stream truncated — counts reflect first 2000 events only.")
        lines.append("")

    # Rankings
    if summary["rankings"]:
        rankings = summary["rankings"]
        player_rankings = rankings.get("data", []) if isinstance(rankings, dict) else []
        if player_rankings:
            lines.append("### Parse rankings (DPS)")
            for entry in player_rankings[:15]:
                name = entry.get("name", "Unknown")
                spec = entry.get("spec", "")
                rank_pct = entry.get("rankPercent", 0)
                all_stars_pts = (
                    entry.get("allStars", {}).get("points", 0) if isinstance(entry.get("allStars"), dict) else 0
                )
                line = f"  {name} ({spec}): {rank_pct:.1f}th percentile"
                if all_stars_pts:
                    line += f" — {all_stars_pts:.1f} All-Stars pts"
                lines.append(line)
            lines.append("")

    # Non-fatal errors / warnings
    if summary["errors"]:
        lines.append("### Notes")
        for err in summary["errors"]:
            lines.append(f"  ⚠ {err}")
        lines.append("")

    return "\n".join(lines).strip()
