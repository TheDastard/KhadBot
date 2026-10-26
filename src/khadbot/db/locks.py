"""
db/locks.py

``PostgresReportLock``: the event store's per-report lock (``encounter.store.ReportLock``) as a Postgres advisory
lock, so process jobs for different pulls of one report, in different worker processes, update the report's
``pulls.json`` one at a time.

Hosted only: it needs ``psycopg`` and ``psycopg_pool`` (the ``hosted`` group).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import LiteralString

from psycopg import Connection
from psycopg_pool import ConnectionPool

# Advisory-lock namespace for event store writes (the first key of the two-key form); the second is hashtext(code).
EVENTS_LOCK_NAMESPACE = 0x4B424556
_LOCK: LiteralString = "SELECT pg_advisory_lock(%s::integer, hashtext(%s::text))"
_UNLOCK: LiteralString = "SELECT pg_advisory_unlock(%s::integer, hashtext(%s::text))"

# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


class PostgresReportLock:
    """
    A session-level advisory lock per report on its own pooled connection, held while the event store rewrites the
    report's ``pulls.json``. It is called from a worker thread (the process job writes off the event loop), so it is
    synchronous. If the unlock can't be confirmed, the connection is closed instead of reused, which ends its session
    and with it the lock.

    Args:
        pool: Builds the pool on first use (default ``db.get_pool``), so building the event store opens nothing.
    """

    def __init__(self, pool: Callable[[], ConnectionPool] | None = None) -> None:
        self._pool_factory = pool

    def _pool(self) -> ConnectionPool:
        if self._pool_factory is not None:
            return self._pool_factory()
        from khadbot.db.connect import get_pool

        return get_pool()

    @contextmanager
    def __call__(self, code: str) -> Iterator[None]:
        pool = self._pool()
        conn = pool.getconn()
        released = False
        try:
            _run(conn, _LOCK, code)
            try:
                yield
            finally:
                _run(conn, _UNLOCK, code)
                released = True
        finally:
            if not released:
                conn.close()
            pool.putconn(conn)


def _run(conn: Connection, sql: LiteralString, code: str) -> None:
    conn.execute(sql, (EVENTS_LOCK_NAMESPACE, code))
    conn.commit()  # leave the connection idle; the advisory lock is held by the session, not the transaction


__all__ = ["EVENTS_LOCK_NAMESPACE", "PostgresReportLock"]
