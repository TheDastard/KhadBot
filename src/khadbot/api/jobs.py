"""
api/jobs.py

Hosted mode's ingest endpoints, over the Postgres job queue (``jobs.queue.JobQueue``). This router replaces the
single-process, in-memory job registry the first dashboard plan sketched: a job is a ``jobs`` row, so it survives an
API restart and runs in a worker, and any API instance can follow it.

Routes (all under ``/v1``; every one needs a signed-in user, and the two that change state need the CSRF header):

- ``POST /reports``: open pulls (or a whole report). Answers ``ready`` with no job when every pull asked for is already
  loaded (200); queues an ingest (202); or returns the queued or running ingest that already covers the request (200),
  whoever started it, so a second visitor opening a pull that is loading follows the first one's job.
- ``GET /jobs/{id}``: the job's state, per-pull steps (download, prepare, analyse) and typed error.
- ``GET /jobs/{id}/events``: the same as a server-sent event stream.
- ``POST /jobs/{id}/cancel``: cancel it. Pulls already written stay written; the raw page cache resumes.

Any signed-in user who can read a job's report may read the job and its stream (the access rule every report route
uses, so losing access to a report ends it); only the user who started a job may cancel it (403 ``not_your_job``),
and only they see its points spent. A missing job, or a process job, answers 404 ``job_not_found``.

Event stream
------------
``job.state`` (a ``Job``) is always the first event, so a client that reconnects gets the current state and nothing
is replayed. After that: ``pull.progress`` (a ``JobPull``) when a pull's stage, percentage or status changes,
``job.state`` when the job's state, stage, percentage or error changes, and, last, ``job.done`` (a ``Job``) or
``error`` (a ``JobFailure``) before the stream closes. A cancelled job ends with its ``job.state`` event alone.
Events carry increasing ``id`` values within one connection. A comment line is sent every
``KHADBOT_SSE_KEEPALIVE_S`` seconds, and each one also re-reads the job, so a lost notification delays the stream by
at most that long.

The stream subscribes to ``job_events`` (Postgres ``NOTIFY``) before it reads the job, so a change between the two
is not missed, and it reads the row again on every notification for the job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from khadbot.api.budget import get_budget
from khadbot.api.deps import SignedIn, csrf, require_readable
from khadbot.api.schemas import (
    ErrorBody,
    IngestRequest,
    IngestResponse,
    Job,
    JobErrorCode,
    JobFailure,
    JobPull,
    JobState,
    JobStep,
    PullStatus,
    StepName,
    StepState,
)
from khadbot.api.views import ApiError, difficulty_name
from khadbot.config import ApiConfig, QueueConfig
from khadbot.jobs.budget_store import BudgetManager
from khadbot.jobs.failures import ErrorCode
from khadbot.jobs.queue import JobQueue  # hosted only: this module is imported only when the app has a queue
from khadbot.jobs.specs import AppAuth, IngestReport, UserAuth
from khadbot.jobs.status import JobKind, JobRecord, JobStore, PullProgress
from khadbot.jobs.status import JobState as StoredState
from khadbot.jobs.steps import pull_steps

logger = logging.getLogger(__name__)

_REPORT_CODE = re.compile(r"^[A-Za-z0-9]{1,40}$")
_WCL_HOST = re.compile(r"^([a-z0-9-]+\.)*warcraftlogs\.com$")
_REPORT_PATH = re.compile(r"^/reports/([A-Za-z0-9]{1,40})/?$")
_TERMINAL = frozenset({StoredState.DONE, StoredState.FAILED, StoredState.CANCELLED})

# ---------------------------------------------------------------------------
# Parsing the request
# ---------------------------------------------------------------------------


def parse_source(source: str, fights: list[int] | None) -> tuple[str, list[int] | None]:
    """
    The report code and fight list an ingest request names.

    ``source`` is a report code or a WCL report URL. A ``fight=N`` in a URL's fragment or query selects that one
    fight, but only when ``fights`` is empty; ``fight=last`` and anything else non-numeric selects nothing.

    Raises:
        ApiError: 400 ``bad_source`` (not a code or a WCL report URL) or ``bad_fights`` (an ID below 1).
    """
    text = source.strip()
    url_fight: int | None = None
    if _REPORT_CODE.match(text):
        code = text
    else:
        try:
            parts = urlsplit(text if "://" in text else "https://" + text)
            host = (parts.hostname or "").lower()
        except ValueError:
            host, parts = "", urlsplit("")
        match = _REPORT_PATH.match(parts.path) if _WCL_HOST.match(host) else None
        if match is None:
            raise ApiError(400, "bad_source", "Enter a Warcraft Logs report code or a warcraftlogs.com report URL.")
        code = match.group(1)
        for query in (parts.query, parts.fragment):
            value = (parse_qs(query).get("fight") or [""])[0]
            if value.isdigit() and int(value) >= 1 and url_fight is None:
                url_fight = int(value)
    chosen = list(dict.fromkeys(fights)) if fights else None
    if chosen is None and url_fight is not None:
        chosen = [url_fight]
    if chosen is not None and any(f < 1 for f in chosen):
        raise ApiError(400, "bad_fights", "Fight IDs start at 1.")
    return code, chosen


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

_CODES = {
    ErrorCode.SIGN_IN_REQUIRED.value: JobErrorCode.wcl_not_signed_in,
    ErrorCode.PRIVATE_REPORT.value: JobErrorCode.report_not_found,  # WCL does not tell private from missing
    ErrorCode.RATE_LIMITED.value: JobErrorCode.rate_limited,
    ErrorCode.NETWORK.value: JobErrorCode.wcl_unreachable,
    ErrorCode.WCL_ERROR.value: JobErrorCode.wcl_error,
    ErrorCode.NOT_IN_CACHE.value: JobErrorCode.not_in_cache,
    ErrorCode.PULLS_FAILED.value: JobErrorCode.pulls_failed,
    ErrorCode.INTERRUPTED.value: JobErrorCode.interrupted,
}
_DEFAULT_MESSAGES = {
    JobErrorCode.wcl_not_signed_in: "Sign in to Warcraft Logs again, then start the ingest again.",
    JobErrorCode.report_not_found: "The report was not found, or it is private to another account.",
    JobErrorCode.rate_limited: "Warcraft Logs rate limited the requests.",
    JobErrorCode.wcl_unreachable: "Warcraft Logs could not be reached.",
    JobErrorCode.wcl_error: "Warcraft Logs answered with an error.",
    JobErrorCode.internal_error: "The ingest failed unexpectedly.",
}


EtaLookup = Callable[[JobRecord], Awaitable[float | None]]
"""Seconds until a waiting job is expected to get its WCL points, or None when there is no estimate."""


def budget_eta(manager: BudgetManager) -> EtaLookup:
    """The ETA lookup for jobs in ``waiting_budget``: the budget manager's estimate for the job, in seconds from now."""

    async def eta_s(rec: JobRecord) -> float | None:
        if rec.state is not StoredState.WAITING_BUDGET:
            return None
        wait = await manager.wait_of(rec.id)
        if wait is None:
            return None
        return max(0.0, (wait.eta - datetime.now(UTC)).total_seconds())

    return eta_s


