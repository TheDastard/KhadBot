"""
db/index.py

``PostgresReadModelIndex``: what retention and backfill ask of the read-model tables. Which pulls exist for an
encounter (the backfill's candidates) and which records blobs the ``pulls`` rows still point at (the cleanup job keeps
those). Both are plain reads; the queries are in ``queries``.
"""

from __future__ import annotations

from typing import Any

from khadbot.db import queries
from khadbot.db.connect import get_pool


class PostgresReadModelIndex:
    """Reads over the read-model tables through the pool (taken on first use, so building one opens no connection)."""

    def __init__(self, pool: Any | None = None) -> None:
        self._pool = pool

    def _conn(self) -> Any:
        return (self._pool or get_pool()).connection()

    def pull_keys(self, encounter_id: int, difficulty: int | None = None, since_ms: int | None = None) -> list[str]:
        with self._conn() as conn:
            return queries.backfill_pull_keys(conn, encounter_id, difficulty, since_ms)

    def records_keys(self) -> set[str]:
        with self._conn() as conn:
            return queries.referenced_records_keys(conn)


__all__ = ["PostgresReadModelIndex"]
