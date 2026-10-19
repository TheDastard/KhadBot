"""
encounter/store.py

Event store: the normalized event and position schemas, and events and positions per pull, behind a
protocol so the implementation (polars + parquet today, possibly DuckDB later) can change without touching
callers.

Layout (rooted at ``$KHADBOT_DATA_DIR/events``)::

    <report>/pulls.json        # [{"pull": Pull, "events_hash": str, "positions_hash": str | None,
                               #   "truncated": [dir_name]}]
    <report>/actors.parquet
    <report>/abilities.json    # {ability_id: name} from report masterData
    <report>/<fight>.parquet
    <report>/positions/<fight>.parquet

A record written before positions were stored has no ``positions_hash`` and no position table; it reads
as an empty table until the pull is re-ingested.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import orjson
import polars as pl

from khadbot.encounter.models import Actor, Pull

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

EVENT_SCHEMA: dict[str, pl.DataType] = {
    "seq": pl.UInt32(),
    "t_ms": pl.Int32(),
    "data_type": pl.Categorical(),
    "type": pl.Categorical(),
    "source_id": pl.Int32(),
    "target_id": pl.Int32(),
    "ability_id": pl.Int32(),
    "extra_ability_id": pl.Int32(),
    "amount": pl.Int64(),
    "absorbed": pl.Int64(),
    "overkill": pl.Int64(),
    "overheal": pl.Int64(),
    "unmitigated": pl.Int64(),
    "hit_type": pl.Int8(),
    "hp_pct": pl.Float32(),
    "stack": pl.Int16(),
    "source_friendly": pl.Boolean(),
    "target_friendly": pl.Boolean(),
    "source_instance": pl.Int32(),
    "target_instance": pl.Int32(),
}


def empty_events() -> pl.DataFrame:
    return pl.DataFrame(schema=EVENT_SCHEMA)


# One row per position sample: where a unit was at ``t_ms``, in WCL's map units (1/100 yard). ``instance``
# numbers NPCs that share an actor ID and is null for unique actors and players.
POSITION_SCHEMA: dict[str, pl.DataType] = {
    "t_ms": pl.Int32(),
    "actor_id": pl.Int32(),
    "instance": pl.Int32(),
    "x": pl.Int32(),
    "y": pl.Int32(),
    "map_id": pl.Int32(),
}

# The position table's row order, which positions_hash relies on.
POSITION_ORDER = ["actor_id", "instance", "t_ms", "x", "y", "map_id"]


def empty_positions() -> pl.DataFrame:
    return pl.DataFrame(schema=POSITION_SCHEMA)


def positions_hash(df: pl.DataFrame) -> str:
    """SHA-256 over the position rows (in ``POSITION_ORDER``) serialised with orjson, columns fixed by the schema."""
    h = hashlib.sha256()
    h.update(",".join(POSITION_SCHEMA).encode())
    if df.height:
        ordered = df.select(list(POSITION_SCHEMA)).sort(POSITION_ORDER)
        for offset in range(0, ordered.height, 10_000):
            h.update(orjson.dumps(ordered.slice(offset, 10_000).rows()))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class EventStore(Protocol):
    def write_pull(
        self,
        pull: Pull,
        df: pl.DataFrame,
        events_hash: str,
        truncated: list[str] | None = None,
        positions: pl.DataFrame | None = None,
    ) -> None: ...

    def write_actors(self, code: str, actors: list[Actor]) -> None: ...

    def write_abilities(self, code: str, abilities: dict[int, str]) -> None: ...

    def reports(self) -> list[str]: ...

    def pulls(self, code: str) -> list[Pull]: ...

    def events_hash(self, pull_key: str) -> str | None: ...

    def positions_hash(self, pull_key: str) -> str | None: ...

    def truncated(self, pull_key: str) -> list[str]: ...

    def actors(self, code: str) -> list[Actor]: ...

    def abilities(self, code: str) -> dict[int, str]: ...

    def events(
        self,
        pull_key: str,
        *,
        types: list[str] | None = None,
        actor_id: int | None = None,
        ability_ids: list[int] | None = None,
        t_from: int | None = None,
        t_to: int | None = None,
    ) -> pl.DataFrame: ...

    def positions(self, pull_key: str) -> pl.DataFrame: ...


def split_pull_key(pull_key: str) -> tuple[str, int]:
    code, _, fight = pull_key.rpartition(":")
    return code, int(fight)


def _atomic(path: Path, write: Callable[[Path], object]) -> None:
    """Run ``write(tmp_path)`` then ``os.replace`` onto ``path`` so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class ParquetEventStore:
    """EventStore over per-pull parquet files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # --- paths ---------------------------------------------------------------

    def _pulls_path(self, code: str) -> Path:
        return self.root / code / "pulls.json"

    def _events_path(self, pull_key: str) -> Path:
        code, fight = split_pull_key(pull_key)
        return self.root / code / f"{fight}.parquet"

    def _positions_path(self, pull_key: str) -> Path:
        code, fight = split_pull_key(pull_key)
        return self.root / code / "positions" / f"{fight}.parquet"

    def _records(self, code: str) -> list[dict]:
        path = self._pulls_path(code)
        return orjson.loads(path.read_bytes()) if path.exists() else []

    # --- writes --------------------------------------------------------------

    def write_pull(
        self,
        pull: Pull,
        df: pl.DataFrame,
        events_hash: str,
        truncated: list[str] | None = None,
        positions: pl.DataFrame | None = None,
    ) -> None:
        """
        Store a pull's events and, when given, its position table (hashed here). Without ``positions`` the pull
        has no position table: a stale one is removed and the record's ``positions_hash`` is null.
        """
        _atomic(self._events_path(pull.pull_key), lambda tmp: df.write_parquet(tmp))
        pos_path = self._positions_path(pull.pull_key)
        pos_hash: str | None = None
        if positions is not None:
            table = positions.select(list(POSITION_SCHEMA))
            _atomic(pos_path, lambda tmp: table.write_parquet(tmp))
            pos_hash = positions_hash(table)
        else:
            pos_path.unlink(missing_ok=True)
        records = [r for r in self._records(pull.report_code) if r["pull"]["fight_id"] != pull.fight_id]
        records.append(
            {
                "pull": pull.model_dump(mode="json"),
                "events_hash": events_hash,
                "positions_hash": pos_hash,
                "truncated": truncated or [],
            }
        )
        records.sort(key=lambda r: r["pull"]["fight_id"])
        _atomic(self._pulls_path(pull.report_code), lambda tmp: tmp.write_bytes(orjson.dumps(records)))

    def write_actors(self, code: str, actors: list[Actor]) -> None:
        df = pl.DataFrame([a.model_dump() for a in actors], schema=_ACTOR_SCHEMA)
        _atomic(self.root / code / "actors.parquet", lambda tmp: df.write_parquet(tmp))

    def write_abilities(self, code: str, abilities: dict[int, str]) -> None:
        data = orjson.dumps(abilities, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SORT_KEYS)
        _atomic(self.root / code / "abilities.json", lambda tmp: tmp.write_bytes(data))

    # --- reads ---------------------------------------------------------------

    def reports(self) -> list[str]:
        """Codes of the reports with at least one stored pull, sorted."""
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and self._records(p.name))

    def pulls(self, code: str) -> list[Pull]:
        return [Pull.model_validate(r["pull"]) for r in self._records(code)]

    def _record(self, pull_key: str) -> dict | None:
        code, fight = split_pull_key(pull_key)
        return next((r for r in self._records(code) if r["pull"]["fight_id"] == fight), None)

    def events_hash(self, pull_key: str) -> str | None:
        rec = self._record(pull_key)
        return rec["events_hash"] if rec else None

    def positions_hash(self, pull_key: str) -> str | None:
        rec = self._record(pull_key)
        return rec.get("positions_hash") if rec else None

    def truncated(self, pull_key: str) -> list[str]:
        rec = self._record(pull_key)
        return list(rec["truncated"]) if rec else []

    def actors(self, code: str) -> list[Actor]:
        path = self.root / code / "actors.parquet"
        if not path.exists():
            return []
        return [Actor(**row) for row in pl.read_parquet(path).to_dicts()]

    def abilities(self, code: str) -> dict[int, str]:
        path = self.root / code / "abilities.json"
        if not path.exists():
            return {}
        return {int(k): v for k, v in orjson.loads(path.read_bytes()).items()}

    def events(
        self,
        pull_key: str,
        *,
        types: list[str] | None = None,
        actor_id: int | None = None,
        ability_ids: list[int] | None = None,
        t_from: int | None = None,
        t_to: int | None = None,
    ) -> pl.DataFrame:
        """Filtered events, ``t_from``/``t_to`` inclusive. ``actor_id`` matches source or target."""
        path = self._events_path(pull_key)
        if not path.exists():
            return empty_events()
        lf = pl.scan_parquet(path)
        if types is not None:
            lf = lf.filter(pl.col("type").cast(pl.Utf8).is_in(types))
        if actor_id is not None:
            lf = lf.filter((pl.col("source_id") == actor_id) | (pl.col("target_id") == actor_id))
        if ability_ids is not None:
            lf = lf.filter(pl.col("ability_id").is_in(ability_ids))
        if t_from is not None:
            lf = lf.filter(pl.col("t_ms") >= t_from)
        if t_to is not None:
            lf = lf.filter(pl.col("t_ms") <= t_to)
        return lf.collect()

    def positions(self, pull_key: str) -> pl.DataFrame:
        """The pull's position table; empty when it has none (no samples, or ingested before positions)."""
        path = self._positions_path(pull_key)
        if not path.exists() or self.positions_hash(pull_key) is None:
            return empty_positions()
        return pl.read_parquet(path)


_ACTOR_SCHEMA = {
    "actor_id": pl.Int32(),
    "name": pl.Utf8(),
    "kind": pl.Utf8(),
    "wcl_type": pl.Utf8(),
    "sub_type": pl.Utf8(),
    "owner_id": pl.Int32(),
    "game_id": pl.Int32(),
}

__all__ = [
    "EVENT_SCHEMA",
    "POSITION_SCHEMA",
    "EventStore",
    "ParquetEventStore",
    "empty_positions",
    "positions_hash",
    "split_pull_key",
]
