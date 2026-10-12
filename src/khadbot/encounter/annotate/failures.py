"""
encounter/annotate/failures.py

Failure rules: which rows of a mechanic are failures, and who is charged with each.

Most rules charge the player hit. ``not_interrupted`` and ``soak_count_lt`` charge no one. ``per_cluster``
charges the player its ``blame`` spec points at, and ``hits_on_non_tank`` judges each player against the
aggro holder of the NPC instance that hit them. Who an NPC is attacking comes from the Threat data
(``frame.aggro_events``): its melee swings name its target, and a taunt debuff landing on it hands it to the
taunting player. ``per_cluster`` blame and ``hits_on_non_tank`` read the NPC's latest such event.
"""

from __future__ import annotations

import polars as pl

from khadbot.encounter.annotate.frame import is_hit, type_is
from khadbot.encounter.config_files import Blame, Mechanic

# Interrupt matching tolerance for not_interrupted: an interrupt 0-100 ms before the cast counts.
INTERRUPT_TOLERANCE_MS = 100


def _victims(rows: pl.DataFrame) -> dict[int, int | None]:
    """Failure rows charged to the friendly player they hit (None for a non-friendly target)."""
    return {
        seq: (target if friendly else None)
        for seq, target, friendly in rows.select("seq", "target_id", "target_friendly").rows()
    }


def _aggro_holder(events: pl.DataFrame, npc_ids: set[int], t_ms: int, ms: int) -> int | None:
    """The player behind the latest swing or taunt on any of ``npc_ids`` in the ``ms`` up to ``t_ms``."""
    recent = events.filter(pl.col("npc_id").is_in(npc_ids) & pl.col("t_ms").is_between(t_ms - ms, t_ms))
    return recent["player_id"][-1] if recent.height else None  # null after the NPC's death


def _blamed_player(frame: pl.DataFrame, events: pl.DataFrame, blame: Blame, t0: int, npc_ids: set[int]) -> int | None:
    """The player ``blame`` points at for an occurrence whose first hit is at ``t0``; None if nobody."""
    if blame.by == "aggro_holder":
        return _aggro_holder(events, npc_ids, t0, blame.ms)
    cand = frame.filter(
        pl.col("t_ms").is_between(t0 - blame.ms, t0)
        & pl.col("target_friendly")
        & type_is("cast")
        & ~pl.col("source_friendly")
        & (pl.col("ability_id") == blame.ability_id)
    )
    return cand.sort("seq")["target_id"][-1] if cand.height else None


def mechanic_failures(
    frame: pl.DataFrame, mech: Mechanic, duration_ms: int, game_ids: dict[int, int | None], aggro: pl.DataFrame
) -> dict[int, int | None]:
    """Failure rows under ``mech.failure``: row ``seq`` -> the player charged with it (None if no one)."""
    if mech.failure is None:
        return {}
    rule = mech.failure
    rows = frame.filter(pl.col("mechanic_id") == mech.id)
    hits = rows.filter(is_hit())

    if rule.rule == "any_hit":
        return _victims(hits)

    if rule.rule == "hits_per_cluster_gt":
        # One failure per (player, cluster): the hit that takes the player past `value`.
        nth = hits.sort("seq").with_columns(pl.int_range(1, pl.len() + 1).over("cluster_id", "target_id").alias("_n"))
        return _victims(nth.filter(pl.col("_n") == (rule.value or 0) + 1))

    if rule.rule == "soak_count_lt":
        per_cluster = hits.group_by("cluster_id").agg(
            pl.col("target_id").n_unique().alias("_players"), pl.col("seq").min().alias("_first")
        )
        return dict.fromkeys(per_cluster.filter(pl.col("_players") < (rule.value or 0))["_first"].to_list())

    if rule.rule == "not_interrupted":
        # Stateful: small Python loop over this mechanic's enemy casts and matching interrupts only.
        ids = mech.ability_ids
        casts = rows.filter(type_is("cast") & ~pl.col("source_friendly")).select("seq", "t_ms").rows()
        kicks = frame.filter(type_is("interrupt") & pl.col("extra_ability_id").is_in(ids))["t_ms"].to_list()
        return {seq: None for seq, t in casts if not any(t - INTERRUPT_TOLERANCE_MS <= k <= t for k in kicks)}

    if rule.rule == "debuff_duration_gt":
        auras = rows.filter(type_is("applydebuff", "removedebuff") & pl.col("target_friendly")).sort("seq")
        open_since: dict[tuple[int, int], tuple[int, int]] = {}
        failures: dict[int, int | None] = {}
        limit = rule.ms or 0
        for seq, t, typ, target, ability in auras.select(
            "seq", "t_ms", pl.col("type").cast(pl.Utf8), "target_id", "ability_id"
        ).rows():
            key = (target, ability)
            if typ == "applydebuff":
                open_since.setdefault(key, (seq, t))
            elif key in open_since:
                apply_seq, apply_t = open_since.pop(key)
                if t - apply_t > limit:
                    failures[apply_seq] = target
        failures |= {seq: target for (target, _), (seq, t) in open_since.items() if duration_ms - t > limit}
        return failures

    if rule.rule == "hit_amount_gt":
        size = pl.coalesce("unmitigated", pl.col("amount").fill_null(0) + pl.col("absorbed").fill_null(0))
        return _victims(hits.filter(size > (rule.value or 0)))

    if rule.rule == "aura_applied":
        return _victims(rows.filter(type_is("applydebuff", "applybuff") & pl.col("target_friendly")))

    if rule.rule == "hits_on_non_tank":
        # Each player is judged once per cluster, at their first hit, against the aggro holder of the NPC
        # instance that hit them: the latest swing or taunt on it within `ms` before. Later ticks don't
        # count. A player hit while the NPC had no holder is a failure too.
        holders = (
            aggro.filter(pl.col("kind") != "death")
            .select(
                pl.col("npc_id").alias("source_id"),
                pl.col("npc_instance").fill_null(-1).alias("_inst"),
                pl.col("t_ms").alias("_at"),
                pl.col("player_id").alias("_holder"),
            )
            .sort("_at")
        )
        first_hits = hits.sort("seq").filter(pl.int_range(1, pl.len() + 1).over("cluster_id", "target_id") == 1)
        judged = (
            first_hits.with_columns(pl.col("source_instance").fill_null(-1).alias("_inst"))
            .sort("t_ms")
            .join_asof(
                holders,
                left_on="t_ms",
                right_on="_at",
                by=["source_id", "_inst"],
                strategy="backward",
                check_sortedness=False,
            )
        )
        is_tank = (pl.col("_holder") == pl.col("target_id")) & (pl.col("t_ms") - pl.col("_at") <= (rule.ms or 0))
        return _victims(judged.filter(~is_tank.fill_null(False)))

    if rule.rule == "per_cluster":
        # One failure per occurrence, on its first hit, charged to the player the blame spec points at.
        blame = rule.blame
        assert blame is not None  # enforced by FailureRule
        npc_ids = {a for a, g in game_ids.items() if g == blame.npc_game_id}
        firsts = hits.group_by("cluster_id").agg(pl.col("seq").min(), pl.col("t_ms").min())
        return {
            seq: _blamed_player(frame, aggro, blame, t0, npc_ids) for seq, t0 in firsts.select("seq", "t_ms").rows()
        }

    return {}  # pragma: no cover - RuleName is exhaustive