def request_eta(request: Request) -> EtaLookup:
    """The ETA lookup for one request. The budget manager is fetched only for a job that is actually waiting."""

    async def eta_s(rec: JobRecord) -> float | None:
        if rec.state is not StoredState.WAITING_BUDGET:
            return None
        return await budget_eta(get_budget(request))(rec)

    return eta_s


def failure_for(rec: JobRecord, config: QueueConfig, now: datetime, *, eta_s: float | None = None) -> JobFailure | None:
    """
    The typed error a job shows: ``budget_wait`` while it waits for points (``eta_s`` from the budget manager); the
    last failure when it failed or is queued for a retry (``rate_limited`` then carries the seconds left of the
    retry's backoff); otherwise None.
    """
    if rec.state is StoredState.WAITING_BUDGET:
        return JobFailure(
            code=JobErrorCode.budget_wait,
            message="Waiting for Warcraft Logs points to be available.",
            eta_s=eta_s,
        )
    if rec.error_code is None or rec.state in (StoredState.DONE, StoredState.CANCELLED):
        return None
    code = _CODES.get(rec.error_code, JobErrorCode.internal_error)
    retry_after: float | None = None
    if code is JobErrorCode.rate_limited and rec.progress.retry_at is not None:
        # When the queue will try again (a queued job), or when WCL said to (a job that ran out of retries).
        retry_after = max(0.0, (rec.progress.retry_at - now).total_seconds())
    elif code is JobErrorCode.rate_limited and rec.state is StoredState.QUEUED:
        # No stored time: the queue waits backoff ** attempts seconds before the next try (jobs.queue's strategy).
        wait = float(config.retry_backoff_s) ** max(rec.attempts, 1)
        retry_after = max(0.0, wait - (now - rec.updated_at).total_seconds())
    return JobFailure(
        code=code,
        message=rec.error_message or _DEFAULT_MESSAGES.get(code, _DEFAULT_MESSAGES[JobErrorCode.internal_error]),
        retry_after_s=retry_after,
    )


