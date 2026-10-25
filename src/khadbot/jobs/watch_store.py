"""
jobs/watch_store.py

The ``watches`` table (migration 0007): one row per live report watch, with its schedule, the boss fights it has
already handed to ingest and why it stopped. The state rules live in ``jobs/watch.py``; this module only keeps rows.

A ``WatchBackend`` supplies the transaction a change runs in, holding one advisory lock per report so two requests
for the same report never both start a watch: ``PostgresWatchBackend`` in hosted mode, ``MemoryWatchBackend`` for
unit tests. ``WatchStore`` is the async facade the queue and the API call. ``update`` applies a change only to an
active watch, so a poll that finishes after the user stopped the watch cannot bring it back.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, LiteralString, Protocol

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WatchState(StrEnum):
    ACTIVE = "active"
    STOPPED = "stopped"


class StopReason(StrEnum):
    USER = "user"  # the user stopped it
    QUIET = "quiet"  # no new boss fight for the quiet period
    MAX_LENGTH = "max_length"  # it ran for the maximum length
    SIGN_IN = "sign_in"  # the user's WCL sign-in is gone or was rejected
    REPORT_UNAVAILABLE = "report_unavailable"  # WCL no longer shows the user the report
    ERRORS = "errors"  # too many polls in a row failed


class WatchRecord(BaseModel):
    """One row of the ``watches`` table."""

    model_config = ConfigDict(frozen=True)

    id: str
    report_code: str
    user_id: str
    state: WatchState = WatchState.ACTIVE
    stop_reason: StopReason | None = None
    started_at: datetime
    expires_at: datetime
    quiet_since: datetime
    last_polled_at: datetime | None = None
    next_poll_at: datetime | None = None
    polls: int = 0
    polls_skipped: int = 0
    failures: int = 0
    fights: dict[int, int] = Field(default_factory=dict)  # boss fight ID -> end_ms when handed to ingest
    ingests: int = 0
    last_job_id: str | None = None
    points_spent: float = 0.0
    error_message: str | None = None
    queue_job_id: int | None = None
    updated_at: datetime
    stopped_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.state is WatchState.ACTIVE


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class WatchTx(Protocol):
    def load(self, watch_id: str) -> WatchRecord | None: ...

    def insert(self, rec: WatchRecord) -> None: ...

    def save(self, rec: WatchRecord) -> None: ...

    def active_for(self, report_code: str) -> WatchRecord | None: ...

    def latest_for(self, report_code: str, user_id: str) -> WatchRecord | None: ...


class WatchBackend(Protocol):
    def transaction(self, *, report: str | None = None, watch_id: str | None = None) -> AbstractContextManager[WatchTx]:
        """A transaction holding the report's lock; ``watch_id`` names the report when the caller only has the watch."""
        ...


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class WatchStore:
    """The async interface to the ``watches`` table. Each call is one transaction on a worker thread."""

    def __init__(self, backend: WatchBackend) -> None:
        self._backend = backend

    async def _run[T](self, fn: Callable[[WatchTx], T], *, report: str | None = None, watch_id: str | None = None) -> T:
        def work() -> T:
            with self._backend.transaction(report=report, watch_id=watch_id) as tx:
                return fn(tx)

        return await asyncio.to_thread(work)

    async def start(self, rec: WatchRecord) -> tuple[WatchRecord, bool]:
        """Insert ``rec``, or return the report's active watch as ``(watch, False)``."""

        def work(tx: WatchTx) -> tuple[WatchRecord, bool]:
            existing = tx.active_for(rec.report_code)
            if existing is not None:
                return existing, False
            tx.insert(rec)
            return rec, True

        return await self._run(work, report=rec.report_code)

    async def get(self, watch_id: str) -> WatchRecord | None:
        return await self._run(lambda tx: tx.load(watch_id), watch_id=watch_id)

    async def active_for(self, report_code: str) -> WatchRecord | None:
        return await self._run(lambda tx: tx.active_for(report_code), report=report_code)

    async def latest_for(self, report_code: str, user_id: str) -> WatchRecord | None:
        """The user's most recent watch of the report, active or not."""
        return await self._run(lambda tx: tx.latest_for(report_code, user_id), report=report_code)

    async def update(
        self, watch_id: str, change: Callable[[WatchRecord], WatchRecord]
    ) -> tuple[WatchRecord | None, bool]:
        """
        Apply ``change`` to an active watch. Returns the row as it now stands and whether the change was applied;
        a watch that is gone or already stopped is returned unchanged (False).
        """

        def work(tx: WatchTx) -> tuple[WatchRecord | None, bool]:
            rec = tx.load(watch_id)
            if rec is None or not rec.active:
                return rec, False
            new = change(rec).model_copy(update={"updated_at": datetime.now(UTC)})
            tx.save(new)
            return new, True

        return await self._run(work, watch_id=watch_id)


# ---------------------------------------------------------------------------
# In-memory backend (unit tests)
# ---------------------------------------------------------------------------


class MemoryWatchBackend:
    """The ``watches`` table in a dict, with the same rules as Postgres."""

    def __init__(self) -> None:
        self.rows: dict[str, WatchRecord] = {}
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self, *, report: str | None = None, watch_id: str | None = None) -> Iterator[WatchTx]:
        with self._lock:
            yield _MemoryTx(self)


