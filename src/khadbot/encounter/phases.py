"""
encounter/phases.py

Phase detection: turn an encounter file's ordered phase triggers into fight-relative spans.

Phases are walked in order and each trigger is searched only after the previous phase's start.
The first trigger that does not match ends detection; ``unmatched_phase`` names it so the digest
can report ``phase_trigger_unmatched:<phase_id>``.

``boss_hp_below`` needs damage events whose target is the boss. The default fetch specs only pull
damage *taken* by friendlies, so this trigger matches nothing until a DamageDone spec is added.
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

from khadbot.encounter.config_files import EncounterFile, Phase, PhaseTrigger
from khadbot.encounter.models import Actor, PhaseSpan, Pull

_AURA_APPLIED = ["applybuff", "applydebuff"]


def _first_t(events: pl.DataFrame, after_ms: int, condition: pl.Expr) -> int | None:
    hit = events.filter((pl.col("t_ms") > after_ms) & condition).select(pl.col("t_ms").min()).item()
    return int(hit) if hit is not None else None


def _trigger_start(
    trigger: PhaseTrigger, after_ms: int, pull: Pull, events: pl.DataFrame, boss_ids: set[int]
) -> int | None:
    """Fight-relative start of a phase whose trigger fires after ``after_ms``; None if it never fires."""
    bosses = list(boss_ids)
    match trigger.trigger:
        case "pull":
            return 0
        case "boss_cast":
            return _first_t(
                events,
                after_ms,
                (pl.col("type").cast(pl.Utf8) == "cast")
                & (pl.col("ability_id") == trigger.ability_id)
                & pl.col("source_id").is_in(bosses),
            )
        case "boss_aura_applied":
            return _first_t(
                events,
                after_ms,
                pl.col("type").cast(pl.Utf8).is_in(_AURA_APPLIED)
                & (pl.col("ability_id") == trigger.ability_id)
                & (pl.col("source_id").is_in(bosses) | pl.col("target_id").is_in(bosses)),
            )
        case "boss_hp_below":
            return _first_t(
                events,
                after_ms,
                (pl.col("type").cast(pl.Utf8) == "damage")
                & pl.col("target_id").is_in(bosses)
                & (pl.col("hp_pct") < trigger.pct),
            )
        case "time":
            t = trigger.t_ms
            return t if t is not None and after_ms < t < pull.duration_ms else None
        case "wcl_phase":
            return next((t for phase, t in pull.phase_transitions if phase == trigger.phase and t > after_ms), None)
    return None  # pragma: no cover - TriggerName is exhaustive


def detect_phases(pull: Pull, events: pl.DataFrame, enc: EncounterFile, boss_ids: set[int]) -> list[PhaseSpan]:
    """Spans for every phase reached, in order. Detection stops at the first unmatched trigger."""
    starts: list[tuple[Phase, int]] = []
    after = -1
    for phase in enc.phases:
        start = _trigger_start(phase.start, after, pull, events, boss_ids)
        if start is None:
            break
        starts.append((phase, start))
        after = start
    ends = [s for _, s in starts[1:]] + [pull.duration_ms]
    return [PhaseSpan(phase_id=p.id, start_ms=s, end_ms=e) for (p, s), e in zip(starts, ends, strict=True)]


def unmatched_phase(spans: list[PhaseSpan], enc: EncounterFile) -> str | None:
    """The phase whose trigger stopped detection, or None if every phase was reached."""
    return enc.phases[len(spans)].id if len(spans) < len(enc.phases) else None


def boss_ids_of(actors: Iterable[Actor]) -> set[int]:
    return {a.actor_id for a in actors if a.kind == "boss"}
