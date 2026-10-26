"""
encounter/store.py

Event store: the normalized event and position schemas, and events and positions per pull, behind a
protocol so the implementation (polars + parquet today, possibly DuckDB later) can change without touching
callers.

Keys, under ``events/`` in the blob store (``$KHADBOT_DATA_DIR/events`` in the local store)::

    <report>/pulls.json        # [{"pull": Pull, "events_hash": str, "positions_hash": str | None,
                               #   "input_fingerprint": str | None, "truncated": [dir_name]}]
    <report>/actors.parquet
    <report>/abilities.json    # {ability_id: name} from report masterData
    <report>/<fight>.parquet
    <report>/positions/<fight>.parquet

A record written before positions were stored has no ``positions_hash`` and no position table; it reads
as an empty table until the pull is re-ingested. A record written before fingerprints has no
``input_fingerprint``; ``set_input_fingerprint`` adds one without rewriting the pull.

``pulls.json`` is rewritten by every ``write_pull``, and process jobs for different pulls of one report write in
parallel, so the read-modify-write runs under a per-report lock (``ReportLock``). The default is a lock within this
process; hosted mode passes a Postgres advisory lock, which holds across worker processes (``khadbot.db.locks``).
``set_input_fingerprint`` takes the same lock.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Protocol

import orjson
import polars as pl

from khadbot.encounter.models import Actor, Pull
from khadbot.storage import BlobStore, join_key, resolve_blobs

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


# ``write_pull`` has a ``positions_hash`` parameter that shadows the function above.
_positions_hash = positions_hash


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


# Serializes the writers of one report's ``pulls.json``: called with the report code, held around the update.
ReportLock = Callable[[str], AbstractContextManager[None]]

_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def local_report_lock(code: str) -> Iterator[None]:
    """A per-report lock within this process: enough for the CLI and for one worker's threads."""
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.setdefault(code, threading.Lock())
    with lock:
        yield


class EventStore(Protocol):
    def write_pull(
        self,
        pull: Pull,
        df: pl.DataFrame,
        events_hash: str,
        truncated: list[str] | None = None,
        positions: pl.DataFrame | None = None,
        *,
        input_fingerprint: str | None = None,
        positions_hash: str | None = None,
    ) -> None: ...

    def set_input_fingerprint(self, pull_key: str, fingerprint: str) -> None: ...

    def write_actors(self, code: str, actors: list[Actor]) -> None: ...

    def write_abilities(self, code: str, abilities: dict[int, str]) -> None: ...

    def reports(self) -> list[str]: ...

    def pulls(self, code: str) -> list[Pull]: ...

    def events_hash(self, pull_key: str) -> str | None: ...

    def positions_hash(self, pull_key: str) -> str | None: ...

    def input_fingerprint(self, pull_key: str) -> str | None: ...

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


def _parquet_bytes(df: pl.DataFrame) -> bytes:
    """``df`` as parquet bytes, the same bytes ``df.write_parquet(path)`` writes to a file."""
    buf = io.BytesIO()
    df.write_parquet(buf)
    return buf.getvalue()