def _pull_status(stage: str, state: str) -> PullStatus:
    if state == "failed":
        return PullStatus.failed
    if state == "cancelled":
        return PullStatus.cancelled
    if state == "done":
        return PullStatus.done
    if stage == "normalizing":
        return PullStatus.normalizing
    if stage in ("records", "project"):
        return PullStatus.processing
    return PullStatus.fetching


def _pull_view(key: str, rec: JobRecord) -> JobPull:
    """One pull of a job: from its progress entry, or, before it has reported, from the job's own state."""
    fight = key.rpartition(":")[2]
    progress: PullProgress | None = rec.progress.pulls.get(key)
    info = rec.progress.info.get(key)
    steps = pull_steps(progress, StoredState(rec.state.value), fetched=rec.awaiting_children)
    if progress is not None:
        stage, pct, status, message = (
            progress.stage,
            round(min(max(progress.fraction, 0.0), 1.0) * 100, 1),
            _pull_status(progress.stage, progress.state),
            progress.error,
        )
    else:
        running = rec.state is StoredState.RUNNING
        stage, pct, message = "", 0.0, None
        status = PullStatus.fetching if running else PullStatus.waiting
    return JobPull(
        pull_key=key,
        fight=int(fight) if fight.isdigit() else 0,
        encounter_id=info.encounter_id if info else None,
        encounter_name=info.encounter_name if info else None,
        difficulty=info.difficulty if info else None,
        difficulty_name=difficulty_name(info.difficulty) if info else None,
        steps=[JobStep(name=StepName(s.step.value), state=StepState(s.state.value), pct=s.pct) for s in steps],
        stage=stage,
        pct=pct,
        status=status,
        message=message,
    )


def _pull_keys(rec: JobRecord) -> list[str]:
    """Every pull the job covers: the fights it was asked for (before they report) and those that have reported."""
    keys = {f"{rec.report_code}:{f}" for f in (rec.spec.get("fights") or []) if isinstance(f, int)}
    keys.update(rec.progress.pulls)
    keys.update(rec.progress.info)
    return list(keys)


def job_view(
    rec: JobRecord,
    config: QueueConfig | None = None,
    now: datetime | None = None,
    *,
    eta_s: float | None = None,
    own: bool = True,
) -> Job:
    """
    The API's view of a job row. ``eta_s`` is the budget wait of a job in ``waiting_budget``. ``own`` says the viewer
    started the job: only they may cancel it and see its points spent.
    """
    pulls = sorted((_pull_view(k, rec) for k in _pull_keys(rec)), key=lambda p: (p.fight, p.pull_key))
    done = rec.state is StoredState.DONE
    return Job(
        id=rec.id,
        report_code=rec.report_code,
        state=JobState(rec.state.value),
        stage=rec.progress.stage,
        pct=100.0 if done else round(min(max(rec.progress.fraction, 0.0), 1.0) * 100, 1),
        pulls=pulls,
        error=failure_for(rec, config or QueueConfig(), now or datetime.now(UTC), eta_s=eta_s),
        points_spent=rec.points_spent if own else None,
        cancel_requested=rec.cancel_requested,
        can_cancel=own and rec.active,
        created_at=rec.created_at,
        started_at=rec.started_at,
        finished_at=rec.finished_at,
        updated_at=rec.updated_at,
    )


# ---------------------------------------------------------------------------
# The event stream
# ---------------------------------------------------------------------------


def sse(event_id: int, event: str, data: BaseModel) -> str:
    """One server-sent event. The JSON is on one line, so ``data:`` needs no continuation lines."""
    return f"id: {event_id}\nevent: {event}\ndata: {data.model_dump_json()}\n\n"


def _changes(old: Job, new: Job) -> list[tuple[str, BaseModel]]:
    """The events that take a client from ``old`` to ``new``, ending with the terminal one."""
    out: list[tuple[str, BaseModel]] = []
    before = {p.pull_key: p for p in old.pulls}
    out.extend(("pull.progress", p) for p in new.pulls if before.get(p.pull_key) != p)
    if (old.state, old.stage, old.pct, old.error, old.cancel_requested) != (
        new.state,
        new.stage,
        new.pct,
        new.error,
        new.cancel_requested,
    ):
        out.append(("job.state", new))
    return [*out, *_terminal(new)]


