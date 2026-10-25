"""
jobs/status.py

The ``jobs`` table: the user-facing state of every queued job. A row holds the job's state, per-pull progress, the
typed failure, who asked and with whose WCL auth, and the points spent. Each change to a row sends
``NOTIFY job_events`` with the job's ID, so a client can follow a job without polling.

The state rules live here once, as plain functions over ``JobRecord`` (``_begin``, ``_progress``, ``_finish``,
``_settle``, ``_cancel``). A ``JobBackend`` supplies the transaction they run in: ``PostgresJobBackend`` in hosted
mode, ``MemoryJobBackend`` for unit tests and tools. ``JobStore`` is the async facade the queue and the API call.

An ingest finishes with its process jobs: when its own work is done it waits (``awaiting_children``) until every
``ProcessPull`` it enqueued has finished, then becomes ``done``, or ``failed`` if any pull failed, or ``cancelled``.
Every transaction first takes one advisory lock per report, so a report's rows change one writer at a time.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, LiteralString, Protocol

from pydantic import BaseModel, ConfigDict, Field

from khadbot.jobs.failures import ErrorCode
from khadbot.jobs.specs import IngestReport

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

NOTIFY_CHANNEL = "job_events"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_BUDGET = "waiting_budget"  # in the WCL budget manager's line: not enough points right now
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATES = frozenset({JobState.QUEUED, JobState.RUNNING, JobState.WAITING_BUDGET})


class JobKind(StrEnum):
    INGEST = "ingest"
    PROCESS = "process"
    REVIEW = "review"


class PullProgress(BaseModel):
    """Where one pull is: the stage it last reported and whether it is running, done, failed or cancelled."""

    model_config = ConfigDict(frozen=True)

    stage: str
    fraction: float
    state: Literal["running", "done", "failed", "cancelled"] = "running"
    error: str | None = None


class JobProgress(BaseModel):
    """A job's progress document (the ``progress`` column). Job-level events fill ``stage``; pull events ``pulls``."""

    model_config = ConfigDict(frozen=True)

    stage: str | None = None
    fraction: float = 0.0
    pulls: dict[str, PullProgress] = Field(default_factory=dict)


class JobRecord(BaseModel):
    """One row of the ``jobs`` table."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: JobKind
    report_code: str
    pull_key: str | None = None
    parent_id: str | None = None
    state: JobState = JobState.QUEUED
    progress: JobProgress = Field(default_factory=JobProgress)
    requested_by: str | None = None
    auth_kind: Literal["app", "user"] = "app"
    points_spent: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempts: int = 0
    cancel_requested: bool = False
    awaiting_children: bool = False
    queue_job_id: int | None = None
    spec: dict[str, Any]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES


class NewJob(BaseModel):
    """What a caller names to create a job; the store fills the rest."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: JobKind
    report_code: str
    pull_key: str | None = None
    parent_id: str | None = None
    requested_by: str | None = None
    auth_kind: Literal["app", "user"] = "app"
    spec: dict[str, Any]


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class JobTx(Protocol):
    """Reads and writes inside one transaction. ``insert`` and ``save`` also announce the row on ``job_events``."""

    def load(self, job_id: str) -> JobRecord | None: ...

    def insert(self, rec: JobRecord) -> None: ...

    def save(self, rec: JobRecord) -> None: ...

    def active_ingests(self, report_code: str) -> list[JobRecord]: ...

    def active_process(self, report_code: str) -> list[JobRecord]: ...

    def children(self, parent_id: str) -> list[JobRecord]: ...


class JobBackend(Protocol):
    def transaction(self, *, job_id: str | None = None, report: str | None = None) -> AbstractContextManager[JobTx]:
        """A transaction holding the report's lock; ``job_id`` names the report when the caller only has the job."""
        ...

    def cancel_requested(self, job_id: str) -> bool: ...

    def listen(self) -> AbstractAsyncContextManager[AsyncIterator[str]]: ...


