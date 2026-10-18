"""
encounter/annotate/failures.py

Failure rules: which rows of a mechanic are failures, and who is charged with each.

Most rules charge the player hit. ``not_interrupted``, ``soak_count_lt`` and ``debuff_stacks_gt`` charge no
one. ``per_cluster`` charges the player its ``blame`` spec points at, and ``hits_on_non_tank`` judges each
player against the aggro holder of the NPC instance that hit them. Who an NPC is attacking comes from the Threat data
(``frame.aggro_events``): its melee swings name its target, and a taunt debuff landing on it hands it to the
taunting player. ``per_cluster`` blame and ``hits_on_non_tank`` read the NPC's latest such event.

``blame_context`` adds where a ``per_cluster`` failure's blamed player was at the occurrence's first hit
(``blame_state``, see frame.py): dead, or alive and in or beyond the NPC's reach, from the position table.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from khadbot.encounter.analysis.positions import distance_yd, position_at
from khadbot.encounter.annotate.frame import is_hit, type_is
from khadbot.encounter.config_files import Blame, Mechanic
from khadbot.encounter.models import BlameState

# Interrupt matching tolerance for not_interrupted: an interrupt 0-100 ms before the cast counts.
INTERRUPT_TOLERANCE_MS = 100

# Oldest player sample blame_context accepts. A raid-wide hit carries the hit player's own position (age 0);
# a living aggro holder's latest sample was at most 475 ms old on the 47 Heroic Ula'tek pulls.
BLAME_POSITION_MAX_AGE_MS = 1_500

# What shows a player who died is alive again: a cast of their own, or a resurrect landing on them.
_ALIVE_AGAIN_SOURCE = ("cast", "begincast")
_ALIVE_AGAIN_TARGET = ("resurrect",)

_AURA_OPEN = ("applybuff", "applydebuff", "applybuffstack", "applydebuffstack", "refreshbuff", "refreshdebuff")
_AURA_CLOSE = ("removebuff", "removedebuff")


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


def _aura_spans(frame: pl.DataFrame, aura_ids: list[int], duration_ms: int) -> dict[int, list[tuple[int, int]]]:
    """
    Friendly target -> ``(start_ms, end_ms)`` spans during which it carried any of ``aura_ids``, both ends
    inclusive. A span opens on an apply (or a refresh or stack row with no open span) and closes on the
    matching remove; one never removed lasts to the pull end, and a remove with no apply before it opens at
    the pull start.
    """
    auras = frame.filter(
        type_is(*_AURA_OPEN, *_AURA_CLOSE) & pl.col("target_friendly") & pl.col("ability_id").is_in(aura_ids)
    ).sort("seq")
    open_since: dict[tuple[int, int], int] = {}
    spans: dict[int, list[tuple[int, int]]] = {}
    for t, typ, target, ability in auras.select("t_ms", pl.col("type").cast(pl.Utf8), "target_id", "ability_id").rows():
        key = (target, ability)
        if typ in _AURA_OPEN:
            open_since.setdefault(key, t)
        else:
            spans.setdefault(target, []).append((open_since.pop(key, 0), t))
    for (target, _), t in open_since.items():
        spans.setdefault(target, []).append((t, duration_ms))
    return spans


def _blamed(
    frame: pl.DataFrame, events: pl.DataFrame, blame: Blame, t0: int, npc_ids: set[int]
) -> tuple[int | None, set[int]]:
    """
    The player ``blame`` points at for an occurrence whose first hit is at ``t0`` (None if nobody), and the
    NPC actor IDs whose reach applies: ``npc_ids`` for ``aggro_holder``, the cast's caster for ``cast_target``.
    """
    if blame.by == "aggro_holder":
        return _aggro_holder(events, npc_ids, t0, blame.ms), npc_ids
    cand = frame.filter(
        pl.col("t_ms").is_between(t0 - blame.ms, t0)
        & pl.col("target_friendly")
        & type_is("cast")
        & ~pl.col("source_friendly")
        & (pl.col("ability_id") == blame.ability_id)
    )
    if not cand.height:
        return None, set()
    target, caster = cand.sort("seq").select("target_id", "source_id").row(-1)
    return target, {caster}


def _occurrences(frame: pl.DataFrame, mech: Mechanic) -> list[tuple[int, int]]:
    """``(seq, t_ms)`` of the first hit of each of the mechanic's clusters."""
    hits = frame.filter((pl.col("mechanic_id") == mech.id) & is_hit())
    firsts = hits.group_by("cluster_id").agg(pl.col("seq").min(), pl.col("t_ms").min()).sort("seq")
    return list(firsts.select("seq", "t_ms").rows())


