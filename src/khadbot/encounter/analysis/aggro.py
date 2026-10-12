"""
encounter/analysis/aggro.py

Aggro from the WCL Threat data. WCL Threat events carry no threat values: an NPC's melee swing names
the player it is attacking, and a taunt shows as the taunt debuff landing on the NPC.

``aggro_bands`` turns those into spans of "this NPC instance was on this player": a band starts at a
swing or taunt on a new player and ends at the next switch, the NPC's death, ``AGGRO_QUIET_MS`` without
a swing or taunt, or the pull end. Merged per NPC actor and player, this rule reproduces WCL's own
threat table on the reference pull (see docs/wcl-api.md). ``aggro_summaries`` is the digest view.
"""

from __future__ import annotations

from collections import Counter

import polars as pl

from khadbot.encounter.annotate import Annotated, aggro_events, type_is
from khadbot.encounter.models import Actor, AggroBand, AggroSummary, AggroTarget

AGGRO_TARGET_CAP = 3

# A band with no swing or taunt for this long ends at its last event (the NPC stopped attacking).
# 20 s matches WCL's bands best on the reference pull: shorter splits real bands, longer joins
# separate ones across a phase where the boss is away.
AGGRO_QUIET_MS = 20_000


def _by_count(counts: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _npc_bands(
    npc: int, inst: int | None, events: list[tuple[int, str, int | None]], quiet_ms: int, duration_ms: int
) -> list[AggroBand]:
    """Bands for one NPC instance from its (t_ms, kind, player) events in order."""
    spans: list[tuple[int, int, int, str]] = []  # (player, start, end, how)
    holder: int | None = None
    start = last = 0
    how = "melee"
    for t, kind, player in events:
        if holder is not None and t - last > quiet_ms:  # went quiet: ends at its last swing or taunt
            spans.append((holder, start, last, how))
            holder = None
        if kind == "death":
            if holder is not None:
                spans.append((holder, start, t, how))
            holder = None
            continue
        if holder is not None and player != holder:
            spans.append((holder, start, t, how))
            holder = None
        if holder is None:
            holder, start, how = player, t, kind
        last = t
    if holder is not None:
        spans.append((holder, start, duration_ms if duration_ms - last <= quiet_ms else last, how))
    return [
        AggroBand(npc_id=npc, npc_instance=inst, player_id=p, start_ms=s, end_ms=e, start=h)  # type: ignore[arg-type]
        for p, s, e, h in spans
    ]


def aggro_bands(a: Annotated, quiet_ms: int = AGGRO_QUIET_MS) -> list[AggroBand]:
    """Every aggro band in the pull, per NPC instance, in start order."""
    by_npc: dict[tuple[int, int | None], list[tuple[int, str, int | None]]] = {}
    for _, t, npc, inst, player, kind in aggro_events(a.frame).iter_rows():
        by_npc.setdefault((npc, inst), []).append((t, kind, player))
    out = [b for (npc, inst), ev in by_npc.items() for b in _npc_bands(npc, inst, ev, quiet_ms, a.pull.duration_ms)]
    return sorted(out, key=lambda b: (b.start_ms, b.npc_id, b.npc_instance or 0))


def aggro_holder(bands: list[AggroBand], npc_ids: set[int], t_ms: int, instance: int | None = None) -> int | None:
    """The player one of ``npc_ids`` (optionally one instance) was attacking at ``t_ms``; None if none was."""
    held = [
        b
        for b in bands
        if b.npc_id in npc_ids and (instance is None or b.npc_instance == instance) and b.start_ms <= t_ms < b.end_ms
    ]
    return max(held, key=lambda b: b.start_ms).player_id if held else None


def aggro_summaries(a: Annotated, actors: list[Actor]) -> list[AggroSummary]:
    """One summary per boss NPC that swung at a friendly target, in actor order; [] without Threat data."""
    threat = a.frame.filter(pl.col("data_type").cast(pl.Utf8) == "Threat")
    if threat.is_empty():
        return []
    names = {x.actor_id: x.name for x in actors}
    swings = threat.filter(type_is("cast") & ~pl.col("source_friendly") & pl.col("target_friendly"))
    taunts = threat.filter(type_is("applydebuff") & pl.col("source_friendly") & ~pl.col("target_friendly"))
    out = []
    for boss in sorted((x for x in actors if x.kind == "boss"), key=lambda x: x.actor_id):
        targets = swings.filter(pl.col("source_id") == boss.actor_id)["target_id"].to_list()
        if not targets:
            continue
        counts = Counter(names.get(t, f"actor-{t}") for t in targets)
        tauntees = taunts.filter(pl.col("target_id") == boss.actor_id)["source_id"].to_list()
        out.append(
            AggroSummary(
                npc=boss.name,
                swings=len(targets),
                targets=[
                    AggroTarget(name=name, swings=n, share_pct=round(n / len(targets) * 100, 1))
                    for name, n in _by_count(counts)[:AGGRO_TARGET_CAP]
                ],
                taunts=dict(_by_count(Counter(names.get(s, f"actor-{s}") for s in tauntees))),
            )
        )
    return out
