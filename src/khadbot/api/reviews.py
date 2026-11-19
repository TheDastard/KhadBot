"""
api/reviews.py

Pull reviews, hosted mode (mounted with the job queue; sets ``features.reviews``).

- ``GET /reports/{code}/pulls/{fight}/review`` says what a review of the pull would do for the caller: whether its
  findings are already cached, what it would cost in tokens, the caller's quota, and the review already running.
- ``POST`` (sign-in and CSRF) answers with the cached findings when the findings cache key hits, which is shared by
  everyone who can read the report and costs no quota; otherwise it queues a ``ReviewPull`` on the ``review`` queue,
  after checking the caller's rolling quota (429 ``quota_exceeded``). An identical review already queued or running is
  returned, whoever started it. The job is followed through ``/jobs/{id}`` and its findings read through the findings
  route.

The cache key is the one the worker computes (``review_graph.pull_cache_key``) with the analyst model from the
configuration (``jobs.reviews.analyst_model_id``); the job carries that model so a worker configured otherwise fails
the job before any model call.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from khadbot.api.deps import ACCESS_BUDGET_RESPONSE, Caller, SignedIn, csrf, readable_report
from khadbot.api.jobs import Queue, job_view, request_eta
from khadbot.api.routes import Ctx
from khadbot.api.schemas import (
    ErrorBody,
    QuotaExceededBody,
    ReviewEstimate,
    ReviewQuota,
    ReviewRequest,
    ReviewResponse,
    ReviewStatus,
)
from khadbot.api.views import ApiContext, ApiError
from khadbot.config import ReviewConfig
from khadbot.encounter.analyst import estimate_review_cost
from khadbot.encounter.analyst.review_graph import pull_cache_key, review_prompt_version
from khadbot.encounter.models import Depth
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.reviews import REVIEW_WINDOW, analyst_model_id, quota_from
from khadbot.jobs.reviews import ReviewQuota as QuotaState
from khadbot.jobs.specs import ReviewPull
from khadbot.jobs.status import JobRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["reviews"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "Report or pull not ingested, or a report the caller cannot read"},
    422: {"model": ErrorBody, "description": "Invalid body or parameter (bad_request)"},
    503: {"model": ErrorBody, "description": "Warcraft Logs could not answer the access check (wcl_unavailable)"},
    **ACCESS_BUDGET_RESPONSE,
}

# ---------------------------------------------------------------------------
# Looking at the pull
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Inspected:
    pull_key: str
    model: str
    cached: bool
    estimate: ReviewEstimate


def _inspect(ctx: ApiContext, code: str, fight: int, depth: Depth) -> _Inspected:
    """Whether the pull's review is cached under the current data, prompts and model, and what a review would cost."""
    with ctx.lock:
        ctx.records(code, fight)  # 404 pull_not_found / report_not_found
        pull_key = f"{code}:{fight}"
        model = analyst_model_id()
        key = pull_cache_key(ctx.data, pull_key, model_id=model, version=review_prompt_version(), depth=depth)
        hit = ctx.findings.get(key)
        estimate = estimate_review_cost([pull_key], ctx.data)
    return _Inspected(
        pull_key=pull_key,
        model=model,
        cached=hit is not None and not hit.flagged,
        estimate=ReviewEstimate(
            digest_tokens=estimate.digest_tokens, output_tokens=estimate.output_tokens, total_tokens=estimate.total
        ),
    )