# ---------------------------------------------------------------------------
# State rules
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def covers(existing: IngestReport, new: IngestReport) -> bool:
    """Whether an ingest that is already queued or running does the work ``new`` asks for."""
    if existing.refresh_meta != new.refresh_meta:
        return False
    if existing.offline and not new.offline:  # an offline run never fetches what an online one would
        return False
    if existing.fights is None:
        return True
    return new.fights is not None and set(new.fights) <= set(existing.fights)


def _new_record(new: NewJob) -> JobRecord:
    now = _now()
    return JobRecord(**new.model_dump(), created_at=now, updated_at=now)


def _with_pull(progress: JobProgress, pull: str, entry: PullProgress) -> JobProgress:
    return progress.model_copy(update={"pulls": {**progress.pulls, pull: entry}})


def _claim_ingest(tx: JobTx, new: NewJob) -> tuple[JobRecord, bool]:
    wanted = IngestReport.model_validate(new.spec)
    for existing in tx.active_ingests(new.report_code):
        if covers(IngestReport.model_validate(existing.spec), wanted):
            return existing, False
    rec = _new_record(new)
    tx.insert(rec)
    return rec, True


def _begin(tx: JobTx, job_id: str) -> JobRecord | None:
    rec = tx.load(job_id)
    if rec is None or rec.state is not JobState.QUEUED:
        return None
    if rec.cancel_requested:
        _finish(tx, job_id, JobState.CANCELLED)
        return None
    now = _now()
    rec = rec.model_copy(
        update={
            "state": JobState.RUNNING,
            "attempts": rec.attempts + 1,
            "started_at": rec.started_at or now,
            "error_code": None,
            "error_message": None,
            "updated_at": now,
        }
    )
    tx.save(rec)
    return rec


def _progress(tx: JobTx, job_id: str, pull: str | None, stage: str, fraction: float) -> None:
    rec = tx.load(job_id)
    if rec is None or not rec.active:
        return
    if pull is None:
        doc = rec.progress.model_copy(update={"stage": stage, "fraction": fraction})
    else:
        doc = _with_pull(rec.progress, pull, PullProgress(stage=stage, fraction=fraction))
    tx.save(rec.model_copy(update={"progress": doc, "updated_at": _now()}))
    if pull is not None and rec.parent_id:
        parent = tx.load(rec.parent_id)
        if parent is not None and parent.active:
            current = parent.progress.pulls.get(pull)
            if current is None or current.state == "running":
                entry = PullProgress(stage=stage, fraction=fraction)
                tx.save(
                    parent.model_copy(
                        update={"progress": _with_pull(parent.progress, pull, entry), "updated_at": _now()}
                    )
                )


def _finish(
    tx: JobTx,
    job_id: str,
    state: JobState,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    points_spent: float | None = None,
    failed_pulls: dict[str, str] | None = None,
    awaiting_children: bool = False,
) -> JobRecord | None:
    rec = tx.load(job_id)
    if rec is None or not rec.active:
        return rec
    now = _now()
    doc = rec.progress
    for pull, message in (failed_pulls or {}).items():
        doc = _with_pull(doc, pull, PullProgress(stage="ingest", fraction=0.0, state="failed", error=message))
    update: dict[str, Any] = {"progress": doc, "points_spent": points_spent, "updated_at": now}
    if awaiting_children:
        update["awaiting_children"] = True
    else:
        update.update(
            state=state, finished_at=now, awaiting_children=False, error_code=error_code, error_message=error_message
        )
        if state is JobState.DONE:
            update["progress"] = doc.model_copy(update={"fraction": 1.0})
    rec = rec.model_copy(update=update)
    tx.save(rec)
    if awaiting_children:
        return _settle(tx, rec)
    if rec.parent_id and rec.pull_key:
        parent = tx.load(rec.parent_id)
        if parent is not None:
            pull_state = {JobState.DONE: "done", JobState.CANCELLED: "cancelled"}.get(state, "failed")
            own = rec.progress.pulls.get(rec.pull_key)
            entry = PullProgress(
                stage=own.stage if own else state.value,
                fraction=1.0 if state is JobState.DONE else (own.fraction if own else 0.0),
                state=pull_state,  # type: ignore[arg-type]
                error=error_message if state is JobState.FAILED else None,
            )
            parent = parent.model_copy(
                update={"progress": _with_pull(parent.progress, rec.pull_key, entry), "updated_at": now}
            )
            tx.save(parent)
            _settle(tx, parent)
    return rec