class ParquetEventStore:
    """
    EventStore over per-pull parquet objects in a ``BlobStore``, under the ``events/`` prefix by default.

    ``blobs`` may also be a directory laid out like the store itself (``<data_dir>/events``); see
    ``storage.resolve_blobs``.
    """

    def __init__(
        self,
        blobs: BlobStore | str | os.PathLike[str],
        prefix: str | None = None,
        *,
        report_lock: ReportLock | None = None,
    ) -> None:
        self.blobs, self.prefix = resolve_blobs(blobs, prefix, "events")
        self._report_lock = report_lock or local_report_lock

    # --- keys ----------------------------------------------------------------

    def _key(self, code: str, *parts: str) -> str:
        return join_key(self.prefix, code, *parts)

    def _pulls_key(self, code: str) -> str:
        return self._key(code, "pulls.json")

    def _events_key(self, pull_key: str) -> str:
        code, fight = split_pull_key(pull_key)
        return self._key(code, f"{fight}.parquet")

    def _positions_key(self, pull_key: str) -> str:
        code, fight = split_pull_key(pull_key)
        return self._key(code, "positions", f"{fight}.parquet")

    def _records(self, code: str) -> list[dict]:
        data = self.blobs.get(self._pulls_key(code))
        return orjson.loads(data) if data is not None else []

    # --- writes --------------------------------------------------------------

    def write_pull(
        self,
        pull: Pull,
        df: pl.DataFrame,
        events_hash: str,
        truncated: list[str] | None = None,
        positions: pl.DataFrame | None = None,
        *,
        input_fingerprint: str | None = None,
        positions_hash: str | None = None,
    ) -> None:
        """
        Store a pull's events and, when given, its position table. Without ``positions`` the pull has no
        position table: a stale one is removed and the record's ``positions_hash`` is null.

        ``positions_hash`` is the hash of ``positions`` when the caller already has it; otherwise it is computed
        here. ``input_fingerprint`` is recorded with the pull (see ``ingest.fetch.input_fingerprint``).
        """
        self.blobs.put(self._events_key(pull.pull_key), _parquet_bytes(df))
        pos_key = self._positions_key(pull.pull_key)
        pos_hash: str | None = None
        if positions is not None:
            table = positions.select(list(POSITION_SCHEMA))
            self.blobs.put(pos_key, _parquet_bytes(table))
            pos_hash = positions_hash if positions_hash is not None else _positions_hash(table)
        else:
            self.blobs.delete(pos_key)
        record = {
            "pull": pull.model_dump(mode="json"),
            "events_hash": events_hash,
            "positions_hash": pos_hash,
            "input_fingerprint": input_fingerprint,
            "truncated": truncated or [],
        }
        with self._report_lock(pull.report_code):
            records = [r for r in self._records(pull.report_code) if r["pull"]["fight_id"] != pull.fight_id]
            records.append(record)
            records.sort(key=lambda r: r["pull"]["fight_id"])
            self.blobs.put(self._pulls_key(pull.report_code), orjson.dumps(records))

    def set_input_fingerprint(self, pull_key: str, fingerprint: str) -> None:
        """Record ``fingerprint`` on a stored pull without touching its events or positions."""
        code, fight = split_pull_key(pull_key)
        with self._report_lock(code):
            records = self._records(code)
            for rec in records:
                if rec["pull"]["fight_id"] == fight:
                    rec["input_fingerprint"] = fingerprint
                    self.blobs.put(self._pulls_key(code), orjson.dumps(records))
                    return
        raise KeyError(f"pull {pull_key} is not stored")

    def write_actors(self, code: str, actors: list[Actor]) -> None:
        df = pl.DataFrame([a.model_dump() for a in actors], schema=_ACTOR_SCHEMA)
        self.blobs.put(self._key(code, "actors.parquet"), _parquet_bytes(df))

    def write_abilities(self, code: str, abilities: dict[int, str]) -> None:
        data = orjson.dumps(abilities, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SORT_KEYS)
        self.blobs.put(self._key(code, "abilities.json"), data)

    # --- reads ---------------------------------------------------------------

    def reports(self) -> list[str]:
        """Codes of the reports with at least one stored pull, sorted."""
        base = f"{self.prefix}/" if self.prefix else ""
        codes = {
            rest.removesuffix("/pulls.json")
            for key in self.blobs.list(base)
            if (rest := key[len(base) :]).endswith("/pulls.json") and rest.count("/") == 1
        }
        return sorted(code for code in codes if self._records(code))

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

    def input_fingerprint(self, pull_key: str) -> str | None:
        rec = self._record(pull_key)
        return rec.get("input_fingerprint") if rec else None

    def truncated(self, pull_key: str) -> list[str]:
        rec = self._record(pull_key)
        return list(rec["truncated"]) if rec else []

    def actors(self, code: str) -> list[Actor]:
        data = self.blobs.get(self._key(code, "actors.parquet"))
        if data is None:
            return []
        return [Actor(**row) for row in pl.read_parquet(io.BytesIO(data)).to_dicts()]

    def abilities(self, code: str) -> dict[int, str]:
        data = self.blobs.get(self._key(code, "abilities.json"))
        if data is None:
            return {}
        return {int(k): v for k, v in orjson.loads(data).items()}

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
        data = self.blobs.get(self._events_key(pull_key))
        if data is None:
            return empty_events()
        lf = pl.scan_parquet(io.BytesIO(data))
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
        if self.positions_hash(pull_key) is None:
            return empty_positions()
        data = self.blobs.get(self._positions_key(pull_key))
        if data is None:
            return empty_positions()
        return pl.read_parquet(io.BytesIO(data))


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
    "ReportLock",
    "empty_positions",
    "local_report_lock",
    "positions_hash",
    "split_pull_key",
]
