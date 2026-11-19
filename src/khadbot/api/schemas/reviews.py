"""
api/schemas/reviews.py

Pull reviews (hosted mode): ``GET`` and ``POST /v1/reports/{code}/pulls/{fight}/review``. A review is a job on the
``review`` queue that the caller follows through the job routes; its findings are read through the findings route.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from khadbot.api.schemas.common import ErrorBody
from khadbot.api.schemas.jobs import Job
from khadbot.encounter.models import Depth


class ReviewRequest(BaseModel):
    """Start a review of one pull."""

    depth: Depth = Field(
        default="standard", description="The analyst's step budget: `quick` (4 rounds), `standard` (8) or `deep` (16)"
    )


class ReviewQuota(BaseModel):
    """The caller's review allowance over a rolling window. A review answered from cached findings never counts."""

    limit: int = Field(description="Reviews allowed in any window")
    used: int = Field(
        description="Reviews counted in the window: queued, running and done ones; not failed or cancelled"
    )
    remaining: int = Field(description="`limit` minus `used`, never below 0")
    window_hours: int = Field(description="The rolling window's length, in hours")
    next_slot_at: datetime | None = Field(
        default=None, description="When one more review frees up; set only when `remaining` is 0 and a slot will free"
    )


class ReviewEstimate(BaseModel):
    """What a review of the pull would cost in model tokens if nothing were cached. An allowance, not a cap."""

    digest_tokens: int = Field(description="The pull's digest, which the analyst reads (about 4 characters a token)")
    output_tokens: int = Field(description="Allowance for the analyst's investigation and findings")
    total_tokens: int = Field(description="`digest_tokens` plus `output_tokens`")


class ReviewStatus(BaseModel):
    """What reviewing this pull would do now, for the review button."""

    depth: str = Field(description="The depth this answer is for")
    model: str = Field(description="The analyst model the review would run on; findings are cached per model")
    cached: bool = Field(
        description="Findings for this pull from this model, depth and data are cached: a review is free and instant"
    )
    estimate: ReviewEstimate
    quota: ReviewQuota | None = Field(default=None, description="Null for a signed-out visitor, who cannot review")
    job: Job | None = Field(
        default=None, description="A review of this pull that is queued or running, whoever started it; follow it"
    )


class ReviewResponse(BaseModel):
    """
    What `POST .../review` answers. `cached`: the findings already exist, read them from the findings route (no job,
    nothing counted). Otherwise `job` is the review to follow: a new one (202), or the identical one already queued or
    running (200).
    """

    cached: bool
    job: Job | None = None
    estimate: ReviewEstimate
    quota: ReviewQuota


class QuotaExceededBody(ErrorBody):
    """The answer when the caller has used their reviews: `quota_exceeded`."""

    quota: ReviewQuota
    wait_s: float | None = Field(default=None, description="Seconds until a review frees up, when one will")