def _settle(tx: JobTx, rec: JobRecord) -> JobRecord:
    """Finish an ingest that is waiting for its process jobs, once none of them is active."""
    rec = tx.load(rec.id) or rec
    if rec.state is not JobState.RUNNING or not rec.awaiting_children:
        return rec
    if any(child.active for child in tx.children(rec.id)):
        return rec
    pulls = rec.progress.pulls
    failed = sum(1 for p in pulls.values() if p.state == "failed")
    update: dict[str, Any]
    if rec.cancel_requested:
        update = {"state": JobState.CANCELLED}
    elif failed:
        update = {
            "state": JobState.FAILED,
            "error_code": ErrorCode.PULLS_FAILED.value,
            "error_message": f"{failed} of {len(pulls)} pulls failed.",
        }
    else:
        update = {"state": JobState.DONE, "progress": rec.progress.model_copy(update={"fraction": 1.0})}
    now = _now()
    rec = rec.model_copy(update={**update, "awaiting_children": False, "finished_at": now, "updated_at": now})
    tx.save(rec)
    return rec


def _requeue(tx: JobTx, job_id: str, error_code: str, error_message: str) -> None:
    rec = tx.load(job_id)
    if rec is None or rec.state is not JobState.RUNNING:
        return
    tx.save(
        rec.model_copy(
            update={
                "state": JobState.QUEUED,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": _now(),
            }
        )
    )


def _set_budget_wait(tx: JobTx, job_id: str, waiting: bool) -> bool:
    """
    Move a job between ``queued`` and ``waiting_budget`` (the WCL budget manager's line). False when the job is not
    in the state it should leave: it was cancelled, finished or started meanwhile.
    """
    rec = tx.load(job_id)
    source, target = (
        (JobState.QUEUED, JobState.WAITING_BUDGET) if waiting else (JobState.WAITING_BUDGET, JobState.QUEUED)
    )
    if rec is None or rec.state is not target and rec.state is not source:
        return False
    if rec.state is source:
        tx.save(rec.model_copy(update={"state": target, "updated_at": _now()}))
    return True


