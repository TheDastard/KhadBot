"""
api/watch.py

Live report watch routes (hosted only; ``create_app`` mounts this router with the ingest routes). A watch polls a
report that is still being logged on the caller's own Warcraft Logs token and ingests each new boss fight
(``jobs/watch.py``).

- ``POST /v1/reports/{code}/watch``: start a watch (202), or return the caller's own active watch (200).
- ``GET /v1/reports/{code}/watch``: the caller's active watch of the report, else their latest one.
- ``DELETE /v1/reports/{code}/watch``: stop it (200). Stopping a watch that already stopped returns it unchanged.

Every route needs a signed-in user and the caller must be able to read the report (404 ``report_not_found``
otherwise, the one access rule of every report route); ``POST`` and ``DELETE`` also need the CSRF header. A watch
uses its owner's token, so only a user with a Warcraft Logs sign-in can start one (409 ``wcl_sign_in_required``),
only the owner sees or stops it (another user's watch is a 404 ``watch_not_found``, and starting a watch on a report
someone else is watching is a 409 ``watch_in_progress``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from khadbot.api.deps import SignedIn, csrf, require_readable
from khadbot.api.jobs import Queue
from khadbot.api.schemas import ErrorBody
from khadbot.api.views import ApiError
from khadbot.config import WatchConfig
from khadbot.jobs.watch_store import WatchRecord

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class WatchStatus(BaseModel):
    """A live report watch."""

    report_code: str
    state: Literal["active", "stopped"]
    stop_reason: Literal["user", "quiet", "max_length", "sign_in", "report_unavailable", "errors"] | None = Field(
        default=None, description="Why a stopped watch stopped; null while it is active"
    )
    started_at: datetime
    last_polled_at: datetime | None
    next_poll_at: datetime | None = Field(description="When the next poll is due; null once stopped")
    stops_by: datetime | None = Field(
        description="The latest the watch runs to if no new boss fight turns up (the quiet period), capped by its "
        "maximum length; null once stopped"
    )
    expires_at: datetime = Field(description="When the watch stops whatever happens (its maximum length)")
    poll_interval_s: float
    polls: int = Field(description="Metadata reads that reached Warcraft Logs")
    polls_skipped: int = Field(description="Polls skipped because the WCL budget had no points for them")
    fights_found: int = Field(description="Boss fights the watch has handed to ingest")
    ingests: int = Field(description="Ingest jobs the watch queued; they are ordinary jobs under GET /v1/jobs/{id}")
    last_job_id: str | None = Field(description="The latest ingest job that covers a new fight")
    points_spent: float = Field(
        description="Points the polls reserved from the budget (the ingests count on their jobs)"
    )
    error: str | None = Field(
        default=None, description="Why the latest poll failed, or why the watch stopped on an error"
    )


def watch_view(rec: WatchRecord, config: WatchConfig) -> WatchStatus:
    """The API's view of a ``watches`` row."""
    stops_by: datetime | None = None
    if rec.active:
        stops_by = min(rec.expires_at, rec.quiet_since + timedelta(seconds=config.quiet_period_s))
    return WatchStatus(
        report_code=rec.report_code,
        state=rec.state.value,
        stop_reason=rec.stop_reason.value if rec.stop_reason else None,
        started_at=rec.started_at,
        last_polled_at=rec.last_polled_at,
        next_poll_at=rec.next_poll_at if rec.active else None,
        stops_by=stops_by,
        expires_at=rec.expires_at,
        poll_interval_s=config.poll_interval_s,
        polls=rec.polls,
        polls_skipped=rec.polls_skipped,
        fights_found=len(rec.fights),
        ingests=rec.ingests,
        last_job_id=rec.last_job_id,
        points_spent=rec.points_spent,
        error=rec.error_message,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody, "description": "Not signed in (not_signed_in)"},
    404: {
        "model": ErrorBody,
        "description": "A report you cannot read, or no watch of yours (report_not_found, watch_not_found)",
    },
    422: {"model": ErrorBody, "description": "Invalid parameter (bad_request)"},
    503: {"model": ErrorBody, "description": "Warcraft Logs could not answer the access check (wcl_unavailable)"},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    403: {"model": ErrorBody, "description": "CSRF check failed (csrf_failed)"},
}

router = APIRouter(prefix="/v1", tags=["watch"])


@router.post(
    "/reports/{code}/watch",
    response_model=WatchStatus,
    status_code=202,
    dependencies=[Depends(csrf)],
    responses={
        200: {"model": WatchStatus, "description": "You already watch this report"},
        **_WRITE_ERRORS,
        409: {
            "model": ErrorBody,
            "description": "You have no Warcraft Logs sign-in to poll with (wcl_sign_in_required), or someone else "
            "is watching this report (watch_in_progress)",
        },
    },
)
async def start_watch(code: str, request: Request, response: Response, user: SignedIn, queue: Queue) -> WatchStatus:
    """
    Watch a report that is still being logged. The watch polls its metadata on your own Warcraft Logs token and
    ingests each new boss fight, until you stop it, no boss fight turns up for a while, or it reaches its maximum length.
    """
    await require_readable(request, code)
    if not await request.app.state.auth.tokens.has_tokens(user.id):
        raise ApiError(409, "wcl_sign_in_required", "Sign in to Warcraft Logs to watch a report: it is polled as you.")
    try:
        watch, created = await queue.watches.start(code, user.id)
    except Exception:
        raise ApiError(503, "queue_unavailable", "The job queue is unavailable; try again shortly.") from None
    if watch.user_id != user.id:
        raise ApiError(409, "watch_in_progress", "Someone else is already watching this report.")
    if not created:
        response.status_code = 200
    return watch_view(watch, queue.watches.config)


@router.get("/reports/{code}/watch", response_model=WatchStatus, responses=_ERRORS)
async def get_watch(code: str, request: Request, user: SignedIn, queue: Queue) -> WatchStatus:
    """Your watch of the report: the active one, else the latest that stopped."""
    await require_readable(request, code)
    watch = await queue.watches.store.latest_for(code, user.id)
    if watch is None:
        raise ApiError(404, "watch_not_found", "You have not watched this report.")
    return watch_view(watch, queue.watches.config)


@router.delete(
    "/reports/{code}/watch", response_model=WatchStatus, dependencies=[Depends(csrf)], responses=_WRITE_ERRORS
)
async def stop_watch(code: str, request: Request, user: SignedIn, queue: Queue) -> WatchStatus:
    """Stop your watch of the report. Ingests it already queued carry on. A watch that already stopped is returned as it is."""
    await require_readable(request, code)
    watch = await queue.watches.stop(code, user.id)
    if watch is None:
        raise ApiError(404, "watch_not_found", "You have not watched this report.")
    return watch_view(watch, queue.watches.config)


__all__ = ["WatchStatus", "router", "watch_view"]
