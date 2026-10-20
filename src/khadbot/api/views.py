"""
api/views.py

Pure builders behind the read endpoints: report groups and pull summaries, the pull timeline, capped event rows and
cached findings with their staleness. They read through ``EncounterData`` (records, cached in its LRU) and the
``FindingsStore``, and never call WCL or an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

import orjson
import polars as pl

from khadbot.api.schemas import (
    CachedFindings,
    CooldownMarker,
    DeathMarker,
    DifficultyName,
    EventsPage,
    GroupInfo,
    MechanicMarker,
    PhaseMarker,
    PullSummary,
    PullTimeline,
    ReportGroup,
    ReportInfo,
    WindowMarker,
)
from khadbot.encounter.analysis.mechanics import mechanic_clusters
from khadbot.encounter.config_files import WCL_DIFFICULTY, encfile_hash, load_encounter_view
from khadbot.encounter.digest import PullRecords
from khadbot.encounter.evidence import EncounterData, event_row, pull_summary
from khadbot.encounter.findings import FindingsStore
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.models import Pull, PullDigest, make_ref
from khadbot.storage import InvalidKeyError

PULL_CAP = 500  # pull summaries per group
EVENT_ROW_CAP = 200
MAX_EVENT_WINDOW_MS = 30_000  # same as the analyst's query_events


class ApiError(Exception):
    """An error with a stable code; the app turns it into ``{code, message}`` with ``status``."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class ApiContext:
    """What the endpoints read from. ``lock`` serializes record building: ``EncounterData`` is not thread-safe."""

    data: EncounterData
    findings: FindingsStore
    data_dir: Path | None = None
    raw: RawPageCache | None = None
    lock: RLock = field(default_factory=RLock)

    def title(self, code: str) -> str | None:
        """The report title from the cached report metadata (``raw/<code>/report.json``), if present."""
        if self.raw is None:
            return None
        try:
            title = (self.raw.report_meta(code) or {}).get("title")
        except (orjson.JSONDecodeError, AttributeError, InvalidKeyError):
            return None
        return title if isinstance(title, str) else None

    def pulls(self, code: str) -> list[Pull]:
        """The report's stored pulls in time order. Raises ``report_not_found``."""
        try:
            pulls = self.data.store.pulls(code)
        except InvalidKeyError:  # a code no store key can hold, such as "..", was never ingested
            pulls = []
        if not pulls:
            raise ApiError(404, "report_not_found", f"Report {code} has not been ingested.")
        return sorted(pulls, key=lambda p: (p.start_ms, p.fight_id))

    def records(self, code: str, fight: int) -> PullRecords:
        """Uncapped records for one pull. Raises ``report_not_found`` or ``pull_not_found``."""
        pulls = self.pulls(code)
        if not any(p.fight_id == fight for p in pulls):
            raise ApiError(404, "pull_not_found", f"Fight {fight} of report {code} has not been ingested.")
        return self.data.records(f"{code}:{fight}")


# ---------------------------------------------------------------------------
# Reports and groups
# ---------------------------------------------------------------------------

GroupKey = tuple[int, int | None]  # (encounter_id, difficulty)


def group_pulls(pulls: list[Pull]) -> list[tuple[GroupKey, list[Pull]]]:
    """Pulls (already in time order) grouped by encounter and difficulty, groups in the order first pulled."""
    groups: dict[GroupKey, list[Pull]] = {}
    for p in pulls:
        groups.setdefault((p.encounter_id, p.difficulty), []).append(p)
    return list(groups.items())


def difficulty_name(difficulty: int | None) -> DifficultyName | None:
    return WCL_DIFFICULTY.get(difficulty) if difficulty is not None else None


def report_info(ctx: ApiContext, code: str) -> ReportInfo:
    pulls = ctx.pulls(code)
    groups = [
        GroupInfo(
            encounter_id=eid,
            encounter_name=members[0].encounter_name,
            difficulty=diff,
            difficulty_name=difficulty_name(diff),
            pulls=len(members),
        )
        for (eid, diff), members in group_pulls(pulls)
    ]
    return ReportInfo(code=code, title=ctx.title(code), pulls=len(pulls), groups=groups)


