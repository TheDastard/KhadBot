"""
encounter/analysis/deaths.py

Death records: one per player death, each with its own pre-death window (unlike the legacy tool,
which built a window only for a player's first death).

A death's window is the ``window_ms`` before it, clipped to the actor's previous death so a second
death after a battle res never reuses damage from the first life.

The game strips a dying player's auras a few milliseconds before WCL's death row, so a defensive
removed just before the death still counts as active when the cooldown was still running: its aura
was still up on another player, or its registry duration had not run out.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence

import polars as pl

from khadbot.encounter.analysis.cooldowns import AuraInterval, aura_intervals
from khadbot.encounter.annotate import Annotated, type_is
from khadbot.encounter.config_files import CooldownRegistry, FriendlyDamageRegistry
from khadbot.encounter.models import Actor, DeathRecord, Hit, actor_label, make_ref

DEATH_WINDOW_MS = 10_000
TOP_HITS = 5
HEALTHY_PCT = 90.0
# Auras removed this long before a death row are taken as stripped by the death (1-56 ms on the recorded nights).
DEATH_STRIP_MS = 100
# The columns a death reads from its actor's rows (``type`` is added as text).
_ROW_COLUMNS = (
    "target_id",
    "t_ms",
    "amount",
    "ability_id",
    "source_id",
    "source_instance",
    "overkill",
    "hp_pct",
    "mechanic_id",
)


def death_records(
    a: Annotated,
    actors: list[Actor],
    defensive: CooldownRegistry | None = None,
    abilities: Mapping[int, str] | None = None,
    *,
    friendly: FriendlyDamageRegistry | None = None,
    window_ms: int = DEATH_WINDOW_MS,
) -> list[DeathRecord]:
    """
    Every player death in time order; refs ``death:<n>`` are 1-based in that order. ``friendly`` names the
    self-inflicted abilities counted in ``window_self_damage``.

    Each dying actor's rows are read into Python lists once, and a death's life and window are found by bisect on
    the actor's times, so no per-death work touches the whole frame.
    """
    pull = a.pull
    names = {x.actor_id: x.name for x in actors}
    abilities = abilities or {}
    self_ids = friendly.ability_ids(["self_inflicted"]) if friendly else []
    frame = a.frame
    has_healing = (frame["data_type"].cast(pl.Utf8) == "Healing").any()
    intervals = aura_intervals(frame, defensive.aura_ids() if defensive else {}, pull.duration_ms)

    deaths = (
        frame.filter(type_is("death") & pl.col("target_id").is_in(pull.friendly_player_ids))
        .sort("seq")
        .select("t_ms", "target_id", "ability_id", "phase_id")
        .rows()
    )
    self_set = set(self_ids)
    # Each dying actor's rows, once, as Python lists in ``seq`` order (``annotate`` sorts the frame by ``seq``).
    dying = {actor_id for _, actor_id, _, _ in deaths}
    by_actor: dict[int, dict[str, list]] = {}
    time_sorted: dict[int, bool] = {}
    if dying:
        rows = (
            frame.filter(pl.col("target_id").is_in(list(dying)))
            .select(*_ROW_COLUMNS, type=pl.col("type").cast(pl.Utf8))
            .partition_by("target_id", as_dict=True, maintain_order=True)
        )
        by_actor = {_actor_id(k): part.to_dict(as_series=False) for k, part in rows.items()}
        # Whether bisect applies: an actor's times have no nulls and never decrease.
        time_sorted = {
            _actor_id(k): part["t_ms"].null_count() == 0 and part["t_ms"].is_sorted() for k, part in rows.items()
        }
    no_rows: dict[str, list] = {c: [] for c in (*_ROW_COLUMNS, "type")}

    previous_death: dict[int, int] = {}
    out = []
    for n, (t, actor_id, death_ability, phase_id) in enumerate(deaths, start=1):
        life_start = previous_death.get(actor_id)
        previous_death[actor_id] = t
        lower = t - window_ms if life_start is None else max(t - window_ms, life_start + 1)
        cols = by_actor.get(actor_id, no_rows)
        times = cols["t_ms"]
        life: Sequence[int]
        window: Sequence[int]
        if time_sorted.get(actor_id, True):
            # Sorted by time within the actor, so the life and the window are contiguous index ranges.
            end = bisect_right(times, t)
            start = 0 if life_start is None else min(bisect_right(times, life_start), end)
            first = min(max(bisect_left(times, lower), start), end)
            life, window = range(start, end), range(first, end)
        else:
            life = [
                i for i, x in enumerate(times) if x is not None and x <= t and (life_start is None or x > life_start)
            ]
            window = [i for i in life if times[i] >= lower]
        types, amounts = cols["type"], cols["amount"]
        damage = [i for i in window if types[i] == "damage"]  # seq order

        killing_blow = damage[-1] if damage else None
        # A death ability of 0 (events normalized before the killing ability was filled in) is unknown too.
        killing_ability = death_ability or (cols["ability_id"][killing_blow] if killing_blow is not None else None)

        # A null amount counts as 0; the sort is stable, so equal amounts stay in ``seq`` order.
        top_hits = [
            Hit(
                t_ms=times[i],
                ability_id=cols["ability_id"][i],
                ability_name=abilities.get(cols["ability_id"][i]),
                amount=amounts[i] or 0,
                source_name=actor_label(names, cols["source_id"][i], cols["source_instance"][i])
                if cols["source_id"][i] is not None
                else None,
            )
            for i in sorted(damage, key=lambda i: -(amounts[i] or 0))[:TOP_HITS]
        ]

        out.append(
            DeathRecord(
                ref=make_ref(pull.pull_key, "death", n),
                actor_id=actor_id,
                name=names.get(actor_id, f"actor-{actor_id}"),
                t_ms=t,
                phase_id=phase_id,
                killing_ability_id=killing_ability,
                killing_ability_name=abilities.get(killing_ability) if killing_ability is not None else None,
                overkill=cols["overkill"][killing_blow] if killing_blow is not None else None,
                time_to_die_ms=_time_to_die(times, cols["hp_pct"], life, t),
                window_ms=window_ms,
                window_damage=sum(amounts[i] or 0 for i in damage),
                window_healing=sum(amounts[i] or 0 for i in window if types[i] == "heal") if has_healing else None,
                window_self_damage=sum(amounts[i] or 0 for i in damage if cols["ability_id"][i] in self_set),
                top_hits=top_hits,
                mechanic_ids=sorted({m for i in damage if (m := cols["mechanic_id"][i]) is not None}),
                active_defensives=_defensives_at_death(intervals, actor_id, t, defensive),
                after_cutoff=a.cutoff_ms is not None and t > a.cutoff_ms,
            )
        )
    return out


def _actor_id(key: object) -> int:
    """The actor ID in a ``partition_by`` key, which polars returns as a one-tuple for a single column."""
    return key[0] if isinstance(key, tuple) else key  # type: ignore[return-value]


def _defensives_at_death(
    intervals: list[AuraInterval], actor_id: int, t: int, defensive: CooldownRegistry | None
) -> list[int]:
    """Registry spells active on ``actor_id`` at ``t``, counting an aura stripped by the death (module docstring)."""
    out = set()
    for i in intervals:
        if i.target_id != actor_id:
            continue
        if i.active_at(t):
            out.add(i.spell_id)
        elif 0 < t - i.end_ms <= DEATH_STRIP_MS:
            entry = defensive.get(i.spell_id) if defensive else None
            still_running = (
                entry is not None and entry.duration_s is not None and i.start_ms + entry.duration_s * 1000 > t
            ) or any(o.spell_id == i.spell_id and o.target_id != actor_id and o.active_at(t) for o in intervals)
            if still_running:
                out.add(i.spell_id)
    return sorted(out)


def _time_to_die(times: list[int], hp_pct: list[float | None], life: Sequence[int], death_t: int) -> int | None:
    """Death time minus the last time the actor was at >= 90% HP in this life; None without such a reading."""
    healthy = [times[i] for i in life if (h := hp_pct[i]) is not None and h >= HEALTHY_PCT]
    return death_t - max(healthy) if healthy else None
