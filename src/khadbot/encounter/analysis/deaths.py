"""
encounter/analysis/deaths.py

Death records: one per player death, each with its own pre-death window (unlike the legacy tool,
which built a window only for a player's first death).

A death's window is the ``window_ms`` before it, clipped to the actor's previous death so a second
death after a battle res never reuses damage from the first life.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from khadbot.encounter.analysis.cooldowns import aura_intervals
from khadbot.encounter.annotate import Annotated, type_is
from khadbot.encounter.config_files import CooldownRegistry
from khadbot.encounter.models import Actor, DeathRecord, Hit, make_ref

DEATH_WINDOW_MS = 10_000
TOP_HITS = 5
HEALTHY_PCT = 90.0


def death_records(
    a: Annotated,
    actors: list[Actor],
    registry: CooldownRegistry | None = None,
    abilities: Mapping[int, str] | None = None,
    *,
    window_ms: int = DEATH_WINDOW_MS,
) -> list[DeathRecord]:
    """Every player death in time order; refs ``death:<n>`` are 1-based in that order."""
    pull = a.pull
    names = {x.actor_id: x.name for x in actors}
    abilities = abilities or {}
    frame = a.frame
    has_healing = (frame["data_type"].cast(pl.Utf8) == "Healing").any()
    intervals = aura_intervals(frame, registry.spell_ids() if registry else [], pull.duration_ms)

    deaths = (
        frame.filter(type_is("death") & pl.col("target_id").is_in(pull.friendly_player_ids))
        .sort("seq")
        .select("t_ms", "target_id", "ability_id", "phase_id")
        .rows()
    )
    on_players = frame.filter(pl.col("target_id").is_in(pull.friendly_player_ids))

    previous_death: dict[int, int] = {}
    out = []
    for n, (t, actor_id, death_ability, phase_id) in enumerate(deaths, start=1):
        life_start = previous_death.get(actor_id)
        previous_death[actor_id] = t
        lower = t - window_ms if life_start is None else max(t - window_ms, life_start + 1)
        life = on_players.filter((pl.col("target_id") == actor_id) & (pl.col("t_ms") <= t))
        if life_start is not None:
            life = life.filter(pl.col("t_ms") > life_start)
        window = life.filter(pl.col("t_ms") >= lower)
        damage = window.filter(type_is("damage")).sort("seq")
        heals = window.filter(type_is("heal"))

        killing_blow = damage.row(-1, named=True) if damage.height else None
        killing_ability = death_ability if death_ability is not None else (killing_blow or {}).get("ability_id")

        top = damage.with_columns(pl.col("amount").fill_null(0)).sort("amount", descending=True, maintain_order=True)
        top_hits = [
            Hit(
                t_ms=r["t_ms"],
                ability_id=r["ability_id"],
                ability_name=abilities.get(r["ability_id"]),
                amount=r["amount"],
                source_name=names.get(r["source_id"]),
            )
            for r in top.head(TOP_HITS).iter_rows(named=True)
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
                overkill=killing_blow["overkill"] if killing_blow else None,
                time_to_die_ms=_time_to_die(life, t),
                window_ms=window_ms,
                window_damage=int(damage["amount"].sum() or 0),
                window_healing=int(heals["amount"].sum() or 0) if has_healing else None,
                top_hits=top_hits,
                mechanic_ids=sorted(damage["mechanic_id"].drop_nulls().unique().to_list()),
                active_defensives=sorted({i.spell_id for i in intervals if i.target_id == actor_id and i.active_at(t)}),
                after_cutoff=a.cutoff_ms is not None and t > a.cutoff_ms,
            )
        )
    return out


def _time_to_die(life: pl.DataFrame, death_t: int) -> int | None:
    """Death time minus the last time the actor was at >= 90% HP in this life; None without such a reading."""
    healthy = life.filter(pl.col("hp_pct") >= HEALTHY_PCT)["t_ms"].to_list()
    return death_t - max(healthy) if healthy else None
