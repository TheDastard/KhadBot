"""
encounter/query_tools.py

Analyst query tools (plan section 6.6). They read only from the event store and annotations,
through ``EncounterData``; none of them can reach WCL.

The pull scope and data come from the run config (``config["configurable"]["scope"]``, an
``AnalystScope``), never from tool arguments, so the model cannot query pulls outside its request.
Every tool returns a capped ``ToolResult`` envelope as JSON (25 rows, 4,000 characters) and every row
carries the evidence ref it resolves from. Rows are the same dicts ``EncounterData.resolve`` returns
for their ref, or a subset of their fields.

Each tool is a thin wrapper over a ``*_result(scope, ...)`` function that holds the logic and can be
tested without LangChain. Tool docstrings are prompt text: they feed ``prompt_version``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

import polars as pl
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from khadbot.encounter.digest import PullRecords, render_digest
from khadbot.encounter.evidence import EncounterData, event_row, mechanic_detail, pull_summary
from khadbot.encounter.models import Confidence, PhaseSpan, ToolResult, make_ref

ROW_CAP = 25
CHAR_CAP = 4_000
MAX_EVENT_WINDOW_MS = 30_000

CompareMetric = Literal["deaths", "mechanic_failures", "phase_reached", "duration"]


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalystScope:
    """What one analysis request may read: these pulls, through this data."""

    data: EncounterData
    pulls: tuple[str, ...]

    def pull_key(self, pull: str) -> str | None:
        """Normalize ``pull`` to an in-scope pull_key. A bare fight ID works when it is unambiguous."""
        pull = str(pull).strip()
        if pull in self.pulls:
            return pull
        matches = [k for k in self.pulls if k.rpartition(":")[2] == pull]
        return matches[0] if len(matches) == 1 else None


def scope_from(config: RunnableConfig) -> AnalystScope:
    scope = (config.get("configurable") or {}).get("scope")
    if not isinstance(scope, AnalystScope):
        raise RuntimeError("analyst tools need an AnalystScope in config['configurable']['scope']")
    return scope


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def fail(note: str) -> ToolResult:
    return ToolResult(ok=False, data=[], refs=[], note=note)


def envelope(
    rows: list[dict[str, Any]],
    *,
    wrap: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
    head_refs: Iterable[str] = (),
    hint: str = "Narrow the query to see the rest.",
    note: str | None = None,
    total: int | None = None,
) -> ToolResult:
    """
    Cap ``rows`` at ``ROW_CAP`` and the JSON at ``CHAR_CAP`` (dropping rows from the end, keeping at
    least one), and collect refs. ``wrap`` places the kept rows inside a dict (for a header record
    whose refs are ``head_refs``). ``total`` is the row count before any capping, for callers that
    pass only the first rows so a large result is never built in full.
    """
    total = len(rows) if total is None else max(total, len(rows))
    keep = min(total, ROW_CAP)
    while True:
        kept = rows[:keep]
        truncated = keep < total
        notes = [n for n in (note, f"Showing {keep} of {total} rows. {hint}" if truncated else None) if n]
        refs = list(dict.fromkeys([*head_refs, *(r["ref"] for r in kept)]))
        result = ToolResult(
            ok=True,
            data=wrap(kept) if wrap else kept,
            refs=refs,
            truncated=truncated,
            total=total,
            note=" ".join(notes) or None,
        )
        if keep <= 1 or len(result.model_dump_json()) <= CHAR_CAP:
            return result
        keep -= 1


# ---------------------------------------------------------------------------
# Shared lookups
# ---------------------------------------------------------------------------


def _records(scope: AnalystScope, pull: str) -> PullRecords | ToolResult:
    key = scope.pull_key(pull)
    if key is None:
        return fail(f"Pull {pull!r} is not in this request's scope. Pulls in scope: {', '.join(scope.pulls)}.")
    try:
        return scope.data.records(key)
    except KeyError:
        return fail(f"Pull {key} has not been ingested, so there is no data for it.")


def _actor_id(r: PullRecords, actor: str) -> int | ToolResult:
    wanted = actor.strip().casefold()
    match = next((a for a in r.actors if a.name.casefold() == wanted), None)
    if match is None:
        players = sorted(a.name for a in r.actors if a.kind == "player")
        return fail(f"No actor named {actor!r} in {r.pull.pull_key}. Players: {', '.join(players[:40])}.")
    return match.actor_id


def _phase_ids(r: PullRecords) -> list[str]:
    return [p.id for p in r.enc.phases] if r.enc else []


def _check_phase(r: PullRecords, phase: str) -> ToolResult | None:
    known = _phase_ids(r)
    if phase not in known:
        return fail(f"Unknown phase {phase!r} for {r.pull.pull_key}. Phases: {', '.join(known) or 'none defined'}.")
    return None


def phase_at(phases: list[PhaseSpan], t_ms: int) -> str | None:
    """Phase active at ``t_ms`` (the last phase that started at or before it)."""
    current = None
    for span in phases:
        if span.start_ms <= t_ms:
            current = span.phase_id
    return current


# ---------------------------------------------------------------------------
# Tool logic
# ---------------------------------------------------------------------------

_LIST_FIELDS = ("ref", "pull_key", "outcome", "duration_ms", "boss_pct", "phase_reached", "deaths")


def list_pulls_result(scope: AnalystScope) -> ToolResult:
    rows, missing = [], []
    for key in scope.pulls:
        try:
            summary = pull_summary(scope.data.records(key))
        except KeyError:
            missing.append(key)
            continue
        rows.append({k: summary[k] for k in _LIST_FIELDS})
    note = f"Not ingested: {', '.join(missing)}." if missing else None
    return envelope(rows, note=note, hint="Use compare_pulls for one metric across every pull.")


def get_digest_result(scope: AnalystScope, pull: str) -> ToolResult:
    r = _records(scope, pull)
    if isinstance(r, ToolResult):
        return r
    d = r.digest
    refs = [make_ref(d.pull_key, "pull")]
    refs += [x.ref for x in [*d.deaths, *d.mechanics, *d.cooldowns, *d.coverage_windows]]
    # Not subject to CHAR_CAP: the digest's own caps bound it.
    return ToolResult(ok=True, data={"pull_key": d.pull_key, "text": render_digest(d)}, refs=refs)


def query_deaths_result(
    scope: AnalystScope,
    pull: str,
    actor: str | None = None,
    phase: str | None = None,
    include_after_cutoff: bool = False,
) -> ToolResult:
    r = _records(scope, pull)
    if isinstance(r, ToolResult):
        return r
    deaths = r.deaths
    if actor:
        actor_id = _actor_id(r, actor)
        if isinstance(actor_id, ToolResult):
            return actor_id
        deaths = [d for d in deaths if d.actor_id == actor_id]
    if phase:
        if (err := _check_phase(r, phase)) is not None:
            return err
        deaths = [d for d in deaths if d.phase_id == phase]
    note = None
    if not include_after_cutoff:
        hidden = sum(1 for d in deaths if d.after_cutoff)
        deaths = [d for d in deaths if not d.after_cutoff]
        if hidden:
            note = f"{hidden} death(s) after the wipe cutoff hidden; pass include_after_cutoff=true to see them."
    rows = [d.model_dump(mode="json") for d in deaths]
    return envelope(rows, note=note, hint="Filter by actor or phase to see the rest.")


def query_mechanic_result(scope: AnalystScope, pull: str, mechanic_id: str) -> ToolResult:
    r = _records(scope, pull)
    if isinstance(r, ToolResult):
        return r
    stat = next((m for m in r.mechanics if m.mechanic_id == mechanic_id), None)
    if stat is None:
        known = [m.mechanic_id for m in r.mechanics]
        return fail(f"Unknown mechanic {mechanic_id!r} for {r.pull.pull_key}. Mechanics: {', '.join(known) or 'none'}.")
    detail = mechanic_detail(r, stat)
    clusters = [{"ref": stat.ref, **c} for c in detail.pop("cluster_rows")]
    return envelope(
        clusters,
        wrap=lambda kept: {"mechanic": detail, "clusters": kept},
        head_refs=[stat.ref],
        hint="Use query_events with a time window to inspect later clusters.",
    )


def query_events_result(
    scope: AnalystScope,
    pull: str,
    t_from_ms: int,
    t_to_ms: int,
    actor: str | None = None,
    ability_id: int | None = None,
    types: list[str] | None = None,
) -> ToolResult:
    r = _records(scope, pull)
    if isinstance(r, ToolResult):
        return r
    if t_to_ms < t_from_ms:
        return fail("t_to_ms must not be before t_from_ms.")
    if t_to_ms - t_from_ms > MAX_EVENT_WINDOW_MS:
        return fail(
            f"Window is {t_to_ms - t_from_ms:,} ms; the maximum is {MAX_EVENT_WINDOW_MS:,} ms. Query a shorter window."
        )
    frame = r.annotated.frame.filter(pl.col("t_ms").is_between(t_from_ms, t_to_ms))
    if actor:
        actor_id = _actor_id(r, actor)
        if isinstance(actor_id, ToolResult):
            return actor_id
        frame = frame.filter((pl.col("source_id") == actor_id) | (pl.col("target_id") == actor_id))
    if ability_id is not None:
        frame = frame.filter(pl.col("ability_id") == ability_id)
    note = None
    if types:
        present = sorted(set(frame["type"].cast(pl.Utf8).unique().to_list()))
        frame = frame.filter(pl.col("type").cast(pl.Utf8).is_in(types))
        if frame.is_empty():
            note = f"No events of those types in the window. Types present: {', '.join(present) or 'none'}."
    # Build rows only for what the envelope can keep: a 30 s window can hold 20k+ events.
    rows = [event_row(r, row) for row in frame.sort("seq").head(ROW_CAP).iter_rows(named=True)]
    return envelope(
        rows, total=frame.height, note=note, hint="Narrow t_from_ms/t_to_ms, or filter by actor, ability_id or types."
    )


def cooldown_timeline_result(scope: AnalystScope, pull: str, phase: str | None = None) -> ToolResult:
    r = _records(scope, pull)
    if isinstance(r, ToolResult):
        return r
    uses = r.cooldowns
    windows = r.windows
    if phase:
        if (err := _check_phase(r, phase)) is not None:
            return err
        uses = [c for c in uses if c.phase_id == phase]
        windows = [w for w in windows if phase_at(r.annotated.phases, w.t_ms) == phase]
    rows = sorted(
        [c.model_dump(mode="json") for c in uses] + [w.model_dump(mode="json") for w in windows],
        key=lambda row: (row["t_ms"], row["ref"]),
    )
    return envelope(rows, hint="Filter by phase to see the rest.")


def compare_pulls_result(scope: AnalystScope, metric: CompareMetric, mechanic_id: str | None = None) -> ToolResult:
    if metric == "mechanic_failures" and not mechanic_id:
        return fail("metric 'mechanic_failures' needs mechanic_id.")
    rows, missing = [], []
    for key in scope.pulls:
        try:
            r = scope.data.records(key)
        except KeyError:
            missing.append(key)
            continue
        s = pull_summary(r)
        base = {"ref": s["ref"], "pull_key": key, "outcome": s["outcome"]}
        if metric == "deaths":
            rows.append({**base, "deaths": s["deaths"], "deaths_before_cutoff": s["deaths_before_cutoff"]})
        elif metric == "phase_reached":
            rows.append({**base, "phase_reached": s["phase_reached"], "boss_pct": s["boss_pct"]})
        elif metric == "duration":
            rows.append({**base, "duration_ms": s["duration_ms"]})
        else:
            stat = next((m for m in r.mechanics if m.mechanic_id == mechanic_id), None)
            if stat is None:
                rows.append({**base, "mechanic_id": mechanic_id, "failures": None})
            else:
                rows.append(
                    {
                        "ref": stat.ref,
                        "pull_key": key,
                        "mechanic_id": stat.mechanic_id,
                        "failures": stat.failures,
                        "hits": stat.hits,
                        "damage_taken": stat.damage_taken,
                    }
                )
    note = f"Not ingested: {', '.join(missing)}." if missing else None
    return envelope(rows, note=note, hint="The request covers more pulls than fit; compare fewer at a time.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def list_pulls(*, config: RunnableConfig) -> str:
    """List the pulls in this request: outcome, duration, boss health, phase reached and death count.

    Use it first when the request covers several pulls, to pick which ones to look at.
    """
    return list_pulls_result(scope_from(config)).model_dump_json()


@tool(parse_docstring=True)
def get_digest(pull: str, *, config: RunnableConfig) -> str:
    """Get the rendered digest of one pull: deaths, mechanics, cooldowns, damage windows and anomalies.

    Every record line starts with its evidence ref in brackets. Use it to orient on a pull before
    querying details. The digest is capped (10 deaths, 8 mechanics, 30 cooldowns); use query_deaths
    or cooldown_timeline for the full lists.

    Args:
        pull: Pull key in scope, for example "abc123:7".
    """
    return get_digest_result(scope_from(config), pull).model_dump_json()


@tool(parse_docstring=True)
def query_deaths(
    pull: str,
    actor: str | None = None,
    phase: str | None = None,
    include_after_cutoff: bool = False,
    *,
    config: RunnableConfig,
) -> str:
    """Get player death records for one pull, in time order.

    Each record has the killing ability, overkill, time to die from 90% health, damage and healing in
    the 10 s before death, the top hits, the mechanics involved and the defensives active. Deaths after
    the wipe cutoff (when half the raid was already dead) are hidden unless asked for, because they
    rarely explain a wipe.

    Args:
        pull: Pull key in scope, for example "abc123:7".
        actor: Only this player's deaths, by name, for example "Healbot".
        phase: Only deaths in this phase ID, for example "p2".
        include_after_cutoff: Also return deaths after the wipe cutoff.
    """
    return query_deaths_result(scope_from(config), pull, actor, phase, include_after_cutoff).model_dump_json()


@tool(parse_docstring=True)
def query_mechanic(pull: str, mechanic_id: str, *, config: RunnableConfig) -> str:
    """Get one mechanic's statistics for a pull plus one row per cluster of hits.

    The statistics are failures, hits, clusters, damage taken, the players who failed most and the
    deaths it contributed to. Each cluster row has its first and last hit time, players hit, damage
    and failures. Mechanic IDs are listed in the digest.

    Args:
        pull: Pull key in scope, for example "abc123:7".
        mechanic_id: Mechanic ID from the encounter file, for example "swirl".
    """
    return query_mechanic_result(scope_from(config), pull, mechanic_id).model_dump_json()


@tool(parse_docstring=True)
def query_events(
    pull: str,
    t_from_ms: int,
    t_to_ms: int,
    actor: str | None = None,
    ability_id: int | None = None,
    types: list[str] | None = None,
    *,
    config: RunnableConfig,
) -> str:
    """Get raw combat events in a time window of one pull (at most 30,000 ms).

    Use it to inspect what happened around a death or a mechanic: who hit whom with what, health after
    each hit, casts, interrupts and auras. Results are capped; narrow the window or add filters when
    the result says it was truncated.

    Args:
        pull: Pull key in scope, for example "abc123:7".
        t_from_ms: Window start in fight-relative milliseconds, for example 12000.
        t_to_ms: Window end in fight-relative milliseconds (inclusive), for example 20000.
        actor: Only events where this player is the source or target, by name.
        ability_id: Only events of this ability ID.
        types: Only these event types, for example ["damage", "heal", "cast", "death", "interrupt", "applybuff", "removebuff", "applydebuff", "removedebuff"].
    """  # noqa: E501
    return query_events_result(scope_from(config), pull, t_from_ms, t_to_ms, actor, ability_id, types).model_dump_json()


@tool(parse_docstring=True)
def cooldown_timeline(pull: str, phase: str | None = None, *, config: RunnableConfig) -> str:
    """Get raid cooldown casts and damage-window coverage for one pull, in time order.

    Cooldown rows say who cast which registry cooldown when. Damage-window rows say, for each
    occurrence of a raid-wide damage event, which raid cooldowns were active and whether that met the
    expected number.

    Args:
        pull: Pull key in scope, for example "abc123:7".
        phase: Only this phase ID, for example "p2".
    """
    return cooldown_timeline_result(scope_from(config), pull, phase).model_dump_json()


@tool(parse_docstring=True)
def compare_pulls(metric: CompareMetric, mechanic_id: str | None = None, *, config: RunnableConfig) -> str:
    """Compare one metric across every pull in this request, one row per pull.

    Use it for questions about patterns across pulls instead of querying each pull separately.

    Args:
        metric: "deaths" (total and before the wipe cutoff), "mechanic_failures" (needs mechanic_id), "phase_reached" (with boss health) or "duration".
        mechanic_id: Mechanic ID for metric "mechanic_failures", for example "swirl".
    """  # noqa: E501
    return compare_pulls_result(scope_from(config), metric, mechanic_id).model_dump_json()


class SubmittedFinding(BaseModel):
    claim: str = Field(
        description="One factual sentence. Copy every number exactly from a tool result; do not compute new ones."
    )
    refs: list[str] = Field(
        min_length=1,
        description='Evidence refs supporting the claim, copied from tool results, e.g. "ev:abc123:7:death:2".',
    )
    confidence: Confidence = Field(description="How strongly the cited records support the claim.")


class SubmitReportArgs(BaseModel):
    findings: list[SubmittedFinding] = Field(default_factory=list, description="Findings that answer the question.")
    unknowns: list[str] = Field(
        default_factory=list,
        description="Parts of the question the data could not answer, each with the reason.",
    )


@tool("submit_report", args_schema=SubmitReportArgs)
def submit_report(findings: list[SubmittedFinding] | None = None, unknowns: list[str] | None = None) -> str:
    """Submit your final answer and end the investigation. Call it exactly once, when you are done.

    Every finding needs at least one evidence ref from a tool result, and every number in a claim must
    appear in the cited records. If the data cannot answer the question, submit no findings and say why
    in unknowns.
    """
    return "Report received."


ANALYST_TOOLS: tuple[BaseTool, ...] = (
    list_pulls,
    get_digest,
    query_deaths,
    query_mechanic,
    query_events,
    cooldown_timeline,
    compare_pulls,
    submit_report,
)


def build_analyst_tools() -> list[BaseTool]:
    """Every tool the analyst binds, ``submit_report`` last."""
    return list(ANALYST_TOOLS)
