"""
encounter/annotate/pipeline.py

``annotate``: tag a pull's normalized events with what the encounter file says they mean (the columns
are listed in frame.py).

Clusters are consecutive hits of one mechanic (or window) whose gap is at most its ``cluster_window_ms``.
``per_cluster`` failures also get their blame context from the pull's position table. The wipe cutoff is the time cumulative player deaths reach ``ceil(cutoff_fraction * raid_size)``; kills
have no cutoff.
"""

from __future__ import annotations

import math

import polars as pl

from khadbot.encounter.annotate.failures import BlameContext, blame_context, mechanic_failures
from khadbot.encounter.annotate.frame import ANNOTATION_COLUMNS, Annotated, aggro_events, is_hit, type_is
from khadbot.encounter.annotate.phases import boss_ids_of, detect_phases, unmatched_phase
from khadbot.encounter.config_files import EncounterFile, encfile_hash
from khadbot.encounter.models import Actor, PhaseSpan, Pull
from khadbot.encounter.store import empty_positions

# ---------------------------------------------------------------------------
# Column builders
# ---------------------------------------------------------------------------


def _phase_expr(phases: list[PhaseSpan]) -> pl.Expr:
    expr: pl.Expr = pl.lit(None, dtype=pl.Utf8)
    for span in phases:  # later phases override earlier ones
        expr = pl.when(pl.col("t_ms") >= span.start_ms).then(pl.lit(span.phase_id)).otherwise(expr)
    return expr.alias("phase_id")


def _ability_map(groups: list[tuple[str, list[int]]], column: str) -> pl.Expr:
    mapping = {ability_id: gid for gid, ids in groups for ability_id in ids}
    if not mapping:
        return pl.lit(None, dtype=pl.Utf8).alias(column)
    return pl.col("ability_id").replace_strict(mapping, default=None, return_dtype=pl.Utf8).alias(column)


def _clusters(frame: pl.DataFrame, group_col: str, windows: dict[str, int], out_col: str) -> pl.DataFrame:
    """``seq`` -> 1-based gap cluster per group, over hits that have ``group_col`` set."""
    hits = frame.filter(is_hit() & pl.col(group_col).is_not_null()).select("seq", "t_ms", group_col)
    if hits.is_empty():
        return pl.DataFrame(schema={"seq": pl.UInt32(), out_col: pl.Int32()})
    gap = pl.col(group_col).replace_strict(windows, return_dtype=pl.Int32)
    new_cluster = (pl.col("t_ms").diff().over(group_col) > gap).fill_null(True)
    return hits.sort("seq").select(
        "seq", new_cluster.cast(pl.Int32).cum_sum().over(group_col).cast(pl.Int32).alias(out_col)
    )


def wipe_cutoff(pull: Pull, frame: pl.DataFrame, cutoff_fraction: float) -> int | None:
    if pull.kill or not pull.friendly_player_ids:
        return None
    needed = math.ceil(cutoff_fraction * len(pull.friendly_player_ids))
    deaths = (
        frame.filter(type_is("death") & pl.col("target_id").is_in(pull.friendly_player_ids))
        .sort("seq")["t_ms"]
        .to_list()
    )
    return int(deaths[needed - 1]) if needed >= 1 and len(deaths) >= needed else None


# ---------------------------------------------------------------------------
# annotate
# ---------------------------------------------------------------------------


def annotate(
    pull: Pull,
    events: pl.DataFrame,
    enc: EncounterFile | None,
    actors: list[Actor],
    *,
    positions: pl.DataFrame | None = None,
    cutoff_fraction: float = 0.5,
) -> Annotated:
    """
    Tag ``events`` with phases, mechanics, clusters, failures and damage windows. ``positions`` is the pull's
    position table (``store.POSITION_SCHEMA``); without one, every living blamed player is ``no_position``.
    """
    frame = events.sort("seq")
    phases = detect_phases(pull, frame, enc, boss_ids_of(actors)) if enc else []
    mechanics = enc.mechanics if enc else []
    windows = enc.damage_windows if enc else []

    frame = frame.with_columns(
        _phase_expr(phases),
        _ability_map([(m.id, m.ability_ids) for m in mechanics], "mechanic_id"),
        _ability_map([(w.id, w.ability_ids) for w in windows], "window_id"),
    )
    mech_clusters = _clusters(frame, "mechanic_id", {m.id: m.cluster_window_ms for m in mechanics}, "cluster_id")
    win_clusters = _clusters(frame, "window_id", {w.id: w.cluster_window_ms for w in windows}, "window_occurrence")
    frame = (
        frame.join(mech_clusters, on="seq", how="left")
        .join(win_clusters, on="seq", how="left")
        .sort("seq")
        .with_columns(
            # window_id stays only on window hits; casts of the ability are not occurrences.
            pl.when(pl.col("window_occurrence").is_not_null()).then(pl.col("window_id")).alias("window_id")
        )
    )

    game_ids = {a.actor_id: a.game_id for a in actors}
    failures: dict[int, int | None] = {}
    contexts: dict[int, BlameContext] = {}
    aggro = aggro_events(frame)
    table = empty_positions() if positions is None else positions
    for mech in mechanics:
        failures |= mechanic_failures(frame, mech, pull.duration_ms, game_ids, aggro)
        contexts |= blame_context(frame, mech, game_ids, aggro, table)
    blamed = pl.DataFrame(
        {"seq": list(failures), "blamed_id": list(failures.values())},
        schema={"seq": frame.schema["seq"], "blamed_id": pl.Int32()},
    )
    context = pl.DataFrame(
        {
            "seq": list(contexts),
            "blame_state": [c.state for c in contexts.values()],
            "blame_dist_yd": [c.dist_yd for c in contexts.values()],
            "blame_pos_age_ms": [c.pos_age_ms for c in contexts.values()],
        },
        schema={
            "seq": frame.schema["seq"],
            "blame_state": pl.Utf8(),
            "blame_dist_yd": pl.Float32(),
            "blame_pos_age_ms": pl.Int32(),
        },
    )
    frame = (
        frame.join(blamed, on="seq", how="left")
        .join(context, on="seq", how="left")
        .sort("seq")
        .with_columns(pl.col("seq").is_in(list(failures)).alias("is_failure"))
    )
    frame = frame.select(*events.columns, *[pl.col(c).cast(t) for c, t in ANNOTATION_COLUMNS.items()])

    return Annotated(
        pull=pull,
        frame=frame,
        phases=phases,
        cutoff_ms=wipe_cutoff(pull, frame, cutoff_fraction),
        encfile_hash=encfile_hash(enc),
        unmatched_phase=unmatched_phase(phases, enc) if enc else None,
    )
