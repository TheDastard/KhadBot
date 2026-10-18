"""
encounter/analysis/mechanics.py

Per-mechanic statistics over an annotated pull: clusters, hits, failures (with the players charged
with them, ``blamed_id``), damage taken, and how many deaths the mechanic contributed to; plus a
per-cluster breakdown. For ``per_cluster`` mechanics both carry the blame context from annotation: failures
per ``blame_state``, and per cluster who was charged, where they were and the NPC's reach.
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
        failed_rows = rows.filter(pl.col("is_failure"))
        failed = failed_rows["blamed_id"].drop_nulls().to_list()
        by_player = Counter(names.get(t, f"actor-{t}") for t in failed)
        states = Counter(failed_rows["blame_state"].drop_nulls().to_list())
        ranges = (
            failed_rows.filter(pl.col("blame_dist_yd").is_not_null())
            .group_by("blame_state")
            .agg(pl.col("blame_dist_yd").min().alias("lo"), pl.col("blame_dist_yd").max().alias("hi"))
            .sort("blame_state")
        )
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
                blame_states=dict(sorted(states.items())),
                blame_dist_yd={s: (round(lo, 1), round(hi, 1)) for s, lo, hi in ranges.rows()},
            )
        )
    return out


def blame_reach_yd(enc: EncounterFile | None, mechanic_id: str) -> float | None:
    """The swing reach the mechanic's blame spec sets, if any."""
    mech = next((m for m in enc.mechanics if m.id == mechanic_id), None) if enc else None
    blame = mech.failure.blame if mech and mech.failure else None
    return blame.reach_yd if blame else None


def mechanic_clusters(
    a: Annotated, mechanic_id: str, actors: list[Actor], *, reach_yd: float | None = None
) -> list[MechanicCluster]:
    """
    Per-cluster breakdown of one mechanic's hits, in cluster order.

    Failures that are not hits (``not_interrupted`` casts, aura and stack rows) belong to no cluster and are not
    counted here. A cluster whose failure has blame context (``per_cluster``) also names the player charged, their
    ``blame_state`` and distance, and ``reach_yd`` (the blame spec's, passed in) when a state was judged.
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
            pl.col("blamed_id").filter(pl.col("is_failure")).drop_nulls().unique().alias("failed_ids"),
            pl.col("blamed_id").filter(pl.col("blame_state").is_not_null()).first().alias("blamed_id"),
            pl.col("blame_state").drop_nulls().first().alias("blame_state"),
            pl.col("blame_dist_yd").drop_nulls().first().alias("blame_dist_yd"),
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
            blamed=names.get(r["blamed_id"], f"actor-{r['blamed_id']}") if r["blamed_id"] is not None else None,
            blame_state=r["blame_state"],
            # Float32 in the frame; round again so the record holds the 0.1-yard value, not its float32 neighbour.
            blame_dist_yd=round(r["blame_dist_yd"], 1) if r["blame_dist_yd"] is not None else None,
            blame_reach_yd=reach_yd if r["blame_state"] is not None else None,
        )
        for r in grouped.iter_rows(named=True)
    ]
