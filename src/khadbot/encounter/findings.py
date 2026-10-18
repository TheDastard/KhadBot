"""
encounter/findings.py

SQLite store for per-pull findings, keyed so a change to the data, the authored files, the prompts,
the graph, the model or the depth misses the cache instead of serving stale findings.

Uses stdlib ``sqlite3`` with one short-lived connection per call, so the store is safe to share
between concurrent analyst runs. The table is created on first use.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import orjson

from khadbot.config import EncounterConfig
from khadbot.encounter.models import Depth, PullFindings

# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    cache_key  TEXT PRIMARY KEY,
    pull_key   TEXT NOT NULL,
    json       TEXT NOT NULL,
    flagged    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS findings_pull_key ON findings (pull_key);
"""


def cache_key(
    *,
    pull_key: str,
    events_hash: str,
    positions_hash: str,
    encfile_hash: str | None,
    defensive_cooldowns_hash: str,
    offensive_cooldowns_hash: str,
    friendly_damage_hash: str,
    prompt_version: str,
    graph_version: str,
    model_id: str,
    depth: Depth,
) -> str:
    """First 16 hex of SHA-256 over every input that shapes a pull's findings."""
    payload = {
        "pull_key": pull_key,
        "events_hash": events_hash,
        "positions_hash": positions_hash,
        "encfile_hash": encfile_hash,
        "defensive_cooldowns_hash": defensive_cooldowns_hash,
        "offensive_cooldowns_hash": offensive_cooldowns_hash,
        "friendly_damage_hash": friendly_damage_hash,
        "prompt_version": prompt_version,
        "graph_version": graph_version,
        "model_id": model_id,
        "depth": depth,
    }
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class FindingsStore:
    """Per-pull findings in a SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(_SCHEMA)
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    def get(self, key: str) -> PullFindings | None:
        """The findings stored under ``key``, flagged or not, or None."""
        with self._connect() as conn:
            row = conn.execute("SELECT json, flagged FROM findings WHERE cache_key = ?", (key,)).fetchone()
        return _load(*row) if row else None

    def put(self, pf: PullFindings) -> None:
        """Store ``pf`` under its cache key, replacing (and un-flagging) any earlier row."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO findings (cache_key, pull_key, json, flagged, created_at) VALUES (?, ?, ?, ?, ?)",
                (pf.cache_key, pf.pull_key, pf.model_dump_json(), int(pf.flagged), pf.created_at.isoformat()),
            )

    def for_pull(self, pull_key: str, *, include_flagged: bool = False) -> list[PullFindings]:
        """Stored findings for a pull, newest first. Flagged rows are left out unless asked for."""
        sql = "SELECT json, flagged FROM findings WHERE pull_key = ?"
        if not include_flagged:
            sql += " AND flagged = 0"
        with self._connect() as conn:
            rows = conn.execute(sql, (pull_key,)).fetchall()
        return sorted((_load(*r) for r in rows), key=lambda pf: pf.created_at, reverse=True)

    def flag(self, finding_id: str) -> bool:
        """Flag the stored findings that contain ``finding_id``. Returns False if none do."""
        with self._connect() as conn:
            rows = conn.execute("SELECT cache_key, json FROM findings WHERE json LIKE ?", (f"%{finding_id}%",))
            keys = [
                key
                for key, raw in rows.fetchall()
                if any(f.id == finding_id for f in PullFindings.model_validate_json(raw).findings)
            ]
            conn.executemany("UPDATE findings SET flagged = 1 WHERE cache_key = ?", [(k,) for k in keys])
        return bool(keys)


def _load(raw: str, flagged: int) -> PullFindings:
    return PullFindings.model_validate_json(raw).model_copy(update={"flagged": bool(flagged)})


def now() -> datetime:
    """Timestamp for new rows (UTC, timezone-aware)."""
    return datetime.now(UTC)


def default_findings_store() -> FindingsStore:
    """The findings database under ``KHADBOT_DATA_DIR``."""
    return FindingsStore(Path(EncounterConfig().data_dir) / "khadbot.sqlite")