def _quota_view(quota: QuotaState) -> ReviewQuota:
    return ReviewQuota(
        limit=quota.limit,
        used=quota.used,
        remaining=quota.remaining,
        window_hours=int(REVIEW_WINDOW.total_seconds() // 3600),
        next_slot_at=quota.next_slot_at if quota.remaining == 0 else None,
    )


async def _active_review(queue: JobQueue, pull_key: str, depth: Depth, model: str) -> JobRecord | None:
    wanted = ReviewPull(pull_key=pull_key, depth=depth, model=model)
    for rec in await queue.store.active_reviews(pull_key):
        if ReviewPull.model_validate(rec.spec) == wanted:
            return rec
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/reports/{code}/pulls/{fight}/review",
    response_model=ReviewStatus,
    responses=_ERRORS,
    dependencies=[Depends(readable_report)],
)
async def get_review_status(
    code: str, fight: int, ctx: Ctx, user: Caller, queue: Queue, depth: Depth = "standard"
) -> ReviewStatus:
    """
    What a review of the pull would do now, for anyone who can read the report: whether its findings are cached (a
    review is then free), the token estimate, the review already queued or running, and, signed in, the caller's
    quota. A visitor sees `quota` null: only signed-in users can start a review.
    """
    seen = await asyncio.to_thread(_inspect, ctx, code, fight, depth)
    active = await _active_review(queue, seen.pull_key, depth, seen.model)
    quota = None
    if user is not None:
        counted = await queue.store.reviews_since(user.id, datetime.now(UTC) - REVIEW_WINDOW)
        quota = _quota_view(quota_from(counted, ReviewConfig().per_day))
    job = (
        job_view(active, queue.config, own=user is not None and active.requested_by == user.id)
        if active is not None
        else None
    )
    return ReviewStatus(depth=depth, model=seen.model, cached=seen.cached, estimate=seen.estimate, quota=quota, job=job)


@router.post(
    "/reports/{code}/pulls/{fight}/review",
    response_model=ReviewResponse,
    status_code=202,
    dependencies=[Depends(csrf), Depends(readable_report)],
    responses={
        200: {
            "model": ReviewResponse,
            "description": "The findings are already cached (`cached`, no job), or an identical review is already "
            "queued or running (`job`, whoever started it)",
        },
        **_ERRORS,
        401: {"model": ErrorBody, "description": "Signing in is needed to review (not_signed_in)"},
        403: {"model": ErrorBody, "description": "CSRF check failed (csrf_failed)"},
        429: {
            "model": QuotaExceededBody,
            "description": "The caller has used their reviews for the window (quota_exceeded, with `Retry-After` "
            "when one will free up), or no WCL points are free to check access (budget_exhausted)",
        },
    },
)
async def start_review(
    code: str,
    fight: int,
    body: ReviewRequest,
    request: Request,
    response: Response,
    ctx: Ctx,
    user: SignedIn,
    queue: Queue,
) -> Any:
    """
    Review the pull, or return its cached findings. Cached findings (same data, prompts, model and depth) are shared by
    everyone who can read the report: the answer is `cached`, with no job and no quota used. Otherwise the caller's
    quota is checked (429 `quota_exceeded`) and a review job is queued (202); an identical one already queued or
    running is returned instead (200), for anyone who can read the report. Follow the job with `GET /v1/jobs/{id}`
    and read the findings with `GET /v1/reports/{code}/pulls/{fight}/findings`.
    """
    limit = ReviewConfig().per_day
    seen = await asyncio.to_thread(_inspect, ctx, code, fight, body.depth)
    if seen.cached:
        counted = await queue.store.reviews_since(user.id, datetime.now(UTC) - REVIEW_WINDOW)
        response.status_code = 200
        return ReviewResponse(
            cached=True, job=None, estimate=seen.estimate, quota=_quota_view(quota_from(counted, limit))
        )
    spec = ReviewPull(pull_key=seen.pull_key, depth=body.depth, model=seen.model)
    try:
        queued = await queue.enqueue_review(spec, requested_by=user.id, limit=limit)
    except Exception:
        logger.exception("Could not queue a review of %s", seen.pull_key)
        raise ApiError(503, "queue_unavailable", "The job queue is unavailable; try again shortly.") from None
    quota = _quota_view(queued.quota)
    if queued.job is None:
        wait = None
        if quota.next_slot_at is not None:
            wait = max(0.0, (quota.next_slot_at - datetime.now(UTC)).total_seconds())
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(math.ceil(wait))} if wait is not None else {},
            content=QuotaExceededBody(
                code="quota_exceeded",
                message=f"You have used your {limit} reviews for the last {quota.window_hours} hours.",
                quota=quota,
                wait_s=wait,
            ).model_dump(mode="json"),
        )
    if not queued.created:
        response.status_code = 200
    job = job_view(
        queued.job,
        queue.config,
        eta_s=await request_eta(request)(queued.job),
        own=queued.job.requested_by == user.id,
    )
    return ReviewResponse(cached=False, job=job, estimate=seen.estimate, quota=quota)