def summarize_pull(r: PullRecords, ordinal: int) -> PullSummary:
    """A pull's row in its group: the fields of what its ``pull`` ref resolves to, plus fight and ordinal."""
    s = pull_summary(r)
    return PullSummary(
        fight=r.pull.fight_id,
        ordinal=ordinal,
        label=f"P{ordinal}",
        outcome=s["outcome"],
        duration_ms=s["duration_ms"],
        boss_pct=s["boss_pct"],
        boss_hp_pct=s["boss_hp_pct"],
        phase_reached=s["phase_reached"],
        deaths=s["deaths"],
        deaths_before_cutoff=s["deaths_before_cutoff"],
        coverage=s["coverage"],
        anomalies=s["anomalies"],
        ref=s["ref"],
    )


def report_groups(ctx: ApiContext, code: str) -> list[ReportGroup]:
    out = []
    for (eid, diff), members in group_pulls(ctx.pulls(code)):
        rows = [summarize_pull(ctx.data.records(p.pull_key), i) for i, p in enumerate(members[:PULL_CAP], start=1)]
        out.append(
            ReportGroup(
                encounter_id=eid,
                encounter_name=members[0].encounter_name,
                difficulty=diff,
                difficulty_name=difficulty_name(diff),
                pulls=rows,
                truncated=len(members) > PULL_CAP,
                total=len(members),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Pull views
# ---------------------------------------------------------------------------


def full_digest(r: PullRecords) -> PullDigest:
    """
    The digest with its capped lists replaced by every record (``?full=true``): every death, mechanic, cooldown cast
    of each category, and window, in time order (deaths and mechanics in the digest's own order).
    """
    mechanics = sorted(r.mechanics, key=lambda m: (-m.failures, -m.damage_taken))
    return r.digest.model_copy(
        update={
            "deaths": sorted(r.deaths, key=lambda d: (d.after_cutoff, d.t_ms)),
            "mechanics": mechanics,
            "defensive_cooldowns": [c for c in r.cooldowns if c.category == "defensive"],
            "offensive_cooldowns": [c for c in r.cooldowns if c.category == "offensive"],
            "utility_cooldowns": [c for c in r.cooldowns if c.category == "utility"],
            "coverage_windows": list(r.windows),
        }
    )


def timeline(r: PullRecords) -> PullTimeline:
    key = r.pull.pull_key
    enc = r.enc
    phase_names = {p.id: p.name for p in enc.phases} if enc else {}
    mech_names = {m.id: m.name for m in enc.mechanics} if enc else {}
    window_names = {w.id: w.name for w in enc.damage_windows} if enc else {}

    mechanics = []
    for stat in r.mechanics:
        for c in mechanic_clusters(r.annotated, stat.mechanic_id, r.actors):
            mechanics.append(
                MechanicMarker(
                    mechanic_id=stat.mechanic_id,
                    name=mech_names.get(stat.mechanic_id),
                    cluster=c.cluster,
                    t_ms=c.t_ms,
                    end_ms=c.end_ms,
                    hits=c.hits,
                    failures=c.failures,
                    ref=stat.ref,
                )
            )
    mechanics.sort(key=lambda m: (m.t_ms, m.mechanic_id))

    return PullTimeline(
        pull_key=key,
        duration_ms=r.pull.duration_ms,
        cutoff_ms=r.annotated.cutoff_ms,
        ref=make_ref(key, "pull"),
        phases=[
            PhaseMarker(id=s.phase_id, name=phase_names.get(s.phase_id), start_ms=s.start_ms, end_ms=s.end_ms)
            for s in r.annotated.phases
        ],
        deaths=[DeathMarker(t_ms=d.t_ms, name=d.name, after_cutoff=d.after_cutoff, ref=d.ref) for d in r.deaths],
        mechanics=mechanics,
        cooldowns=[
            CooldownMarker(
                t_ms=c.t_ms,
                spell_id=c.spell_id,
                spell=c.spell_name,
                category=c.category,
                caster=c.caster,
                ref=c.ref,
            )
            for c in r.cooldowns
        ],
        windows=[
            WindowMarker(
                t_ms=w.t_ms,
                window_id=w.window_id,
                name=window_names.get(w.window_id),
                occurrence=w.occurrence,
                covered=w.covered,
                ref=w.ref,
            )
            for w in sorted(r.windows, key=lambda w: (w.t_ms, w.window_id))
        ],
    )


def events_page(
    r: PullRecords,
    t_from_ms: int,
    t_to_ms: int,
    *,
    actor: str | None = None,
    ability_id: int | None = None,
    types: list[str] | None = None,
) -> EventsPage:
    """Annotated event rows in ``[t_from_ms, t_to_ms]`` (at most 30 s), filtered, in ``seq`` order, capped at 200."""
    if t_to_ms < t_from_ms:
        raise ApiError(400, "bad_window", "t_to_ms must not be before t_from_ms.")
    if t_to_ms - t_from_ms > MAX_EVENT_WINDOW_MS:
        raise ApiError(
            400,
            "bad_window",
            f"Window is {t_to_ms - t_from_ms:,} ms; the maximum is {MAX_EVENT_WINDOW_MS:,} ms.",
        )
    frame = r.annotated.frame.filter(pl.col("t_ms").is_between(t_from_ms, t_to_ms))
    if actor:
        actor_id = _actor_id(r, actor)
        frame = frame.filter((pl.col("source_id") == actor_id) | (pl.col("target_id") == actor_id))
    if ability_id is not None:
        frame = frame.filter(pl.col("ability_id") == ability_id)
    note = None
    if types:
        present = sorted(set(frame["type"].cast(pl.Utf8).unique().to_list()))
        frame = frame.filter(pl.col("type").cast(pl.Utf8).is_in(types))
        if frame.is_empty():
            note = f"No events of those types in the window. Types present: {', '.join(present) or 'none'}."
    total = frame.height
    rows = [event_row(r, row) for row in frame.sort("seq").head(EVENT_ROW_CAP).iter_rows(named=True)]
    if total > EVENT_ROW_CAP:
        note = f"Showing the first {EVENT_ROW_CAP} of {total:,} rows; narrow the window or filter."
    return EventsPage(
        rows=rows, refs=[row["ref"] for row in rows], truncated=total > EVENT_ROW_CAP, total=total, note=note
    )


def _actor_id(r: PullRecords, actor: str) -> int:
    """An actor by name (case-insensitive) or numeric ID. Raises ``unknown_actor``."""
    wanted = actor.strip()
    if wanted.lstrip("-").isdigit() and any(a.actor_id == int(wanted) for a in r.actors):
        return int(wanted)
    match = next((a for a in r.actors if a.name.casefold() == wanted.casefold()), None)
    if match is None:
        raise ApiError(400, "unknown_actor", f"No actor named {actor!r} in {r.pull.pull_key}.")
    return match.actor_id


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def cached_findings(ctx: ApiContext, pull: Pull) -> list[CachedFindings]:
    """The pull's cached findings, newest first, each marked stale if its events or encounter file changed."""
    if not ctx.findings.db_path.is_file():  # reading must not create the database
        return []
    events_hash = ctx.data.store.events_hash(pull.pull_key) or ""
    current_enc = encfile_hash(load_encounter_view(pull.encounter_id, pull.difficulty, ctx.data.enc_root))
    out = []
    for pf in ctx.findings.for_pull(pull.pull_key):
        produced = pf.produced_by
        stale = produced.events_hashes.get(pull.pull_key) != events_hash or (
            produced.encfile_hash is not None and produced.encfile_hash != current_enc
        )
        out.append(CachedFindings(**pf.model_dump(), stale=stale))
    return out
