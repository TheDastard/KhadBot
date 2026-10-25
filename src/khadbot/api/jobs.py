"""
api/jobs.py

Hosted mode's ingest endpoints, over the Postgres job queue (``jobs.queue.JobQueue``). This router replaces the
single-process, in-memory job registry the first dashboard plan sketched: a job is a ``jobs`` row, so it survives an
API restart and runs in a worker, and any API instance can follow it.

Routes (all under ``/v1``; every one needs a signed-in user, and the two that change state need the CSRF header):

- ``POST /reports``: queue an ingest of a report code or WCL URL (202), or return the caller's own queued or running
  ingest that already covers it (200).
- ``GET /jobs/{id}``: the job's state, per-pull progress and typed error.
- ``GET /jobs/{id}/events``: the same as a server-sent event stream.
- ``POST /jobs/{id}/cancel``: cancel it. Pulls already written stay written; the raw page cache resumes.

A user sees only their own ingest jobs; any other job ID answers 404 ``job_not_found``, as does a missing one. Reading
a job or its stream also needs the caller to still be able to read the job's report (the access rule every report
route uses), so a user who lost access to a report cannot follow its ingest.

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
    Job,
    JobErrorCode,
    JobFailure,
    JobPull,
    JobState,
    PullStatus,
)
from khadbot.api.views import ApiError
from khadbot.config import ApiConfig, QueueConfig
from khadbot.jobs.budget_store import BudgetManager
from khadbot.jobs.failures import ErrorCode
from khadbot.jobs.queue import JobQueue  # hosted only: this module is imported only when the app has a queue
from khadbot.jobs.specs import AppAuth, IngestReport, UserAuth
from khadbot.jobs.status import JobKind, JobRecord, JobStore, PullProgress
from khadbot.jobs.status import JobState as StoredState

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
    if code is JobErrorCode.rate_limited and rec.state is StoredState.QUEUED:
        # The queue waits backoff ** attempts seconds before the next try (jobs.queue's RetryStrategy).
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


def _pull_view(key: str, progress: PullProgress) -> JobPull:
    fight = key.rpartition(":")[2]
    return JobPull(
        pull_key=key,
        fight=int(fight) if fight.isdigit() else 0,
        stage=progress.stage,
        pct=round(min(max(progress.fraction, 0.0), 1.0) * 100, 1),
        status=_pull_status(progress.stage, progress.state),
        message=progress.error,
    )


def job_view(
    rec: JobRecord, config: QueueConfig | None = None, now: datetime | None = None, *, eta_s: float | None = None
) -> Job:
    """The API's view of a job row. ``eta_s`` is the budget wait of a job in ``waiting_budget``."""
    pulls = sorted((_pull_view(k, p) for k, p in rec.progress.pulls.items()), key=lambda p: (p.fight, p.pull_key))
    done = rec.state is StoredState.DONE
    return Job(
        id=rec.id,
        report_code=rec.report_code,
        state=JobState(rec.state.value),
        stage=rec.progress.stage,
        pct=100.0 if done else round(min(max(rec.progress.fraction, 0.0), 1.0) * 100, 1),
        pulls=pulls,
        error=failure_for(rec, config or QueueConfig(), now or datetime.now(UTC), eta_s=eta_s),
        points_spent=rec.points_spent,
        cancel_requested=rec.cancel_requested,
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
) -> AsyncIterator[str]:
    """
    The job's server-sent events, as text chunks, until it ends. See the module docstring for the event order.

    The subscription to ``job_events`` is opened before the first read of the job, and a pending wait for the next
    notification is never cancelled by a timeout (cancelling an async generator's wait would end it); it is kept
    until it completes, and cancelled only when the stream ends.
    """
    seq = 0

    async def view(rec: JobRecord) -> Job:
        return job_view(rec, config, eta_s=await eta(rec) if eta is not None else None)

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
    403: {"model": ErrorBody, "description": "CSRF check failed (csrf_failed)"},
    404: {"model": ErrorBody, "description": "No such job of yours, or a report you cannot read"},
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


async def _own_job(queue: JobQueue, job_id: str, user_id: str) -> JobRecord:
    rec = await queue.store.get(job_id)
    if rec is None or rec.kind is not JobKind.INGEST or rec.requested_by != user_id:
        raise ApiError(404, "job_not_found", "No such job.")
    return rec


@router.post(
    "/reports",
    response_model=Job,
    status_code=202,
    dependencies=[Depends(csrf)],
    responses={
        200: {"model": Job, "description": "Your own queued or running ingest already covers this request"},
        **_ERRORS,
        400: {"model": ErrorBody, "description": "Not a report code or URL (bad_source, bad_fights)"},
        409: {
            "model": ErrorBody,
            "description": "Another user's ingest of this report is running (ingest_in_progress)",
        },
    },
)
async def start_ingest(body: IngestRequest, request: Request, response: Response, user: SignedIn, queue: Queue) -> Job:
    """
    Queue an ingest of a report. The caller must be able to read the report (404 ``report_not_found`` otherwise).
    The job fetches with the caller's own Warcraft Logs token when they have one, else with the app's.
    """
    code, fights = parse_source(body.source, body.fights)
    await require_readable(request, code)
    service = request.app.state.auth
    auth = UserAuth(user_id=user.id) if await service.tokens.has_tokens(user.id) else AppAuth()
    try:
        queued = await queue.enqueue(IngestReport(code=code, fights=fights, auth=auth), requested_by=user.id)
    except Exception:
        logger.exception("Could not queue an ingest of report %s", code)
        raise ApiError(503, "queue_unavailable", "The job queue is unavailable; try again shortly.") from None
    if queued.job.requested_by != user.id:
        raise ApiError(
            409,
            "ingest_in_progress",
            "Someone else is already ingesting this report. It will be in your reports when it finishes.",
        )
    if not queued.created:
        response.status_code = 200
    return job_view(queued.job, queue.config, eta_s=await request_eta(request)(queued.job))


@router.get("/jobs/{job_id}", response_model=Job, responses=_READ_ERRORS)
async def get_job(job_id: str, request: Request, user: SignedIn, queue: Queue) -> Job:
    """A job of yours: its state, per-pull progress and typed error."""
    rec = await _own_job(queue, job_id, user.id)
    await require_readable(request, rec.report_code)
    return job_view(rec, queue.config, eta_s=await request_eta(request)(rec))


@router.post("/jobs/{job_id}/cancel", response_model=Job, dependencies=[Depends(csrf)], responses=_ERRORS)
async def cancel_job(job_id: str, request: Request, user: SignedIn, queue: Queue) -> Job:
    """
    Cancel a job of yours. A queued job is dropped at once; a running one stops at its next page and shows
    ``cancel_requested`` until it has. A finished job is returned unchanged.
    """
    await _own_job(queue, job_id, user.id)
    cancelled = await queue.cancel(job_id)
    if cancelled is None:
        raise ApiError(404, "job_not_found", "No such job.")
    return job_view(cancelled, queue.config, eta_s=await request_eta(request)(cancelled))


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
    """Follow a job of yours as server-sent events. A reconnecting client gets the current state first."""
    rec = await _own_job(queue, job_id, user.id)
    await require_readable(request, rec.report_code)
    stream = event_stream(
        queue.store,
        job_id,
        keepalive_s=ApiConfig().sse_keepalive_s,
        config=queue.config,
        eta=request_eta(request),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