def _cancel(tx: JobTx, job_id: str) -> tuple[JobRecord | None, list[JobRecord]]:
    """
    Cancel a job and the process jobs under it. Queued ones become ``cancelled`` at once (returned, so the queue can
    drop them); running ones are flagged and stop at their next page boundary.
    """
    root = tx.load(job_id)
    if root is None:
        return None, []
    dropped: list[JobRecord] = []
    for listed in [*tx.children(job_id), root]:
        child = tx.load(listed.id)  # reload: finishing a child changes the root's progress
        if child is None or not child.active:
            continue
        if child.state in (JobState.QUEUED, JobState.WAITING_BUDGET):
            done = _finish(tx, child.id, JobState.CANCELLED)
            if done is not None:
                dropped.append(done)
        else:
            tx.save(child.model_copy(update={"cancel_requested": True, "updated_at": _now()}))
    final = tx.load(job_id)
    if final is not None and final.awaiting_children:
        final = _settle(tx, final)
    return final, dropped


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class JobStore:
    """The async interface to the ``jobs`` table. Each call is one transaction on a worker thread."""

    def __init__(self, backend: JobBackend) -> None:
        self._backend = backend

    async def _run[T](self, fn: Callable[[JobTx], T], *, job_id: str | None = None, report: str | None = None) -> T:
        def work() -> T:
            with self._backend.transaction(job_id=job_id, report=report) as tx:
                return fn(tx)

        return await asyncio.to_thread(work)

    async def create(self, new: NewJob) -> JobRecord:
        def work(tx: JobTx) -> JobRecord:
            rec = _new_record(new)
            tx.insert(rec)
            return rec

        return await self._run(work, report=new.report_code)

    async def claim_ingest(self, new: NewJob) -> tuple[JobRecord, bool]:
        """Create the ingest, or return the queued or running one that already covers it as ``(job, False)``."""
        return await self._run(lambda tx: _claim_ingest(tx, new), report=new.report_code)

    async def active_ingests(self, report: str) -> list[JobRecord]:
        return await self._run(lambda tx: tx.active_ingests(report), report=report)

    async def active_process(self, report: str) -> list[JobRecord]:
        """The queued or running process jobs of one report's pulls."""
        return await self._run(lambda tx: tx.active_process(report), report=report)

    async def get(self, job_id: str) -> JobRecord | None:
        return await self._run(lambda tx: tx.load(job_id), job_id=job_id)

    async def children(self, parent_id: str) -> list[JobRecord]:
        return await self._run(lambda tx: tx.children(parent_id), job_id=parent_id)

    async def set_queue_job_id(self, job_id: str, queue_job_id: int) -> None:
        def work(tx: JobTx) -> None:
            rec = tx.load(job_id)
            if rec is not None:
                tx.save(rec.model_copy(update={"queue_job_id": queue_job_id}))

        await self._run(work, job_id=job_id)

    async def begin(self, job_id: str) -> JobRecord | None:
        """Mark a queued job running. None when it is gone, already finished or cancelled: do not run it."""
        return await self._run(lambda tx: _begin(tx, job_id), job_id=job_id)

    async def report_progress(self, job_id: str, pull: str | None, stage: str, fraction: float) -> None:
        await self._run(lambda tx: _progress(tx, job_id, pull, stage, fraction), job_id=job_id)

    async def finish(
        self,
        job_id: str,
        state: JobState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        points_spent: float | None = None,
        failed_pulls: dict[str, str] | None = None,
        awaiting_children: bool = False,
    ) -> JobRecord | None:
        """End a job. With ``awaiting_children`` it stays running until its process jobs finish (``state`` unused)."""
        return await self._run(
            lambda tx: _finish(
                tx,
                job_id,
                state,
                error_code=error_code,
                error_message=error_message,
                points_spent=points_spent,
                failed_pulls=failed_pulls,
                awaiting_children=awaiting_children,
            ),
            job_id=job_id,
        )

    async def requeue(self, job_id: str, error_code: str, error_message: str) -> None:
        """Put a running job back to ``queued`` after a failure the queue will retry."""
        await self._run(lambda tx: _requeue(tx, job_id, error_code, error_message), job_id=job_id)

    async def wait_for_budget(self, job_id: str) -> bool:
        """Put a queued job in the budget's line (``waiting_budget``). False if it is no longer queued."""
        return await self._run(lambda tx: _set_budget_wait(tx, job_id, True), job_id=job_id)

    async def resume_from_budget(self, job_id: str) -> bool:
        """Put a job the budget admitted back to ``queued``, ready to start. False if it is no longer waiting."""
        return await self._run(lambda tx: _set_budget_wait(tx, job_id, False), job_id=job_id)

    async def request_cancel(self, job_id: str) -> tuple[JobRecord | None, list[JobRecord]]:
        """Cancel a job; returns it and the jobs that were still queued and are now cancelled."""
        return await self._run(lambda tx: _cancel(tx, job_id), job_id=job_id)

    async def cancel_requested(self, job_id: str) -> bool:
        return await asyncio.to_thread(self._backend.cancel_requested, job_id)

    def listen(self) -> AbstractAsyncContextManager[AsyncIterator[str]]:
        """
        Subscribe to ``job_events``: ``async with store.listen() as events`` gives an iterator of job IDs, one per
        change, in the order they were committed. The subscription starts when the block is entered, so changes made
        after that are not missed.
        """
        return self._backend.listen()


# ---------------------------------------------------------------------------
# In-memory backend (unit tests and tools)
# ---------------------------------------------------------------------------


