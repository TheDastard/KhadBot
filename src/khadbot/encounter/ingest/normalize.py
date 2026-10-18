"""
encounter/ingest/normalize.py

Raw WCL report metadata and event pages -> typed ``Pull`` / ``Actor`` models, one polars events
frame per pull (schema: ``store.EVENT_SCHEMA``) and, in the same pass over the pages, the pull's
position samples (schema: ``store.POSITION_SCHEMA``).

Pages are turned into small frames and concatenated; no per-event Python objects are
kept beyond a single page's JSON list.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import orjson
import polars as pl

from khadbot.encounter.models import Actor, Pull, WclPhase
from khadbot.encounter.store import EVENT_SCHEMA, POSITION_ORDER, POSITION_SCHEMA, empty_events, empty_positions

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
    phases = {
        e["encounterID"]: [
            WclPhase(id=p["id"], name=p.get("name") or str(p["id"]), is_intermission=bool(p.get("isIntermission")))
            for p in e.get("phases") or []
        ]
        for e in meta.get("phases") or []
        if e.get("encounterID") is not None
    }
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
                wcl_phases=phases.get(f.get("encounterID") or 0, []),
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


def _death_row(e: dict) -> dict:
    """
    WCL Deaths rows name no ability (``abilityGameID: 0``) and no source (``sourceID: -1``); the killing
    blow is in ``killingAbilityGameID`` and ``killerID``. Fill the plain fields from those.
    """
    fixed = dict(e)
    if not e.get("abilityGameID") and e.get("killingAbilityGameID") is not None:
        fixed["abilityGameID"] = e["killingAbilityGameID"]
    if e.get("sourceID", -1) == -1 and e.get("killerID") is not None:
        fixed["sourceID"] = e["killerID"]
    return fixed


def _page_frame(
    events: list[dict],
    data_type: str,
    start_ms: int,
    friendly: set[int],
    positions: dict[str, list] | None = None,
) -> pl.DataFrame:
    """
    One page's rows as an events frame. With ``positions``, also collect a sample into it from every row that
    carries one, including the rows ``_keep`` drops (``_position_sample``).
    """
    cols: dict[str, list] = {name: [] for name in EVENT_SCHEMA if name != "seq"}
    for e in events:
        if positions is not None and "x" in e:
            _position_sample(e, start_ms, positions)
        if not _keep(e, data_type, friendly):
            continue
        if data_type == "Deaths" and e.get("type") == "death":
            e = _death_row(e)
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
    return _normalize(pull, actors, pages_by_type, None)


def normalize_pull_with_positions(
    pull: Pull, actors: list[Actor], pages_by_type: dict[str, Iterable[dict]]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """``normalize_pull`` plus the pull's position table, built in the same pass over the pages."""
    samples: dict[str, list] = {name: [] for name in POSITION_SCHEMA}
    df = _normalize(pull, actors, pages_by_type, samples)
    return df, _positions_frame(samples)


def _normalize(
    pull: Pull, actors: list[Actor], pages_by_type: dict[str, Iterable[dict]], positions: dict[str, list] | None
) -> pl.DataFrame:
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
            frame = _page_frame(page.get("data") or [], base, pull.start_ms, friendly, positions)
            if frame.height:
                frames.append(frame)
                order.extend([rank] * frame.height)

    if not frames:
        return empty_events()

    df = pl.concat(frames).with_columns(pl.Series("_rank", order, dtype=pl.Int8))
    df = df.sort(["t_ms", "_rank"], maintain_order=True).drop("_rank")
    df = df.with_columns(pl.int_range(pl.len(), dtype=pl.UInt32).alias("seq"))
    return df.select(list(EVENT_SCHEMA))


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

# WCL ``resourceActor``: the unit a row's resources and position belong to (raw ID and instance fields).
_RESOURCE_ACTOR = {1: ("sourceID", "sourceInstance"), 2: ("targetID", "targetInstance")}

# Two samples with the same key are one sample seen from two data types (or two page sets).
_POSITION_KEY = ["t_ms", "actor_id", "instance", "x", "y"]


def _position_sample(e: dict, start_ms: int, out: dict[str, list]) -> None:
    """
    Collect the position a raw row carries. It belongs to the unit ``resourceActor`` names: 1 is the source
    (with ``sourceInstance``), 2 the target (with ``targetInstance``). A row naming neither is skipped.
    """
    fields = _RESOURCE_ACTOR.get(e.get("resourceActor", 0))
    if fields is None or e.get(fields[0]) is None or e.get("y") is None:
        return
    out["t_ms"].append(int(e["timestamp"]) - start_ms)
    out["actor_id"].append(e[fields[0]])
    out["instance"].append(e.get(fields[1]))
    out["x"].append(e["x"])
    out["y"].append(e["y"])
    out["map_id"].append(e.get("mapID"))


def _positions_frame(samples: dict[str, list]) -> pl.DataFrame:
    """Samples without duplicates, sorted by actor, instance and time (then position and map, for a stable order)."""
    if not samples["t_ms"]:
        return empty_positions()
    df = pl.DataFrame(samples, schema=POSITION_SCHEMA)
    return df.sort(POSITION_ORDER).unique(subset=_POSITION_KEY, keep="first", maintain_order=True)


def events_hash(df: pl.DataFrame) -> str:
    """SHA-256 over the rows (sorted by ``seq``) serialised with orjson, column order fixed by the schema."""
    h = hashlib.sha256()
    h.update(",".join(EVENT_SCHEMA).encode())
    if df.height:
        ordered = df.select(list(EVENT_SCHEMA)).sort("seq")
        for offset in range(0, ordered.height, 10_000):
            h.update(orjson.dumps(ordered.slice(offset, 10_000).rows()))
    return h.hexdigest()