def _terminal(job: Job) -> list[tuple[str, BaseModel]]:
    if job.state is JobState.done:
        return [("job.done", job)]
    if job.state is JobState.failed:
        failure = job.error or JobFailure(
            code=JobErrorCode.internal_error, message=_DEFAULT_MESSAGES[JobErrorCode.internal_error]
        )
        return [("error", failure)]
    return []


def _is_terminal(job: Job) -> bool:
    return job.state in (JobState.done, JobState.failed, JobState.cancelled)


async def event_stream(
    store: JobStore,
    job_id: str,
    *,
    keepalive_s: float,
    config: QueueConfig | None = None,
    eta: EtaLookup | None = None,
    own: bool = True,
) -> AsyncIterator[str]:
    """
    The job's server-sent events, as text chunks, until it ends. See the module docstring for the event order.

    The subscription to ``job_events`` is opened before the first read of the job, and a pending wait for the next
    notification is never cancelled by a timeout (cancelling an async generator's wait would end it); it is kept
    until it completes, and cancelled only when the stream ends.
    """
    seq = 0

    async def view(rec: JobRecord) -> Job:
        return job_view(rec, config, eta_s=await eta(rec) if eta is not None else None, own=own)

    async with store.listen() as notices:
        pending: asyncio.Future[str] | None = None
        try:
            rec = await store.get(job_id)
            if rec is None:
                return
            current = await view(rec)
            seq += 1
            yield sse(seq, "job.state", current)
            for name, payload in _terminal(current):
                seq += 1
                yield sse(seq, name, payload)
            if _is_terminal(current):
                return
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(anext(notices))
                done, _ = await asyncio.wait({pending}, timeout=keepalive_s)
                if done:
                    try:
                        notice = pending.result()
                    except StopAsyncIteration:
                        return  # the subscription ended; the client reconnects and gets the current state
                    pending = None
                    if notice != job_id:
                        continue
                else:
                    yield ": keepalive\n\n"
                rec = await store.get(job_id)
                if rec is None:
                    return
                latest = await view(rec)
                for name, payload in _changes(current, latest):
                    seq += 1
                    yield sse(seq, name, payload)
                current = latest
                if _is_terminal(current):
                    return
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
            if pending is not None:
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                    await pending


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.jobs
    return queue


Queue = Annotated[JobQueue, Depends(get_queue)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody, "description": "Not signed in (not_signed_in)"},
    403: {"model": ErrorBody, "description": "CSRF check failed (csrf_failed), or not your job (not_your_job)"},
    404: {"model": ErrorBody, "description": "No such job, or a report you cannot read"},
    422: {"model": ErrorBody, "description": "Invalid body or parameter (bad_request)"},
    503: {"model": ErrorBody, "description": "Warcraft Logs could not answer the access check (wcl_unavailable)"},
}
_READ_ERRORS = {k: v for k, v in _ERRORS.items() if k != 403}

# The events route's response class is an event stream, which FastAPI would also name as the media type of its errors.
_JSON_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {
        "description": r["description"],
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorBody"}}},
    }
    for status, r in _READ_ERRORS.items()
}

router = APIRouter(prefix="/v1", tags=["jobs"])


class EventStreamResponse(StreamingResponse):
    """A server-sent event stream (`text/event-stream`); the class names the media type in the OpenAPI document."""

    media_type = "text/event-stream"


async def _ingest_job(queue: JobQueue, job_id: str) -> JobRecord:
    """An ingest job by ID. Process and review jobs are internal and answer like a missing one."""
    rec = await queue.store.get(job_id)
    if rec is None or rec.kind is not JobKind.INGEST:
        raise ApiError(404, "job_not_found", "No such job.")
    return rec


async def _readable_job(request: Request, queue: JobQueue, job_id: str) -> JobRecord:
    """An ingest job the caller may follow: they can read its report (anyone who can may, not only who started it)."""
    rec = await _ingest_job(queue, job_id)
    await require_readable(request, rec.report_code)
    return rec


def _written(request: Request, code: str, fights: list[int] | None) -> bool:
    """
    Whether every pull asked for is already loaded: in the event store with its records stored. A whole-report request
    (no fights) is never \"written\", since the report's pulls are not known without asking Warcraft Logs. Records built
    under older inputs count: the digest route rebuilds them on read.
    """
    if not fights:
        return False
    data = request.app.state.ctx.data
    if data.records_store is None:
        return False
    try:
        stored = {p.fight_id for p in data.store.pulls(code)}
        keys = data.records_store.keys(code)
    except Exception:  # noqa: BLE001 - not knowing means loading it
        logger.warning("Could not check whether the pulls of %s are loaded", code, exc_info=True)
        return False
    names = {k.rpartition("/")[2] for k in keys}
    return all(f in stored and any(n.startswith(f"{f}__") for n in names) for f in fights)


