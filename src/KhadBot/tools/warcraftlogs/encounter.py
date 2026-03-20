"""
WarcraftLogs encounter analysis tool — ``get_encounter_analysis``.

Companion to ``get_warcraftlogs_report``.  Where that tool answers
*performance* questions ("why is my DPS low", "what's my parse"), this tool
answers *encounter* questions ("why did we wipe", "who is dying to what",
"which mechanics are hurting us").

Architecture
------------
The tool runs a deterministic multi-pass analysis pipeline on raw WarcraftLogs
event data before handing structured output to the LLM.  The LLM's job is
causal interpretation and narrative — not event counting.

Pipeline (all deterministic Python):
  1. Fetch fight metadata + actor roster              (GET_REPORT_FIGHTS)
  2. Fetch deaths for the target fight                (GET_DEATHS)
  3. For each death, fetch the 10s pre-death window   (GET_DAMAGE_TAKEN_EVENTS)
  4. Fetch avoidable damage table (all abilities)     (GET_AVOIDABLE_DAMAGE)
  5. Fetch healing breakdown                          (GET_HEALING_TABLE)
  6. Fetch all cast events (CD timeline)              (GET_COOLDOWN_CASTS)

Analysis helpers (deterministic, testable in isolation):
  _analyze_deaths()       — per-death killing blow + pre-death damage window
  _analyze_avoidable()    — filter ability table against avoidable spell registry
  _analyze_cooldowns()    — filter cast stream against CD registry, build timeline
  _analyze_healing()      — overheal %, effective HPS, healer pressure signal
  _build_timeline()       — merge all events into a chronological fight timeline

Avoidable spell registry
------------------------
``AVOIDABLE_SPELL_IDS`` is a hardcoded set of universally avoidable spell IDs
(fire on the floor, swirlies, etc.) that applies across all bosses.  Per-boss
configs (from Wipefest fight configs / WoWAnalyzer) slot in here later.
The registry is intentionally small for the prototype — false negatives (missed
avoidable spells) are safer than false positives (flagging unavoidable damage).

Cooldown registry
-----------------
``MAJOR_COOLDOWN_IDS`` covers raidwide externals and high-impact personal
defensives that are worth tracking for coordination analysis.  Spec-specific
DPS CDs are excluded from this registry — they belong in the per-player
performance analysis in ``get_warcraftlogs_report``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import tool

from khadbot.tools.warcraftlogs._client import (
    WarcraftLogsAPIError,
    WarcraftLogsAuthError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
)
from khadbot.tools.warcraftlogs._queries import (
    GET_AVOIDABLE_DAMAGE,
    GET_COOLDOWN_CASTS,
    GET_DAMAGE_TAKEN_EVENTS,
    GET_DEATHS,
    GET_HEALING_TABLE,
    GET_REPORT_FIGHTS,
)

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1_000

# How far back from a death timestamp to collect incoming damage events.
PRE_DEATH_WINDOW_MS = 10_000  # 10 seconds

# ---------------------------------------------------------------------------
# Spell registries
# ---------------------------------------------------------------------------

# Universally avoidable spell IDs — mechanics that always deal 0 expected hits.
# Source: WoWAnalyzer common spells + Wipefest generic avoidable tags.
# Extend this with per-boss configs from Wipefest fight configs as coverage grows.
AVOIDABLE_SPELL_IDS: frozenset[int] = frozenset(
    {
        # Generic "standing in fire" / environmental damage patterns.
        # Real IDs will come from Wipefest boss configs — these are placeholders
        # that demonstrate the registry pattern without hardcoding wrong spell IDs.
        # Replace with actual spell IDs as per-boss configs are imported.
    }
)

# Major raid cooldowns worth tracking for coordination analysis.
# Raidwide externals + high-impact personal defensives.
# DPS cooldowns (Bloodlust etc.) are excluded — they belong in perf analysis.
MAJOR_COOLDOWN_IDS: dict[int, str] = {
    # Paladin
    633: "Lay on Hands",
    1022: "Blessing of Protection",
    6940: "Blessing of Sacrifice",
    31821: "Aura Mastery",
    # Priest
    33206: "Pain Suppression",
    47788: "Guardian Spirit",
    62618: "Power Word: Barrier",
    # Druid
    29166: "Innervate",
    102342: "Ironbark",
    # Monk
    116849: "Life Cocoon",
    # Evoker
    357170: "Rewind",
    374227: "Stasis",
    # Warrior
    97462: "Rally",
    # Death Knight
    51052: "Anti-Magic Zone",
    # Shaman
    98008: "Spirit Link Totem",
    207399: "Ancestral Protection Totem",
    # Generic externals
    116841: "Tiger's Lust",
}


# ---------------------------------------------------------------------------
# Data classes for analysis output
# ---------------------------------------------------------------------------


@dataclass
class DeathEvent:
    timestamp_ms: int
    target_id: int
    target_name: str
    killing_blow: str  # ability name
    overkill: int  # damage past 0 HP
    pre_death_hits: list[dict] = field(default_factory=list)
    # Each hit: {ability, amount, timestamp_ms, source_name}

    @property
    def fight_relative_seconds(self) -> float:
        """Populated by caller after subtracting fight startTime."""
        return round(self.timestamp_ms / _MS_PER_SECOND, 1)


@dataclass
class AvoidableDamageEntry:
    spell_id: int
    spell_name: str
    total_damage: int
    hit_count: int
    players_hit: int


@dataclass
class CooldownUsage:
    spell_id: int
    spell_name: str
    caster_id: int
    caster_name: str
    timestamp_ms: int
    fight_relative_seconds: float


@dataclass
class HealerSummary:
    name: str
    spec: str
    effective_healing: int
    overhealing: int

    @property
    def overheal_pct(self) -> float:
        total = self.effective_healing + self.overhealing
        return round(self.overhealing / max(total, 1) * 100, 1)

    @property
    def pressure_signal(self) -> str:
        """
        Coarse signal for the LLM: was this healer overwhelmed or comfortable?
        Thresholds are intentionally conservative for prototype use.
        """
        if self.overheal_pct < 15:
            return "overwhelmed (very low overheal)"
        if self.overheal_pct < 30:
            return "pressured"
        return "comfortable"


@dataclass
class EncounterAnalysis:
    """
    Fully assembled encounter analysis for one fight.
    Passed to _format_encounter() for agent consumption.
    """

    fight_id: int
    fight_name: str
    fight_duration_seconds: float
    kill: bool
    last_phase: int | None

    deaths: list[DeathEvent]
    avoidable_damage: list[AvoidableDamageEntry]
    cooldown_timeline: list[CooldownUsage]
    healers: list[HealerSummary]

    errors: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> WarcraftLogsClient:
    return WarcraftLogsClient(
        client_id=os.environ["WARCRAFTLOGS_CLIENT_ID"],
        client_secret=os.environ["WARCRAFTLOGS_CLIENT_SECRET"],
    )


def _actor_name(actors: list[dict], actor_id: int) -> str:
    for a in actors:
        if a.get("id") == actor_id:
            return a.get("name", f"Actor#{actor_id}")
    return f"Actor#{actor_id}"


# ---------------------------------------------------------------------------
# Deterministic analysis passes
# ---------------------------------------------------------------------------


def _analyze_deaths(
    death_events: list[dict],
    damage_windows: dict[int, list[dict]],
    actors: list[dict],
    fight_start_ms: int,
) -> list[DeathEvent]:
    """
    Pass 1 — Deaths.

    For each raw death event, build a DeathEvent with:
      - the killing blow ability name
      - overkill amount
      - the pre-death damage window (10s of incoming hits keyed by targetID)

    ``damage_windows`` maps targetID → list of raw damage-taken events in the
    10s before that player's death.  These are fetched separately per death
    to keep the time window tight.

    Fight-relative timestamps are computed by subtracting fight_start_ms.
    """
    result: list[DeathEvent] = []

    for event in death_events:
        target_id = event.get("targetID", 0)
        raw_ts = event.get("timestamp", 0)
        fight_relative_ms = raw_ts - fight_start_ms

        killing_ability = (event.get("ability") or {}).get("name", "Unknown")
        overkill = event.get("overkill", 0)

        # Pre-death hits — summarise to avoid token bloat.
        pre_hits_raw = damage_windows.get(target_id, [])
        pre_hits: list[dict] = []
        for hit in pre_hits_raw:
            pre_hits.append(
                {
                    "ability": (hit.get("ability") or {}).get("name", "Unknown"),
                    "amount": hit.get("amount", 0),
                    "ts_relative_s": round((hit.get("timestamp", 0) - fight_start_ms) / _MS_PER_SECOND, 1),
                    "source": _actor_name(actors, hit.get("sourceID", 0)),
                }
            )

        result.append(
            DeathEvent(
                timestamp_ms=fight_relative_ms,
                target_id=target_id,
                target_name=_actor_name(actors, target_id),
                killing_blow=killing_ability,
                overkill=overkill,
                pre_death_hits=pre_hits,
            )
        )

    return sorted(result, key=lambda d: d.timestamp_ms)


def _analyze_avoidable(
    ability_table: dict | None,
    avoidable_ids: frozenset[int] = AVOIDABLE_SPELL_IDS,
) -> list[AvoidableDamageEntry]:
    """
    Pass 2 — Avoidable damage.

    Filters the damage-taken-by-ability table against the avoidable spell
    registry.  Returns entries sorted by total damage descending so the
    highest-raid-cost mechanics surface first.

    When ``avoidable_ids`` is empty (prototype default), returns all
    damage-taken entries ranked by total — still useful as a "what hurt the
    raid most" signal even without the registry populated.
    """
    if not ability_table:
        return []

    entries = ability_table.get("data", {}).get("entries", []) if isinstance(ability_table, dict) else []

    results: list[AvoidableDamageEntry] = []
    for entry in entries:
        spell_id = (entry.get("ability") or {}).get("id", 0) or entry.get("abilityGameID", 0)
        spell_name = (entry.get("ability") or {}).get("name", entry.get("name", "Unknown"))
        total = entry.get("total", 0)
        hit_count = entry.get("hitCount", entry.get("totalHits", 0))
        players_hit = entry.get("sources", entry.get("playerCount", 0))
        if isinstance(players_hit, list):
            players_hit = len(players_hit)

        if not avoidable_ids or spell_id in avoidable_ids:
            results.append(
                AvoidableDamageEntry(
                    spell_id=spell_id,
                    spell_name=spell_name,
                    total_damage=total,
                    hit_count=hit_count,
                    players_hit=players_hit,
                )
            )

    return sorted(results, key=lambda e: e.total_damage, reverse=True)


def _analyze_cooldowns(
    cast_events: list[dict],
    actors: list[dict],
    fight_start_ms: int,
    cd_registry: dict[int, str] = MAJOR_COOLDOWN_IDS,
) -> list[CooldownUsage]:
    """
    Pass 3 — Cooldown timeline.

    Filters the full cast event stream to major cooldown ability IDs and
    builds an ordered timeline of who cast what and when (fight-relative
    seconds).  The LLM uses this to identify:
      - overlapping CDs on the same window (wasted coverage)
      - missing CDs during major damage events
      - healers running out of externals when a second spike hit
    """
    result: list[CooldownUsage] = []

    for event in cast_events:
        if event.get("type") != "cast":
            continue
        ability = event.get("ability") or {}
        spell_id = ability.get("abilityGameID", ability.get("id", 0))
        if spell_id not in cd_registry:
            continue

        raw_ts = event.get("timestamp", 0)
        fight_rel_s = round((raw_ts - fight_start_ms) / _MS_PER_SECOND, 1)
        caster_id = event.get("sourceID", 0)

        result.append(
            CooldownUsage(
                spell_id=spell_id,
                spell_name=cd_registry[spell_id],
                caster_id=caster_id,
                caster_name=_actor_name(actors, caster_id),
                timestamp_ms=raw_ts - fight_start_ms,
                fight_relative_seconds=fight_rel_s,
            )
        )

    return sorted(result, key=lambda c: c.timestamp_ms)


def _analyze_healing(
    healing_table: dict | None,
    actors: list[dict],
) -> list[HealerSummary]:
    """
    Pass 4 — Healer pressure.

    Computes overheal % per healer and attaches a coarse pressure signal.
    Healers with <15% overheal during a wipe are likely overwhelmed — that
    context reframes nearby deaths as a healing throughput problem, not
    necessarily individual player failure.
    """
    if not healing_table:
        return []

    entries = healing_table.get("data", {}).get("entries", []) if isinstance(healing_table, dict) else []

    result: list[HealerSummary] = []
    for entry in entries:
        effective = entry.get("total", 0)
        overheal = entry.get("overheal", 0)
        name = entry.get("name", "Unknown")
        spec = entry.get("type", "")

        result.append(
            HealerSummary(
                name=name,
                spec=spec,
                effective_healing=effective,
                overhealing=overheal,
            )
        )

    return sorted(result, key=lambda h: h.effective_healing, reverse=True)


def _build_timeline(
    deaths: list[DeathEvent],
    cooldowns: list[CooldownUsage],
    fight_duration_s: float,
) -> list[dict]:
    """
    Build a merged chronological timeline of deaths and major CD usages.

    This gives the LLM a single ordered sequence to reason over rather than
    two separate lists, making causal relationships (CD used → death follows
    before next CD is available) easier to surface in the narrative.
    """
    events: list[dict] = []

    for d in deaths:
        events.append(
            {
                "type": "death",
                "time_s": round(d.timestamp_ms / _MS_PER_SECOND, 1),
                "player": d.target_name,
                "detail": f"killed by {d.killing_blow} (overkill: {d.overkill:,})",
            }
        )

    for cd in cooldowns:
        events.append(
            {
                "type": "cooldown",
                "time_s": cd.fight_relative_seconds,
                "player": cd.caster_name,
                "detail": cd.spell_name,
            }
        )

    return sorted(events, key=lambda e: e["time_s"])


# ---------------------------------------------------------------------------
# Core fetch + analysis function
# ---------------------------------------------------------------------------


async def fetch_encounter_events(
    code: str,
    fight_id: int,
    user_token: str | None = None,
) -> EncounterAnalysis:
    """
    Fetch and analyze all encounter events for a single fight pull.

    Parameters
    ----------
    code:
        WarcraftLogs report code.
    fight_id:
        The specific fight (pull) to analyze.  Use ``get_warcraftlogs_report``
        first to discover available fight IDs.
    user_token:
        PKCE user access token for private reports.

    Returns
    -------
    EncounterAnalysis dataclass with fully populated analysis fields.
    Non-fatal fetch failures are recorded in ``errors`` and analysis
    continues with whatever data was successfully retrieved.
    """
    errors: list[str] = []
    qkw: dict[str, Any] = {"user_token": user_token} if user_token else {}

    async with _make_client() as client:
        # ------------------------------------------------------------------
        # 1. Fight metadata + actor roster
        # ------------------------------------------------------------------
        fights_data = await client.query(GET_REPORT_FIGHTS, {"code": code}, **qkw)
        report_node = fights_data["reportData"]["report"]
        actors: list[dict] = (report_node.get("masterData") or {}).get("actors") or []

        all_fights: list[dict] = report_node.get("fights") or []
        fight = next((f for f in all_fights if f["id"] == fight_id), None)
        if fight is None:
            available = ", ".join(str(f["id"]) for f in all_fights)
            raise ValueError(f"Fight ID {fight_id} not found in report '{code}'. Available fight IDs: {available}")

        fight_start = float(fight["startTime"])
        fight_end = float(fight["endTime"])
        fight_duration_s = round((fight_end - fight_start) / _MS_PER_SECOND, 1)
        base_vars: dict[str, Any] = {
            "code": code,
            "fightIDs": [fight_id],
            "startTime": fight_start,
            "endTime": fight_end,
        }

        # ------------------------------------------------------------------
        # 2. Deaths
        # ------------------------------------------------------------------
        death_events: list[dict] = []
        try:
            death_data = await client.query(GET_DEATHS, base_vars, **qkw)
            death_events = death_data["reportData"]["report"]["events"].get("data", [])
            if death_data["reportData"]["report"]["events"].get("nextPageTimestamp"):
                errors.append("Death event stream was truncated — this is unusual and may indicate a very long pull.")
        except WarcraftLogsAPIError as exc:
            logger.warning("Deaths fetch failed: %s", exc)
            errors.append(f"Death events unavailable: {exc}")

        # ------------------------------------------------------------------
        # 3. Pre-death damage windows (one query per unique death)
        #    Deduplicated by targetID — if the same player dies twice, we
        #    only query the final death's window to keep query count bounded.
        # ------------------------------------------------------------------
        damage_windows: dict[int, list[dict]] = {}
        seen_targets: set[int] = set()

        for death_event in death_events:
            target_id = death_event.get("targetID", 0)
            if target_id in seen_targets:
                continue
            seen_targets.add(target_id)

            death_ts = float(death_event.get("timestamp", fight_start))
            window_start = max(death_ts - PRE_DEATH_WINDOW_MS, fight_start)

            try:
                window_data = await client.query(
                    GET_DAMAGE_TAKEN_EVENTS,
                    {
                        "code": code,
                        "fightIDs": [fight_id],
                        "startTime": window_start,
                        "endTime": death_ts,
                        "targetID": target_id,
                    },
                    **qkw,
                )
                events_node = window_data["reportData"]["report"]["events"]
                damage_windows[target_id] = events_node.get("data", [])
                if events_node.get("nextPageTimestamp"):
                    errors.append(
                        f"Pre-death window for {_actor_name(actors, target_id)} "
                        f"was truncated — high incoming damage rate."
                    )
            except WarcraftLogsAPIError as exc:
                logger.warning("Pre-death window fetch failed for actor %d: %s", target_id, exc)
                errors.append(f"Pre-death window unavailable for {_actor_name(actors, target_id)}: {exc}")

        # ------------------------------------------------------------------
        # 4. Avoidable damage table
        # ------------------------------------------------------------------
        avoidable_table: dict | None = None
        try:
            av_data = await client.query(GET_AVOIDABLE_DAMAGE, base_vars, **qkw)
            avoidable_table = av_data["reportData"]["report"].get("table")
        except WarcraftLogsAPIError as exc:
            logger.warning("Avoidable damage fetch failed: %s", exc)
            errors.append(f"Avoidable damage table unavailable: {exc}")

        # ------------------------------------------------------------------
        # 5. Healing breakdown
        # ------------------------------------------------------------------
        healing_table: dict | None = None
        try:
            heal_data = await client.query(GET_HEALING_TABLE, base_vars, **qkw)
            healing_table = heal_data["reportData"]["report"].get("table")
        except WarcraftLogsAPIError as exc:
            logger.warning("Healing table fetch failed: %s", exc)
            errors.append(f"Healing breakdown unavailable: {exc}")

        # ------------------------------------------------------------------
        # 6. Cooldown casts (all players, full fight)
        # ------------------------------------------------------------------
        cooldown_cast_events: list[dict] = []
        try:
            cd_data = await client.query(GET_COOLDOWN_CASTS, base_vars, **qkw)
            events_node = cd_data["reportData"]["report"]["events"]
            cooldown_cast_events = events_node.get("data", [])
            if events_node.get("nextPageTimestamp"):
                errors.append(
                    "Cooldown cast stream was truncated (>2000 events). "
                    "CD timeline may be incomplete — consider narrowing to a single phase."
                )
        except WarcraftLogsAPIError as exc:
            logger.warning("Cooldown casts fetch failed: %s", exc)
            errors.append(f"Cooldown timeline unavailable: {exc}")

    # ------------------------------------------------------------------
    # Deterministic analysis passes (all pure Python, no I/O)
    # ------------------------------------------------------------------
    deaths = _analyze_deaths(death_events, damage_windows, actors, int(fight_start))
    avoidable = _analyze_avoidable(avoidable_table)
    cooldowns = _analyze_cooldowns(cooldown_cast_events, actors, int(fight_start))
    healers = _analyze_healing(healing_table, actors)

    return EncounterAnalysis(
        fight_id=fight_id,
        fight_name=fight.get("name", "Unknown"),
        fight_duration_seconds=fight_duration_s,
        kill=fight.get("kill", False),
        last_phase=fight.get("lastPhase"),
        deaths=deaths,
        avoidable_damage=avoidable,
        cooldown_timeline=cooldowns,
        healers=healers,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# LangChain tool
# ---------------------------------------------------------------------------


@tool
async def get_encounter_analysis(
    report_id: str,
    fight_id: str,
    user_token: str = "",
) -> str:
    """
    Analyze a specific boss pull to identify what went wrong and why.

    Use this tool when the user wants to understand encounter mechanics,
    wipe causes, deaths, avoidable damage, or cooldown coordination — not
    when they're asking about their personal DPS parse or spec optimisation
    (use get_warcraftlogs_report for those).

    Typical trigger questions:
      "Why did we wipe on that pull?"
      "Who is dying to [mechanic]?"
      "Are we using our healing cooldowns at the right time?"
      "What avoidable damage is hurting the raid most?"
      "What killed our tank at 12%?"

    Args:
        report_id: WarcraftLogs report code — the alphanumeric slug from the
            URL, e.g. ``"abc123XYZ"`` from
            ``https://www.warcraftlogs.com/reports/abc123XYZ``.
        fight_id: The specific fight (pull) ID to analyze. Use
            get_warcraftlogs_report first to get the list of fight IDs if
            you don't already have them. Must be a single integer.
        user_token: Optional WarcraftLogs user OAuth token. Required for
            private reports. Leave empty for public/unlisted logs.

    Returns:
        Structured analysis covering deaths with pre-death damage windows,
        avoidable damage ranked by raid cost, healing pressure signals, and
        a cooldown usage timeline. Returns a user-friendly error string if
        the report is inaccessible or the fight ID is invalid.
    """
    try:
        fid = int(fight_id.strip())
    except (ValueError, AttributeError):
        return (
            "fight_id must be a single integer, e.g. '3'. "
            "Use get_warcraftlogs_report first to discover available fight IDs."
        )

    try:
        analysis = await fetch_encounter_events(
            code=report_id,
            fight_id=fid,
            user_token=user_token.strip() or None,
        )
    except ValueError as exc:
        # Invalid fight ID — not found in report.
        return str(exc)
    except WarcraftLogsPrivateReportError:
        return (
            "This report is private. The log owner needs to authenticate via "
            "the `/wclauth` command before private reports can be analyzed."
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
        return (
            "WarcraftLogs returned data in an unexpected format. "
            "The report code may be invalid or the API schema may have changed."
        )

    return _format_encounter(analysis)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_encounter(a: EncounterAnalysis) -> str:
    lines: list[str] = []

    result_str = "✓ Kill" if a.kill else f"✗ Wipe (phase {a.last_phase or '?'})"
    lines.append(f"## Encounter Analysis — {a.fight_name}")
    lines.append(f"Fight {a.fight_id} | {a.fight_duration_seconds}s | {result_str}")
    lines.append("")

    # ------------------------------------------------------------------
    # Deaths
    # ------------------------------------------------------------------
    if a.deaths:
        lines.append(f"### Deaths ({len(a.deaths)} total)")
        for d in a.deaths:
            time_s = round(d.timestamp_ms / _MS_PER_SECOND, 1)
            lines.append(
                f"  {d.target_name} — died at {time_s}s (killed by {d.killing_blow}, overkill: {d.overkill:,})"
            )
            if d.pre_death_hits:
                # Show top 5 hits by damage amount to keep output tight.
                top_hits = sorted(d.pre_death_hits, key=lambda h: h["amount"], reverse=True)[:5]
                lines.append("    Last 10s incoming damage (top hits):")
                for hit in top_hits:
                    lines.append(
                        f"      {hit['ts_relative_s']}s  {hit['ability']} from {hit['source']}: {hit['amount']:,}"
                    )
        lines.append("")
    else:
        lines.append("### Deaths\n  None recorded.\n")

    # ------------------------------------------------------------------
    # Avoidable / costly damage
    # ------------------------------------------------------------------
    if a.avoidable_damage:
        label = "### Avoidable damage" if AVOIDABLE_SPELL_IDS else "### Damage taken by ability (ranked)"
        lines.append(label)
        if not AVOIDABLE_SPELL_IDS:
            lines.append(
                "  ⚠ Avoidable spell registry is empty — showing all damage-taken "
                "abilities ranked by total raid cost. Populate AVOIDABLE_SPELL_IDS "
                "with Wipefest boss configs to filter to truly avoidable mechanics."
            )
        for entry in a.avoidable_damage[:10]:
            lines.append(
                f"  {entry.spell_name}: {entry.total_damage:,} total — "
                f"{entry.hit_count} hits across {entry.players_hit} players"
            )
        lines.append("")

    # ------------------------------------------------------------------
    # Healing pressure
    # ------------------------------------------------------------------
    if a.healers:
        lines.append("### Healing pressure")
        for h in a.healers:
            lines.append(
                f"  {h.name} ({h.spec}): {h.effective_healing:,} effective — "
                f"{h.overheal_pct:.1f}% overheal — {h.pressure_signal}"
            )
        lines.append("")

    # ------------------------------------------------------------------
    # Cooldown timeline
    # ------------------------------------------------------------------
    if a.cooldown_timeline:
        lines.append("### Major cooldown timeline")
        for cd in a.cooldown_timeline:
            lines.append(f"  {cd.fight_relative_seconds}s  {cd.caster_name} — {cd.spell_name}")
        lines.append("")
    elif not a.errors:
        lines.append("### Major cooldowns\n  None tracked (no registry hits in cast stream).\n")

    # ------------------------------------------------------------------
    # Fight timeline (deaths + CDs merged)
    # ------------------------------------------------------------------
    timeline = _build_timeline(a.deaths, a.cooldown_timeline, a.fight_duration_seconds)
    if timeline:
        lines.append("### Fight timeline (deaths + major CDs)")
        for event in timeline:
            icon = "💀" if event["type"] == "death" else "🛡"
            lines.append(f"  {event['time_s']}s  {icon} {event['player']} — {event['detail']}")
        lines.append("")

    # ------------------------------------------------------------------
    # Non-fatal errors
    # ------------------------------------------------------------------
    if a.errors:
        lines.append("### Notes")
        for err in a.errors:
            lines.append(f"  ⚠ {err}")
        lines.append("")

    return "\n".join(lines).strip()