class _MemoryTx:
    def __init__(self, backend: MemoryWatchBackend) -> None:
        self._rows = backend.rows

    def load(self, watch_id: str) -> WatchRecord | None:
        return self._rows.get(watch_id)

    def insert(self, rec: WatchRecord) -> None:
        self._rows[rec.id] = rec

    def save(self, rec: WatchRecord) -> None:
        self._rows[rec.id] = rec

    def active_for(self, report_code: str) -> WatchRecord | None:
        return next((r for r in self._rows.values() if r.report_code == report_code and r.active), None)

    def latest_for(self, report_code: str, user_id: str) -> WatchRecord | None:
        rows = [r for r in self._rows.values() if r.report_code == report_code and r.user_id == user_id]
        return max(rows, key=lambda r: r.started_at, default=None)


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------

_COLUMNS: LiteralString = (
    "id, report_code, user_id, state, stop_reason, started_at, expires_at, quiet_since, last_polled_at, "
    "next_poll_at, polls, polls_skipped, failures, fights, ingests, last_job_id, points_spent, error_message, "
    "queue_job_id, updated_at, stopped_at"
)
_SELECT_ONE: LiteralString = "SELECT " + _COLUMNS + " FROM watches WHERE id = %s"
_SELECT_ACTIVE: LiteralString = "SELECT " + _COLUMNS + " FROM watches WHERE report_code = %s AND state = 'active'"
_SELECT_LATEST: LiteralString = (
    "SELECT " + _COLUMNS + " FROM watches WHERE report_code = %s AND user_id = %s ORDER BY started_at DESC LIMIT 1"
)
_INSERT: LiteralString = (
    "INSERT INTO watches (" + _COLUMNS + ") VALUES "
    "(%(id)s, %(report_code)s, %(user_id)s, %(state)s, %(stop_reason)s, %(started_at)s, %(expires_at)s, "
    "%(quiet_since)s, %(last_polled_at)s, %(next_poll_at)s, %(polls)s, %(polls_skipped)s, %(failures)s, %(fights)s, "
    "%(ingests)s, %(last_job_id)s, %(points_spent)s, %(error_message)s, %(queue_job_id)s, %(updated_at)s, "
    "%(stopped_at)s)"
)
_UPDATE: LiteralString = (
    "UPDATE watches SET state = %(state)s, stop_reason = %(stop_reason)s, expires_at = %(expires_at)s, "
    "quiet_since = %(quiet_since)s, last_polled_at = %(last_polled_at)s, next_poll_at = %(next_poll_at)s, "
    "polls = %(polls)s, polls_skipped = %(polls_skipped)s, failures = %(failures)s, fights = %(fights)s, "
    "ingests = %(ingests)s, last_job_id = %(last_job_id)s, points_spent = %(points_spent)s, "
    "error_message = %(error_message)s, queue_job_id = %(queue_job_id)s, updated_at = %(updated_at)s, "
    "stopped_at = %(stopped_at)s WHERE id = %(id)s"
)
_REPORT_OF: LiteralString = "SELECT report_code FROM watches WHERE id = %s"
_LOCK_REPORT: LiteralString = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


def _params(rec: WatchRecord) -> dict[str, Any]:
    from psycopg.types.json import Jsonb  # hosted libraries load only when Postgres is used

    return {
        **rec.model_dump(exclude={"fights", "state", "stop_reason"}),
        "state": rec.state.value,
        "stop_reason": rec.stop_reason.value if rec.stop_reason else None,
        "fights": Jsonb({str(k): v for k, v in rec.fights.items()}),
    }


class PostgresWatchBackend:
    """The ``watches`` table in Postgres (migration 0007), over the process-wide sync pool."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self, *, report: str | None = None, watch_id: str | None = None) -> Iterator[WatchTx]:
        with self._pool.connection() as conn:  # commits when the block ends, rolls back if it raises
            if report is None and watch_id is not None:
                found = conn.execute(_REPORT_OF, (watch_id,)).fetchone()
                report = found[0] if found else None
            if report is not None:
                conn.execute(_LOCK_REPORT, (f"watches:{report}",))
            yield _PostgresTx(conn)


class _PostgresTx:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def _rows(self, query: LiteralString, params: tuple[Any, ...]) -> list[WatchRecord]:
        from psycopg.rows import dict_row

        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [WatchRecord.model_validate(row) for row in cur.fetchall()]

    def load(self, watch_id: str) -> WatchRecord | None:
        rows = self._rows(_SELECT_ONE, (watch_id,))
        return rows[0] if rows else None

    def insert(self, rec: WatchRecord) -> None:
        self._conn.execute(_INSERT, _params(rec))

    def save(self, rec: WatchRecord) -> None:
        self._conn.execute(_UPDATE, _params(rec))

    def active_for(self, report_code: str) -> WatchRecord | None:
        rows = self._rows(_SELECT_ACTIVE, (report_code,))
        return rows[0] if rows else None

    def latest_for(self, report_code: str, user_id: str) -> WatchRecord | None:
        rows = self._rows(_SELECT_LATEST, (report_code, user_id))
        return rows[0] if rows else None


__all__ = [
    "MemoryWatchBackend",
    "PostgresWatchBackend",
    "StopReason",
    "WatchBackend",
    "WatchRecord",
    "WatchState",
    "WatchStore",
    "WatchTx",
]