class MemoryJobBackend:
    """The ``jobs`` table in a dict, with the same rules and announcements as Postgres."""

    def __init__(self) -> None:
        self.rows: dict[str, JobRecord] = {}
        self._lock = threading.RLock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]] = []

    @contextmanager
    def transaction(self, *, job_id: str | None = None, report: str | None = None) -> Iterator[JobTx]:
        with self._lock:
            yield _MemoryTx(self)

    def cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            rec = self.rows.get(job_id)
            return rec is not None and rec.cancel_requested

    @asynccontextmanager
    async def listen(self) -> AsyncIterator[AsyncIterator[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        entry = (asyncio.get_running_loop(), queue)
        self._subscribers.append(entry)

        async def events() -> AsyncIterator[str]:
            while True:
                yield await queue.get()

        try:
            yield events()
        finally:
            self._subscribers.remove(entry)

    def announce(self, job_id: str) -> None:
        for loop, queue in list(self._subscribers):
            loop.call_soon_threadsafe(queue.put_nowait, job_id)


class _MemoryTx:
    def __init__(self, backend: MemoryJobBackend) -> None:
        self._backend = backend

    def load(self, job_id: str) -> JobRecord | None:
        return self._backend.rows.get(job_id)

    def insert(self, rec: JobRecord) -> None:
        self._backend.rows[rec.id] = rec
        self._backend.announce(rec.id)

    def save(self, rec: JobRecord) -> None:
        self._backend.rows[rec.id] = rec
        self._backend.announce(rec.id)

    def active_ingests(self, report_code: str) -> list[JobRecord]:
        rows = [
            r
            for r in self._backend.rows.values()
            if r.kind is JobKind.INGEST and r.report_code == report_code and r.active
        ]
        return sorted(rows, key=lambda r: r.created_at)

    def active_process(self, report_code: str) -> list[JobRecord]:
        rows = [
            r
            for r in self._backend.rows.values()
            if r.kind is JobKind.PROCESS and r.report_code == report_code and r.active
        ]
        return sorted(rows, key=lambda r: r.created_at)

    def children(self, parent_id: str) -> list[JobRecord]:
        rows = [r for r in self._backend.rows.values() if r.parent_id == parent_id]
        return sorted(rows, key=lambda r: r.created_at)


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------

_COLUMNS: LiteralString = (
    "id, kind, report_code, pull_key, parent_id, state, progress, requested_by, auth_kind, points_spent, "
    "error_code, error_message, attempts, cancel_requested, awaiting_children, queue_job_id, spec, "
    "created_at, started_at, finished_at, updated_at"
)
_SELECT_ONE: LiteralString = "SELECT " + _COLUMNS + " FROM jobs WHERE id = %s"
_SELECT_ACTIVE_INGESTS: LiteralString = (
    "SELECT " + _COLUMNS + " FROM jobs WHERE kind = 'ingest' AND report_code = %s "
    "AND state IN ('queued', 'running', 'waiting_budget') ORDER BY created_at"
)
_SELECT_ACTIVE_PROCESS: LiteralString = (
    "SELECT " + _COLUMNS + " FROM jobs WHERE kind = 'process' AND report_code = %s "
    "AND state IN ('queued', 'running', 'waiting_budget') ORDER BY created_at"
)
_SELECT_CHILDREN: LiteralString = "SELECT " + _COLUMNS + " FROM jobs WHERE parent_id = %s ORDER BY created_at"
_INSERT: LiteralString = (
    "INSERT INTO jobs (" + _COLUMNS + ") VALUES "
    "(%(id)s, %(kind)s, %(report_code)s, %(pull_key)s, %(parent_id)s, %(state)s, %(progress)s, %(requested_by)s, "
    "%(auth_kind)s, %(points_spent)s, %(error_code)s, %(error_message)s, %(attempts)s, %(cancel_requested)s, "
    "%(awaiting_children)s, %(queue_job_id)s, %(spec)s, %(created_at)s, %(started_at)s, %(finished_at)s, "
    "%(updated_at)s)"
)
_UPDATE: LiteralString = (
    "UPDATE jobs SET state = %(state)s, progress = %(progress)s, points_spent = %(points_spent)s, "
    "error_code = %(error_code)s, error_message = %(error_message)s, attempts = %(attempts)s, "
    "cancel_requested = %(cancel_requested)s, awaiting_children = %(awaiting_children)s, "
    "queue_job_id = %(queue_job_id)s, started_at = %(started_at)s, finished_at = %(finished_at)s, "
    "updated_at = %(updated_at)s WHERE id = %(id)s"
)
_REPORT_OF: LiteralString = "SELECT report_code FROM jobs WHERE id = %s"
_LOCK_REPORT: LiteralString = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
_NOTIFY: LiteralString = "SELECT pg_notify('job_events', %s)"
_CANCEL_REQUESTED: LiteralString = "SELECT cancel_requested FROM jobs WHERE id = %s"


def _params(rec: JobRecord) -> dict[str, Any]:
    from psycopg.types.json import Jsonb  # hosted libraries load only when Postgres is used

    return {
        **rec.model_dump(exclude={"progress", "spec", "kind", "state"}),
        "kind": rec.kind.value,
        "state": rec.state.value,
        "progress": Jsonb(rec.progress.model_dump(mode="json")),
        "spec": Jsonb(rec.spec),
    }


def _record(row: dict[str, Any]) -> JobRecord:
    return JobRecord.model_validate(row)


class PostgresJobBackend:
    """The ``jobs`` table in Postgres (migration 0004), over the process-wide sync pool."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self, *, job_id: str | None = None, report: str | None = None) -> Iterator[JobTx]:
        # The pool commits when the block ends and rolls back if it raises.
        with self._pool.connection() as conn:
            if report is None and job_id is not None:
                found = conn.execute(_REPORT_OF, (job_id,)).fetchone()
                report = found[0] if found else None
            if report is not None:
                conn.execute(_LOCK_REPORT, (f"jobs:{report}",))
            yield _PostgresTx(conn)

    def cancel_requested(self, job_id: str) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(_CANCEL_REQUESTED, (job_id,)).fetchone()
        return bool(row and row[0])

    def listen(self) -> AbstractAsyncContextManager[AsyncIterator[str]]:
        return _pg_listen(str(self._pool.conninfo), dict(getattr(self._pool, "kwargs", None) or {}))


class _PostgresTx:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def _rows(self, query: LiteralString, params: tuple[Any, ...]) -> list[JobRecord]:
        from psycopg.rows import dict_row

        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [_record(row) for row in cur.fetchall()]

    def load(self, job_id: str) -> JobRecord | None:
        rows = self._rows(_SELECT_ONE, (job_id,))
        return rows[0] if rows else None

    def insert(self, rec: JobRecord) -> None:
        self._conn.execute(_INSERT, _params(rec))
        self._conn.execute(_NOTIFY, (rec.id,))

    def save(self, rec: JobRecord) -> None:
        self._conn.execute(_UPDATE, _params(rec))
        self._conn.execute(_NOTIFY, (rec.id,))

    def active_ingests(self, report_code: str) -> list[JobRecord]:
        return self._rows(_SELECT_ACTIVE_INGESTS, (report_code,))

    def active_process(self, report_code: str) -> list[JobRecord]:
        return self._rows(_SELECT_ACTIVE_PROCESS, (report_code,))

    def children(self, parent_id: str) -> list[JobRecord]:
        return self._rows(_SELECT_CHILDREN, (parent_id,))


@asynccontextmanager
async def _pg_listen(conninfo: str, kwargs: dict[str, Any]) -> AsyncIterator[AsyncIterator[str]]:
    import psycopg

    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True, **kwargs) as conn:
        await conn.execute("LISTEN job_events")
        # notifies() holds the connection's lock until the generator is closed, and leaving the connection's block
        # needs that lock to commit, so both generators are closed here before the connection is.
        notices = conn.notifies()

        async def events() -> AsyncGenerator[str, None]:
            async for notice in notices:
                yield notice.payload

        stream = events()
        try:
            yield stream
        finally:
            await stream.aclose()
            await notices.aclose()


__all__ = [
    "ACTIVE_STATES",
    "NOTIFY_CHANNEL",
    "JobBackend",
    "JobKind",
    "JobProgress",
    "JobRecord",
    "JobState",
    "JobStore",
    "JobTx",
    "MemoryJobBackend",
    "NewJob",
    "PostgresJobBackend",
    "PullProgress",
    "covers",
]
