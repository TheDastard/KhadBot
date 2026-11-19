"""
jobs/reviews.py

What the review routes and the review job share: the analyst model the deployment is configured with, and the rolling
review quota.

- ``analyst_model_id`` names the analyst model from the configuration without building it, so the API can key the
  findings cache and price a review without a model client (or a key). The job built from it carries the same name in
  ``ReviewPull.model`` and fails before any model call if the worker's analyst turns out to be another one.
- ``ReviewQuota`` is a user's allowance over the last ``REVIEW_WINDOW``, worked out from the ``jobs`` table: reviews
  that are queued, running or done count; failed and cancelled ones don't, and a review answered from the findings
  cache is never a job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from khadbot.config import get_config

REVIEW_WINDOW = timedelta(hours=24)

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def analyst_model_id() -> str:
    """
    The analyst's model name as the configuration gives it: ``<ROLE>_MODEL`` for the analyst role, else the provider's
    own model setting. A provider with no model setting (the fake one) is named by the provider, and a role override
    does not apply to it, as in ``llm_factory.get_llm``.
    """
    llm = get_config().llm
    provider = llm.role_providers.get("analyst") or llm.provider.value
    own = getattr(llm, f"{provider}_model", None)
    if own is None:
        return provider
    return llm.role_models.get("analyst") or own


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewQuota:
    """A user's review allowance. ``next_slot_at`` is when one more review frees up; None while some are left."""

    limit: int
    used: int
    next_slot_at: datetime | None

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def quota_from(counted: list[datetime], limit: int, window: timedelta = REVIEW_WINDOW) -> ReviewQuota:
    """The quota for a user whose counted reviews were requested at ``counted`` (oldest first)."""
    used = len(counted)
    next_slot = counted[used - limit] + window if used >= limit > 0 else None
    return ReviewQuota(limit=limit, used=used, next_slot_at=next_slot)
