"""
encounter/analysis/positions.py

Lookups over a pull's position table (``store.POSITION_SCHEMA``): where an actor was at a moment, and how far
apart two samples are.

Positions are in WCL's map units, 1/100 yard (players run 700 to 750 units/s at a base speed of 7 yards/s).
The table stores raw units; ``distance_yd`` converts for what people and the analyst read. A unit is only
sampled on rows where it is the resource actor, so every lookup returns the sample's age and the caller
decides how old is too old.
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
    cond = pl.col("actor_id").is_in(list(actor_ids)) & (pl.col("t_ms") <= t_ms)
    if instance is not None:
        cond &= pl.col("instance") == instance
    if max_age_ms is not None:
        cond &= pl.col("t_ms") >= t_ms - max_age_ms
    rows = positions.filter(cond)
    if rows.is_empty():
        return None
    # Latest first; same-millisecond samples break ties by the table's own order (actor, instance, x, y).
    r = rows.sort("t_ms", maintain_order=True).row(-1, named=True)
    return Sample(
        t_ms=r["t_ms"],
        actor_id=r["actor_id"],
        instance=r["instance"],
        x=r["x"],
        y=r["y"],
        map_id=r["map_id"],
        age_ms=t_ms - r["t_ms"],
    )


def distance_yd(a: Sample, b: Sample) -> float | None:
    """Straight-line distance in yards; None if the samples are on different maps."""
    if a.map_id != b.map_id:
        return None
    return math.hypot(a.x - b.x, a.y - b.y) / UNITS_PER_YARD


__all__ = ["UNITS_PER_YARD", "Sample", "distance_yd", "position_at"]
