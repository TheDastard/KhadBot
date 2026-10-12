"""
encounter/ingest/normalize.py

Raw WCL report metadata and event pages -> typed ``Pull`` / ``Actor`` models and one
polars events frame per pull (schema in plan section 6.1).

Pages are turned into small frames and concatenated; no per-event Python objects are
kept beyond a single page's JSON list.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import orjson
import polars as pl

from khadbot.encounter.models import Actor, Pull
from khadbot.encounter.store import EVENT_SCHEMA, empty_events

# ---------------------------------------------------------------------------
# Ordering and field mapping
# ---------------------------------------------------------------------------

# Sort tie-break between data types at the same millisecond.
DATA_TYPE_ORDER = ["Deaths", "DamageTaken", "Casts", "Buffs", "Debuffs", "Interrupts", "Dispels", "Healing", "Threat"]

_ENEMIES_SUFFIX = "-Enemies"

# WCL raw field -> frame column, for plain numeric passthrough.
_INT_FIELDS = {
    "sourceID": "source_id",
    "targetID": "target_id",
    "abilityGameID": "ability_id",
    "extraAbilityGameID": "extra_ability_id",
    "amount": "amount",
    "absorbed": "absorbed",
    "overkill": "overkill",
    "overheal": "overheal",
    "unmitigatedAmount": "unmitigated",
    "hitType": "hit_type",
    "stack": "stack",
    "sourceInstance": "source_instance",
    "targetInstance": "target_instance",
}


# ---------------------------------------------------------------------------
# Report metadata
# ---------------------------------------------------------------------------


def _actor_kind(wcl_type: str | None, sub_type: str | None) -> str:
    if wcl_type == "Player":
        return "player"
    if wcl_type == "Pet":
        return "pet"
    if sub_type == "Boss":
        return "boss"
    return "npc"


def parse_report_meta(code: str, meta: dict) -> tuple[list[Pull], list[Actor]]:
    """Turn the cached ``reportData.report`` object into pulls and the actor roster."""
    actors = [
        Actor(
            actor_id=a["id"],
            name=a.get("name") or f"actor-{a['id']}",
            kind=_actor_kind(a.get("type"), a.get("subType")),  # type: ignore[arg-type]
            wcl_type=a.get("type"),
            sub_type=a.get("subType"),
            owner_id=a.get("petOwner"),
            game_id=a.get("gameID"),
        )
        for a in (meta.get("masterData") or {}).get("actors") or []
    ]
    pulls = []
    for f in meta.get("fights") or []:
        start, end = int(f["startTime"]), int(f["endTime"])
        pulls.append(
            Pull(
                report_code=code,
                fight_id=f["id"],
                encounter_id=f.get("encounterID") or 0,
                encounter_name=f.get("name") or "",
                difficulty=f.get("difficulty"),
                kill=bool(f.get("kill")),
                start_ms=start,
                end_ms=end,
                duration_ms=end - start,
                last_phase=f.get("lastPhase"),
                boss_pct=f.get("fightPercentage"),
                boss_hp_pct=f.get("bossPercentage"),
                phase_transitions=[(t["id"], int(t["startTime"]) - start) for t in f.get("phaseTransitions") or []],
                friendly_player_ids=list(f.get("friendlyPlayers") or []),
            )
        )
    return pulls, actors


def parse_abilities(meta: dict) -> dict[int, str]:
    """``masterData.abilities`` as ``{gameID: name}``."""
    return {
        int(a["gameID"]): a.get("name") or f"ability-{a['gameID']}"
        for a in (meta.get("masterData") or {}).get("abilities") or []
        if a.get("gameID") is not None
    }


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _friendly_ids(pull: Pull, actors: list[Actor]) -> set[int]:
    players = set(pull.friendly_player_ids)
    pets = {a.actor_id for a in actors if a.owner_id in players}
    return players | pets


def _keep(e: dict, data_type: str, friendly: set[int]) -> bool:
    """
    Threat pages hold NPC melee swings, taunts and NPC deaths. Players' casts (taunts included) repeat the
    Casts data and are dropped; so would a player's death, which the Deaths data already holds. NPC
    deaths are kept: no other data type has them, and they end that NPC's aggro.
    """
    if data_type != "Threat":
        return True
    typ = e.get("type")
    if typ == "death":
        return e.get("targetID") not in friendly
    return not (typ == "cast" and e.get("sourceID") in friendly)


def _page_frame(events: list[dict], data_type: str, start_ms: int, friendly: set[int]) -> pl.DataFrame:
    cols: dict[str, list] = {name: [] for name in EVENT_SCHEMA if name != "seq"}
    for e in events:
        if not _keep(e, data_type, friendly):
            continue
        cols["t_ms"].append(int(e["timestamp"]) - start_ms)
        cols["data_type"].append(data_type)
        cols["type"].append(e.get("type", ""))
        for raw, col in _INT_FIELDS.items():
            cols[col].append(e.get(raw))
        hp, max_hp = e.get("hitPoints"), e.get("maxHitPoints")
        cols["hp_pct"].append(hp / max_hp * 100 if hp is not None and max_hp else None)
        cols["source_friendly"].append(e.get("sourceID") in friendly)
        cols["target_friendly"].append(e.get("targetID") in friendly)
    schema = {k: v for k, v in EVENT_SCHEMA.items() if k != "seq"}
    return pl.DataFrame(cols, schema=schema)


def normalize_pull(pull: Pull, actors: list[Actor], pages_by_type: dict[str, Iterable[dict]]) -> pl.DataFrame:
    """
    Build the events frame for one pull.

    ``pages_by_type`` maps a spec ``dir_name`` (``"Casts"``, ``"Casts-Enemies"``, ...) to its raw
    pages (dicts with a ``data`` list). Rows are stably sorted by (t_ms, data-type order, page order)
    and ``seq`` is assigned afterwards.
    """
    friendly = _friendly_ids(pull, actors)
    frames: list[pl.DataFrame] = []
    order: list[int] = []

    def sort_key(name: str) -> tuple[int, str]:
        base = name.removesuffix(_ENEMIES_SUFFIX)
        return (DATA_TYPE_ORDER.index(base) if base in DATA_TYPE_ORDER else len(DATA_TYPE_ORDER), name)

    for name in sorted(pages_by_type, key=sort_key):
        base = name.removesuffix(_ENEMIES_SUFFIX)
        rank = sort_key(name)[0]
        for page in pages_by_type[name]:
            frame = _page_frame(page.get("data") or [], base, pull.start_ms, friendly)
            if frame.height:
                frames.append(frame)
                order.extend([rank] * frame.height)

    if not frames:
        return empty_events()

    df = pl.concat(frames).with_columns(pl.Series("_rank", order, dtype=pl.Int8))
    df = df.sort(["t_ms", "_rank"], maintain_order=True).drop("_rank")
    df = df.with_columns(pl.int_range(pl.len(), dtype=pl.UInt32).alias("seq"))
    return df.select(list(EVENT_SCHEMA))


def events_hash(df: pl.DataFrame) -> str:
    """SHA-256 over the rows (sorted by ``seq``) serialised with orjson, column order fixed by the schema."""
    h = hashlib.sha256()
    h.update(",".join(EVENT_SCHEMA).encode())
    if df.height:
        ordered = df.select(list(EVENT_SCHEMA)).sort("seq")
        for offset in range(0, ordered.height, 10_000):
            h.update(orjson.dumps(ordered.slice(offset, 10_000).rows()))
    return h.hexdigest()
