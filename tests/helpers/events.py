"""
tests/helpers/events.py

Builders for small, hand-written event frames and pulls, for unit tests that need exact
control over a handful of events rather than the whole SYNTH01 fixture.
"""

from __future__ import annotations

import polars as pl

from khadbot.encounter.models import Actor, Pull
from khadbot.encounter.normalize import EVENT_SCHEMA

PLAYERS = [1, 2, 3, 4, 5]
BOSS = 10

_DEFAULT_TYPE = {"damage": "DamageTaken", "heal": "Healing", "death": "Deaths", "cast": "Casts", "begincast": "Casts"}


def make_events(rows: list[dict], friendly: list[int] | None = None) -> pl.DataFrame:
    """
    Events frame from partial rows (``t_ms`` and ``type`` required). ``data_type`` defaults from ``type``;
    friendly flags default from ``friendly`` (PLAYERS). ``seq`` follows the order given.
    """
    friendly_ids = set(PLAYERS if friendly is None else friendly)
    full = []
    for i, r in enumerate(rows):
        row = {name: None for name in EVENT_SCHEMA}
        row.update(seq=i, data_type=_DEFAULT_TYPE.get(r["type"], "Buffs"))
        row.update(r)
        row.setdefault("source_friendly", None)
        if row["source_friendly"] is None:
            row["source_friendly"] = row["source_id"] in friendly_ids
        if row["target_friendly"] is None:
            row["target_friendly"] = row["target_id"] in friendly_ids
        full.append(row)
    return pl.DataFrame(full, schema=EVENT_SCHEMA)


def make_pull(**kw) -> Pull:
    base = dict(
        report_code="TEST",
        fight_id=1,
        encounter_id=90001,
        encounter_name="Synthetic Boss",
        difficulty=5,
        kill=False,
        start_ms=0,
        end_ms=60_000,
        duration_ms=60_000,
        last_phase=None,
        boss_pct=None,
        friendly_player_ids=PLAYERS,
    )
    base.update(kw)
    return Pull(**base)


def make_actors() -> list[Actor]:
    names = {1: "Tank", 2: "Healer", 3: "Alpha", 4: "Beta", 5: "Gamma"}
    actors = [
        Actor(actor_id=i, name=n, kind="player", wcl_type="Player", sub_type=None, owner_id=None, game_id=None)
        for i, n in names.items()
    ]
    actors.append(
        Actor(actor_id=BOSS, name="Boss", kind="boss", wcl_type="NPC", sub_type="Boss", owner_id=None, game_id=1)
    )
    actors.append(Actor(actor_id=11, name="Add", kind="npc", wcl_type="NPC", sub_type="NPC", owner_id=None, game_id=2))
    return actors