@router.post(
    "/reports",
    response_model=IngestResponse,
    status_code=202,
    dependencies=[Depends(csrf)],
    responses={
        200: {
            "model": IngestResponse,
            "description": "Every pull asked for is already loaded (`ready`, no job), or a queued or running ingest "
            "already covers the request (`job`, whoever started it)",
        },
        **_ERRORS,
        400: {"model": ErrorBody, "description": "Not a report code or URL (bad_source, bad_fights)"},
    },
)
async def start_ingest(
    body: IngestRequest, request: Request, response: Response, user: SignedIn, queue: Queue
) -> IngestResponse:
    """
    Open pulls of a report, or all of it. The caller must be able to read the report (404 `report_not_found`
    otherwise). A pull that is already loaded answers `ready` with no job. One that is loading answers the running job
    (200), also for a caller who did not start it: they may follow it but not cancel it. Otherwise a job is queued
    (202); it fetches with the caller's own Warcraft Logs token when they have one, else with the app's.
    """
    code, fights = parse_source(body.source, body.fights)
    await require_readable(request, code)
    if _written(request, code, fights):
        response.status_code = 200
        return IngestResponse(ready=True, job=None)
    service = request.app.state.auth
    auth = UserAuth(user_id=user.id) if await service.tokens.has_tokens(user.id) else AppAuth()
    try:
        queued = await queue.enqueue(IngestReport(code=code, fights=fights, auth=auth), requested_by=user.id)
    except Exception:
        logger.exception("Could not queue an ingest of report %s", code)
        raise ApiError(503, "queue_unavailable", "The job queue is unavailable; try again shortly.") from None
    if not queued.created:
        response.status_code = 200
    job = job_view(
        queued.job,
        queue.config,
        eta_s=await request_eta(request)(queued.job),
        own=queued.job.requested_by == user.id,
    )
    return IngestResponse(ready=False, job=job)


@router.get("/jobs/{job_id}", response_model=Job, responses=_READ_ERRORS)
async def get_job(job_id: str, request: Request, user: SignedIn, queue: Queue) -> Job:
    """A job on a report you can read: its state, per-pull steps and typed error."""
    rec = await _readable_job(request, queue, job_id)
    return job_view(rec, queue.config, eta_s=await request_eta(request)(rec), own=rec.requested_by == user.id)


@router.post("/jobs/{job_id}/cancel", response_model=Job, dependencies=[Depends(csrf)], responses=_ERRORS)
async def cancel_job(job_id: str, request: Request, user: SignedIn, queue: Queue) -> Job:
    """
    Cancel a job you started (403 `not_your_job` for anyone else who follows it). A queued job is dropped at once; a
    running one stops at its next page and shows `cancel_requested` until it has. A finished job is returned unchanged.
    """
    rec = await _readable_job(request, queue, job_id)
    if rec.requested_by != user.id:
        raise ApiError(403, "not_your_job", "Only the person who started this can stop it.")
    cancelled = await queue.cancel(job_id)
    if cancelled is None:
        raise ApiError(404, "job_not_found", "No such job.")
    return job_view(cancelled, queue.config, eta_s=await request_eta(request)(cancelled), own=True)


@router.get(
    "/jobs/{job_id}/events",
    response_model=None,
    response_class=EventStreamResponse,
    responses={
        200: {
            "description": "Server-sent events: `job.state` (a Job; always first), `pull.progress` (a JobPull), "
            "`job.done` (a Job) and `error` (a JobFailure). The stream closes after `job.done`, `error`, or a "
            "`job.state` whose state is `cancelled`.",
        },
        **_JSON_ERRORS,
    },
)
async def job_events(job_id: str, request: Request, user: SignedIn, queue: Queue) -> StreamingResponse:
    """Follow a job on a report you can read as server-sent events. A reconnecting client gets the current state first."""
    rec = await _readable_job(request, queue, job_id)
    stream = event_stream(
        queue.store,
        job_id,
        keepalive_s=ApiConfig().sse_keepalive_s,
        config=queue.config,
        eta=request_eta(request),
        own=rec.requested_by == user.id,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
