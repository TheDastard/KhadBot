"""
encounter/analysis/positions.py

Lookups over a pull's position table (``store.POSITION_SCHEMA``): where an actor was at a moment, and how far
apart two samples are.

Positions are in WCL's map units, 1/100 yard (players run 700 to 750 units/s at a base speed of 7 yards/s).
The table stores raw units; ``distance_yd`` converts for what people and the analyst read. A unit is only
sampled on rows where it is the resource actor, so every lookup returns the sample's age and the caller
decides how old is too old.

``map_id`` is WCL's ``mapID``: the UI map the unit's client was on. A pull can span several maps (another
floor, a portal to a separate area, part of the raid sent elsewhere), and coordinates on two maps share no
frame, so nothing may measure across them: ``distance_yd`` returns None, and a caller pairing two units takes
each unit's latest sample (``latest_per_unit``) before matching maps, never an older sample that happens to be on
the wanted map. Anything that follows one unit over time must also break its track where ``map_id`` changes.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import polars as pl

UNITS_PER_YARD = 100


@dataclass(frozen=True)
class Sample:
    """One position sample (raw units), taken ``age_ms`` before the moment it was looked up for."""

    t_ms: int
    actor_id: int
    instance: int | None
    x: int
    y: int
    map_id: int | None
    age_ms: int


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def _before(
    positions: pl.DataFrame, actor_ids: Iterable[int], t_ms: int, instance: int | None, max_age_ms: int | None
) -> pl.DataFrame:
    """Samples of ``actor_ids`` at or before ``t_ms`` (and within ``max_age_ms``), oldest first, ties in table order."""
    cond = pl.col("actor_id").is_in(list(actor_ids)) & (pl.col("t_ms") <= t_ms)
    if instance is not None:
        cond &= pl.col("instance") == instance
    if max_age_ms is not None:
        cond &= pl.col("t_ms") >= t_ms - max_age_ms
    return positions.filter(cond).sort("t_ms", maintain_order=True)


def _sample(r: dict, t_ms: int) -> Sample:
    return Sample(
        t_ms=r["t_ms"],
        actor_id=r["actor_id"],
        instance=r["instance"],
        x=r["x"],
        y=r["y"],
        map_id=r["map_id"],
        age_ms=t_ms - r["t_ms"],
    )


def position_at(
    positions: pl.DataFrame,
    actor_ids: Iterable[int],
    t_ms: int,
    *,
    instance: int | None = None,
    max_age_ms: int | None,
) -> Sample | None:
    """
    The latest sample at or before ``t_ms`` for any of ``actor_ids`` (only ``instance`` when given), with its
    age. None when there is none, or when it is older than ``max_age_ms`` (None: no age limit).
    """
    rows = _before(positions, actor_ids, t_ms, instance, max_age_ms)
    if rows.is_empty():
        return None
    # Latest last; same-millisecond samples break ties by the table's own order (actor, instance, x, y).
    return _sample(rows.row(-1, named=True), t_ms)


def latest_per_unit(
    positions: pl.DataFrame, actor_ids: Iterable[int], t_ms: int, *, max_age_ms: int | None
) -> list[Sample]:
    """
    Each unit's (actor and instance) latest sample at or before ``t_ms``, at most ``max_age_ms`` old (None: no
    limit), oldest first. A unit that has since moved to another map shows up on its new map only.
    """
    rows = _before(positions, actor_ids, t_ms, None, max_age_ms)
    latest = rows.unique(subset=["actor_id", "instance"], keep="last", maintain_order=True).sort(
        "t_ms", maintain_order=True
    )
    return [_sample(r, t_ms) for r in latest.iter_rows(named=True)]


def distance_yd(a: Sample, b: Sample) -> float | None:
    """Straight-line distance in yards; None if the samples are on different maps."""
    if a.map_id != b.map_id:
        return None
    return math.hypot(a.x - b.x, a.y - b.y) / UNITS_PER_YARD


__all__ = ["UNITS_PER_YARD", "Sample", "distance_yd", "latest_per_unit", "position_at"]
