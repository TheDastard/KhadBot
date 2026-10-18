"""
encounter/annotate/frame.py

The annotated frame: a pull's normalized events plus the annotation columns.

    phase_id           Utf8   phase active at t_ms (null before the pull or without an encounter file)
    mechanic_id        Utf8   mechanic owning ability_id (any event type)
    cluster_id         Int32  1-based cluster within the mechanic; set on hits only
    is_failure         Bool   row is a failure under the mechanic's rule
    blamed_id          Int32  on failure rows, the player charged with the failure: the player hit for most
                              rules, the player a ``blame`` spec points at for ``per_cluster``; null if no one
    window_id          Utf8   damage window owning ability_id; set on hits only
    window_occurrence  Int32  1-based occurrence of the window; set on hits only
    blame_state        Utf8   on per_cluster failure rows (blame ``aggro_holder`` or ``cast_target``), where the
                              blamed player was at the hit: ``beyond_reach`` / ``in_reach`` (alive, farther than
                              or within the blame's ``reach_yd`` from the NPC), ``dead`` (a Deaths row at or
                              before the hit and nothing they did since), ``alive`` (no ``reach_yd`` set),
                              ``no_position`` (no sample recent enough), ``no_holder`` (``blamed_id`` is null);
                              null on every other row
    blame_dist_yd      Float32  with blame_state, the blamed player's distance from the NPC in yards (0.1);
                              null when dead, without a position or without a holder
    blame_pos_age_ms   Int32  with blame_dist_yd, the age of the player's position sample

A "hit" is a ``damage`` event on a friendly target. ``ANNOTATION_VERSION`` is bumped whenever the columns
or their rules change, so cached frames are rebuilt. ``aggro_events`` reads who each NPC was attacking from
the Threat data; failure blame and the aggro analysis both use it.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from khadbot.encounter.models import PhaseSpan, Pull

ANNOTATION_COLUMNS: dict[str, pl.DataType] = {
    "phase_id": pl.Utf8(),
    "mechanic_id": pl.Utf8(),
    "cluster_id": pl.Int32(),
    "is_failure": pl.Boolean(),
    "blamed_id": pl.Int32(),
    "window_id": pl.Utf8(),
    "window_occurrence": pl.Int32(),
    "blame_state": pl.Utf8(),
    "blame_dist_yd": pl.Float32(),
    "blame_pos_age_ms": pl.Int32(),
}

# Bump when annotation columns or failure rules change, so cached frames are rebuilt.
ANNOTATION_VERSION = 6


@dataclass(frozen=True)
class Annotated:
    pull: Pull
    frame: pl.DataFrame
    phases: list[PhaseSpan]
    cutoff_ms: int | None
    encfile_hash: str
    unmatched_phase: str | None = None

    @property
    def hits(self) -> pl.DataFrame:
        """Damage events on friendly targets."""
        return self.frame.filter(is_hit())


def is_hit() -> pl.Expr:
    return (pl.col("type").cast(pl.Utf8) == "damage") & pl.col("target_friendly")


def type_is(*types: str) -> pl.Expr:
    return pl.col("type").cast(pl.Utf8).is_in(list(types))


def aggro_events(frame: pl.DataFrame) -> pl.DataFrame:
    """
    Threat rows that give an NPC a target or end it, in ``seq`` order: ``seq``, ``t_ms``, ``npc_id``,
    ``npc_instance``, ``player_id`` and ``kind``. ``melee`` is an NPC swing at a player, ``taunt`` a taunt
    debuff landing on the NPC, ``death`` the NPC dying (``player_id`` null).
    """
    threat = frame.filter(pl.col("data_type").cast(pl.Utf8) == "Threat")
    cols = ("seq", "t_ms")
    swings = threat.filter(type_is("cast") & ~pl.col("source_friendly") & pl.col("target_friendly")).select(
        *cols,
        pl.col("source_id").alias("npc_id"),
        pl.col("source_instance").alias("npc_instance"),
        pl.col("target_id").alias("player_id"),
        pl.lit("melee").alias("kind"),
    )
    taunts = threat.filter(type_is("applydebuff") & pl.col("source_friendly") & ~pl.col("target_friendly")).select(
        *cols,
        pl.col("target_id").alias("npc_id"),
        pl.col("target_instance").alias("npc_instance"),
        pl.col("source_id").alias("player_id"),
        pl.lit("taunt").alias("kind"),
    )
    deaths = threat.filter(type_is("death") & ~pl.col("target_friendly")).select(
        *cols,
        pl.col("target_id").alias("npc_id"),
        pl.col("target_instance").alias("npc_instance"),
        pl.lit(None, dtype=pl.Int32).alias("player_id"),
        pl.lit("death").alias("kind"),
    )
    return pl.concat([swings, taunts, deaths]).sort("seq")
