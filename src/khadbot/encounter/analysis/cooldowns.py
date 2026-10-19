"""
encounter/analysis/cooldowns.py

Cooldown casts (defensive, offensive and utility) and damage-window coverage (defensive only), plus the aura
intervals coverage (and death records) use.

Aura activity comes from Buffs ``applybuff`` / ``removebuff`` pairs per (target, spell). An aura with
no remove event lasts until the pull ends; a remove with no apply (aura up before the pull) starts at 0.
A cooldown whose aura has its own spell ID (registry ``aura_spell_ids``) gets intervals under its cast ID.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

import polars as pl

from khadbot.encounter.annotate import Annotated, is_hit, type_is
from khadbot.encounter.config_files import CooldownRegistry, EncounterFile
from khadbot.encounter.models import Actor, CooldownUse, LateCooldown, WindowCoverage, make_ref

# A defensive raid cooldown started this long after a window's first hit still counts as late, even when the
# window's hits end sooner (a one-hit window).
LATE_GRACE_MS = 5_000


class AuraInterval(NamedTuple):
    target_id: int
    spell_id: int
    start_ms: int
    end_ms: int

    def active_at(self, t_ms: int) -> bool:
        return self.start_ms <= t_ms <= self.end_ms


def aura_intervals(
    frame: pl.DataFrame, spell_ids: Mapping[int, int] | Iterable[int], end_ms: int
) -> list[AuraInterval]:
    """
    Buff intervals for ``spell_ids`` on any target, in start order.

    ``spell_ids`` is a list of aura IDs, or a mapping from aura ID to the spell ID the interval is reported
    under (``CooldownRegistry.aura_ids``). Applies and removes still pair by the aura ID on the row.
    """
    report_as = dict(spell_ids) if isinstance(spell_ids, Mapping) else {i: i for i in spell_ids}
    if not report_as:
        return []
    rows = (
        frame.filter(type_is("applybuff", "removebuff") & pl.col("ability_id").is_in(list(report_as)))
        .sort("seq")
        .select("t_ms", pl.col("type").cast(pl.Utf8), "target_id", "ability_id")
        .rows()
    )
    open_at: dict[tuple[int, int], int] = {}
    out: list[AuraInterval] = []
    for t, typ, target, spell in rows:
        key = (target, spell)
        if typ == "applybuff":
            open_at.setdefault(key, t)
        else:
            out.append(AuraInterval(target, report_as[spell], open_at.pop(key, 0), t))
    out += [AuraInterval(target, report_as[spell], start, end_ms) for (target, spell), start in open_at.items()]
    return sorted(out, key=lambda i: (i.start_ms, i.target_id, i.spell_id))


def cooldown_uses(a: Annotated, registries: Sequence[CooldownRegistry], actors: list[Actor]) -> list[CooldownUse]:
    """
    Friendly casts of the spells in ``registries`` (one per cooldown category), in time order; refs are numbered
    per spell. Each use names the category of the file its spell is in; the files share no spell ID.
    """
    names = {x.actor_id: x.name for x in actors}
    entries = {c.spell_id: (c, r.category) for r in registries for c in r.cooldowns}
    casts = (
        a.frame.filter(type_is("cast") & pl.col("source_friendly") & pl.col("ability_id").is_in(list(entries)))
        .sort("seq")
        .select("t_ms", "ability_id", "source_id", "phase_id")
        .rows()
    )
    counts: dict[int, int] = {}
    out = []
    for t, spell, source, phase in casts:
        counts[spell] = counts.get(spell, 0) + 1
        entry, category = entries[spell]
        out.append(
            CooldownUse(
                ref=make_ref(a.pull.pull_key, "cd", spell, counts[spell]),
                spell_id=spell,
                spell_name=entry.name,
                category=category,
                caster=names.get(source, f"actor-{source}"),
                t_ms=t,
                phase_id=phase,
            )
        )
    return out


def window_coverage(a: Annotated, enc: EncounterFile | None, defensive: CooldownRegistry) -> list[WindowCoverage]:
    """
    One row per damage-window occurrence: which defensive raid cooldowns were active at its first hit.

    Only ``target: raid`` defensive cooldowns count. A damage window is raid-wide damage, and an external on
    one player does not cover it; offensive cooldowns never do. ``covered`` judges the first hit only against
    the window's ``expected_defensive_cooldowns``. A defensive raid cooldown whose aura
    started after the first hit, up to the occurrence's last hit or ``LATE_GRACE_MS`` after the first
    hit (whichever is later), is listed as late with how late it started: used earlier, it would have
    covered the first hit.
    """
    if enc is None or not enc.damage_windows:
        return []
    intervals = aura_intervals(a.frame, defensive.aura_ids(["raid"]), a.pull.duration_ms)
    spans = (
        a.frame.filter(is_hit() & pl.col("window_id").is_not_null())
        .group_by("window_id", "window_occurrence")
        .agg(pl.col("t_ms").min().alias("first"), pl.col("t_ms").max().alias("last"))
    )
    hits = {(w, n): (first, last) for w, n, first, last in spans.rows()}
    out = []
    for window in enc.damage_windows:
        occurrences = sorted(n for (w, n) in hits if w == window.id)
        for n in occurrences:
            t, last = hits[(window.id, n)]
            active = sorted({i.spell_id for i in intervals if i.active_at(t)})
            late_until = max(last, t + LATE_GRACE_MS)
            late_start: dict[int, int] = {}
            for i in intervals:
                if t < i.start_ms <= late_until and i.spell_id not in active:
                    late_start[i.spell_id] = min(late_start.get(i.spell_id, i.start_ms), i.start_ms)
            out.append(
                WindowCoverage(
                    ref=make_ref(a.pull.pull_key, "win", window.id, n),
                    window_id=window.id,
                    occurrence=n,
                    t_ms=t,
                    defensive_cooldowns_active=active,
                    covered=len(active) >= window.expected_defensive_cooldowns,
                    defensive_cooldowns_late=[
                        LateCooldown(spell_id=spell, late_ms=start - t)
                        for spell, start in sorted(late_start.items(), key=lambda kv: (kv[1], kv[0]))
                    ],
                )
            )
    return out