def _blame_npcs(blame: Blame, game_ids: dict[int, int | None]) -> set[int]:
    """Actor IDs of the NPC an ``aggro_holder`` spec names (by game ID); empty for ``cast_target``."""
    return {a for a, g in game_ids.items() if g == blame.npc_game_id} if blame.by == "aggro_holder" else set()


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

    if rule.rule == "debuff_stacks_gt":
        # One failure per occurrence, on its first row, charged to no one. Occurrences group the stack rows
        # past `value` with the mechanic's cluster gap; they are not hits, so they belong to no cluster.
        stacks = rows.filter(
            type_is("applydebuff", "applydebuffstack")
            & pl.col("target_friendly")
            & (pl.col("stack") > (rule.value or 0))
        ).sort("seq")
        new_occurrence = (pl.col("t_ms").diff() > mech.cluster_window_ms).fill_null(True)
        return dict.fromkeys(stacks.filter(new_occurrence)["seq"].to_list())

    if rule.rule == "hit_while_aura":
        # Each player is judged once per cluster, at their first hit, against the listed auras' activity.
        assert rule.aura_ids is not None  # enforced by FailureRule
        spans = _aura_spans(frame, rule.aura_ids, duration_ms)
        first_hits = hits.sort("seq").filter(pl.int_range(1, pl.len() + 1).over("cluster_id", "target_id") == 1)
        return {
            seq: target
            for seq, t, target in first_hits.select("seq", "t_ms", "target_id").rows()
            if any(start <= t <= end for start, end in spans.get(target, ()))
        }

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
        npc_ids = _blame_npcs(blame, game_ids)
        return {seq: _blamed(frame, aggro, blame, t0, npc_ids)[0] for seq, t0 in _occurrences(frame, mech)}

    return {}  # pragma: no cover - RuleName is exhaustive


# ---------------------------------------------------------------------------
# Blame context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlameContext:
    """Where a failure's blamed player was at the occurrence's first hit (the annotation's ``blame_*`` columns)."""

    state: BlameState
    dist_yd: float | None = None  # from the NPC, rounded to 0.1; living players with a position only
    pos_age_ms: int | None = None  # age of the player's sample


def _dead_at(frame: pl.DataFrame, player: int, t0: int) -> bool:
    """True if ``player`` has a Deaths row at or before ``t0`` and no cast or resurrect since."""
    deaths = frame.filter(
        (pl.col("data_type").cast(pl.Utf8) == "Deaths")
        & type_is("death")
        & (pl.col("target_id") == player)
        & (pl.col("t_ms") <= t0)
    )
    if deaths.is_empty():
        return False
    died = deaths["t_ms"].max()
    since = frame.filter(
        (pl.col("t_ms") > died)
        & (pl.col("t_ms") <= t0)
        & (
            ((pl.col("source_id") == player) & type_is(*_ALIVE_AGAIN_SOURCE))
            | ((pl.col("target_id") == player) & type_is(*_ALIVE_AGAIN_TARGET))
        )
    )
    return since.is_empty()


def _context(
    frame: pl.DataFrame, positions: pl.DataFrame, player: int | None, npc_ids: set[int], t0: int, reach: float | None
) -> BlameContext:
    if player is None:
        return BlameContext("no_holder")
    if _dead_at(frame, player, t0):
        return BlameContext("dead")
    mine = position_at(positions, [player], t0, max_age_ms=BLAME_POSITION_MAX_AGE_MS)
    # NPCs are sampled only on their own swings and casts, but stand on fixed spots: no age limit.
    npc = position_at(positions, npc_ids, t0, max_age_ms=None) if npc_ids else None
    dist = distance_yd(mine, npc) if mine is not None and npc is not None else None
    if mine is None or dist is None:
        return BlameContext("no_position")
    if reach is None:
        state: BlameState = "alive"
    else:
        state = "beyond_reach" if dist > reach else "in_reach"
    return BlameContext(state, round(dist, 1), mine.age_ms)


def blame_context(
    frame: pl.DataFrame,
    mech: Mechanic,
    game_ids: dict[int, int | None],
    aggro: pl.DataFrame,
    positions: pl.DataFrame,
) -> dict[int, BlameContext]:
    """
    For a ``per_cluster`` mechanic, the blame context of each failure row (``seq``, the occurrence's first hit).

    ``no_holder``: the blame spec found nobody. ``dead``: the blamed player has a Deaths row at or before the
    hit and no cast or resurrect since. Otherwise their latest sample at or before the hit, at most
    ``BLAME_POSITION_MAX_AGE_MS`` old, against the NPC's latest sample: ``beyond_reach`` or ``in_reach``
    against the blame's ``reach_yd``, ``alive`` when it has none, ``no_position`` when either sample is
    missing or the two are on different maps. Other mechanics get no context.
    """
    if mech.failure is None or mech.failure.rule != "per_cluster" or mech.failure.blame is None:
        return {}
    blame = mech.failure.blame
    npc_ids = _blame_npcs(blame, game_ids)
    out = {}
    for seq, t0 in _occurrences(frame, mech):
        player, npcs = _blamed(frame, aggro, blame, t0, npc_ids)
        out[seq] = _context(frame, positions, player, npcs, t0, blame.reach_yd)
    return out
