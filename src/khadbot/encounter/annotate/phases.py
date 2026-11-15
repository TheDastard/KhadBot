"""
encounter/annotate/phases.py

Phase detection: turn an encounter file's ordered phase triggers into fight-relative spans.

Phases are walked in order and each trigger is searched only after the previous phase's start.
After the last phase of the repeating block (the phases with ``repeat: true``, which end the list) detection
walks the block again from its first phase, so a repeating phase gives one span per time it is reached, all
under its one ID. The first trigger that does not match ends detection; ``unmatched_phase`` names it.
``unmatched_phases`` compares the spans with WCL's own phase transitions, for the digest's
``phase_trigger_unmatched:<phase_id>`` anomaly.

``boss_hp_below`` needs damage events whose target is the boss. The default fetch specs only pull
damage *taken* by friendlies, so this trigger matches nothing until a DamageDone spec is added.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator

import polars as pl

from khadbot.encounter.config_files import EncounterFile, Phase, PhaseTrigger, repeat_block
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


def _phase_order(enc: EncounterFile) -> Iterator[Phase]:
    """The phases in the order detection tries them: the list, then the repeating block over and over."""
    yield from enc.phases
    block = repeat_block(enc)
    while block:
        yield from block


def detect_phases(pull: Pull, events: pl.DataFrame, enc: EncounterFile, boss_ids: set[int]) -> list[PhaseSpan]:
    """
    Spans for every phase reached, in order. Detection stops at the first unmatched trigger. Every trigger fires
    strictly after the previous start, so the repeating block ends once its triggers run out.
    """
    starts: list[tuple[Phase, int]] = []
    after = -1
    for phase in _phase_order(enc):
        start = _trigger_start(phase.start, after, pull, events, boss_ids)
        if start is None:
            break
        starts.append((phase, start))
        after = start
    ends = [s for _, s in starts[1:]] + [pull.duration_ms]
    return [PhaseSpan(phase_id=p.id, start_ms=s, end_ms=e) for (p, s), e in zip(starts, ends, strict=True)]


def unmatched_phase(spans: list[PhaseSpan], enc: EncounterFile) -> str | None:
    """
    The phase whose trigger stopped detection: the next one in detection order. None if every listed phase was
    reached and none repeats.
    """
    return next((p.id for i, p in enumerate(_phase_order(enc)) if i == len(spans)), None)


def unmatched_phases(pull: Pull, spans: list[PhaseSpan], enc: EncounterFile) -> list[str]:
    """
    Encounter-file phases WCL says the pull reached but the triggers did not detect, in file order.

    ``wcl_phase`` phases are judged by how often their WCL phase ID occurs in the pull's transitions after the
    pull start (the opening transition at 0 ms belongs to the first phase, which ``trigger: pull`` starts).
    The n-th listed phase with that ID is reached when the ID occurs at least n times. A repeating phase is
    flagged once when the ID occurs more often than spans were detected for the phases that use it.

    Listed phases with other triggers are judged by count: the pull has more distinct WCL transitions than
    detected phases, and each one flagged accounts for one of those extra transitions. Repeating phases with
    other triggers are not judged.
    """
    detected = {s.phase_id for s in spans}
    occurs = Counter(phase for phase, t in pull.phase_transitions if t > 0)
    wcl_of = {p.id: p.start.phase for p in enc.phases if p.start.trigger == "wcl_phase"}
    spans_of = Counter(wcl_of[s.phase_id] for s in spans if s.phase_id in wcl_of)
    missed = len({phase for phase, _ in pull.phase_transitions}) - len(spans)
    listed_nth: Counter[int] = Counter()
    out: list[str] = []
    for phase in enc.phases:
        wcl = phase.start.phase if phase.start.trigger == "wcl_phase" else None
        if wcl is not None and phase.repeat:
            if occurs[wcl] > spans_of[wcl]:
                out.append(phase.id)
            continue
        if wcl is not None:
            listed_nth[wcl] += 1
        if phase.id in detected or phase.repeat:
            continue
        if wcl is not None:
            if occurs[wcl] >= listed_nth[wcl]:
                out.append(phase.id)
        elif len(out) < missed:
            out.append(phase.id)
    return out


def boss_ids_of(actors: Iterable[Actor]) -> set[int]:
    return {a.actor_id for a in actors if a.kind == "boss"}
