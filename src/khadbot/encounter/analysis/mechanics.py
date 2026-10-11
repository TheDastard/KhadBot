"""
encounter/analysis/mechanics.py

Per-mechanic statistics over an annotated pull: clusters, hits, failures (with the players who
failed), damage taken, and how many deaths the mechanic contributed to; plus a per-cluster breakdown.
"""

from __future__ import annotations

from collections import Counter

import polars as pl

from khadbot.encounter.annotate import Annotated, is_hit
from khadbot.encounter.config_files import EncounterFile
from khadbot.encounter.models import Actor, DeathRecord, MechanicCluster, MechanicStat, make_ref

TOP_FAILING_PLAYERS = 10


def mechanic_stats(
    a: Annotated, enc: EncounterFile | None, actors: list[Actor], deaths: list[DeathRecord]
) -> list[MechanicStat]:
    """One stat per mechanic in the encounter file, in file order (zero rows included)."""
    if enc is None:
        return []
    names = {x.actor_id: x.name for x in actors}
    out = []
    for mech in enc.mechanics:
        rows = a.frame.filter(pl.col("mechanic_id") == mech.id)
        hits = rows.filter(is_hit())
        failed = rows.filter(pl.col("is_failure") & pl.col("target_friendly"))["target_id"].to_list()
        by_player = Counter(names.get(t, f"actor-{t}") for t in failed)
        top = sorted(by_player.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_FAILING_PLAYERS]
        out.append(
            MechanicStat(
                ref=make_ref(a.pull.pull_key, "mech", mech.id),
                mechanic_id=mech.id,
                clusters=hits["cluster_id"].n_unique() if hits.height else 0,
                hits=hits.height,
                failures=int(rows["is_failure"].sum()),
                failures_by_player=dict(top),
                damage_taken=int(hits["amount"].sum() or 0),
                deaths_in_window=sum(1 for d in deaths if mech.id in d.mechanic_ids),
            )
        )
    return out


def mechanic_clusters(a: Annotated, mechanic_id: str, actors: list[Actor]) -> list[MechanicCluster]:
    """
    Per-cluster breakdown of one mechanic's hits, in cluster order.

    Failures that are not hits (``not_interrupted`` casts) belong to no cluster and are not counted here.
    """
    names = {x.actor_id: x.name for x in actors}
    hits = a.frame.filter(is_hit() & (pl.col("mechanic_id") == mechanic_id))
    if hits.is_empty():
        return []
    grouped = (
        hits.group_by("cluster_id")
        .agg(
            pl.col("t_ms").min().alias("t_ms"),
            pl.col("t_ms").max().alias("end_ms"),
            pl.col("phase_id").sort_by("seq").first().alias("phase_id"),
            pl.len().alias("hits"),
            pl.col("target_id").n_unique().alias("players"),
            pl.col("amount").fill_null(0).sum().alias("damage"),
            pl.col("is_failure").sum().alias("failures"),
            pl.col("target_id").filter(pl.col("is_failure")).unique().alias("failed_ids"),
        )
        .sort("cluster_id")
    )
    return [
        MechanicCluster(
            cluster=r["cluster_id"],
            t_ms=r["t_ms"],
            end_ms=r["end_ms"],
            phase_id=r["phase_id"],
            hits=r["hits"],
            players=r["players"],
            damage=int(r["damage"]),
            failures=int(r["failures"]),
            failed_players=sorted(names.get(t, f"actor-{t}") for t in r["failed_ids"]),
        )
        for r in grouped.iter_rows(named=True)
    ]
