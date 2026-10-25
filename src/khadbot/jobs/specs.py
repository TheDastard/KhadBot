"""
jobs/specs.py

Job specs: the serializable description of one unit of work. A spec says what to do and on whose behalf, and
never carries a credential: ``auth`` names a user by ID, and the job resolves a WCL token at run time
(``jobs.run.TokenResolver``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from khadbot.encounter.models import Depth

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AppAuth(BaseModel):
    """Fetch with the app's own WCL token (public reports; the app's hourly points)."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["app"] = "app"


class UserAuth(BaseModel):
    """Fetch as a signed-in user, with that user's WCL token resolved at run time. Holds the user ID only."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["user"] = "user"
    user_id: str = Field(min_length=1)


Auth = Annotated[AppAuth | UserAuth, Field(discriminator="kind")]

# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


class IngestReport(BaseModel):
    """Fetch, normalize and store the pulls of one report (every encounter fight when ``fights`` is None)."""

    model_config = ConfigDict(frozen=True)

    job: Literal["ingest_report"] = "ingest_report"
    code: str = Field(min_length=1)
    fights: list[int] | None = None
    auth: Auth = Field(default_factory=AppAuth)
    offline: bool = False  # read the raw page cache only; never call WCL
    refresh_meta: bool = False  # refetch report.json only and update stored pulls
    live: bool = False  # queued by a live report watch: the WCL budget lets it use the headroom ordinary jobs leave

    @model_validator(mode="after")
    def _refresh_needs_wcl(self) -> IngestReport:
        if self.refresh_meta and self.offline:
            raise ValueError("refresh_meta refetches report metadata from WCL and cannot run offline")
        return self


class ProcessPull(BaseModel):
    """Annotate one ingested pull, build its records and store them (and, hosted, project the read models)."""

    model_config = ConfigDict(frozen=True)

    job: Literal["process_pull"] = "process_pull"
    pull_key: str = Field(min_length=1)


class ReviewPull(BaseModel):
    """Run the analyst's standard review of one pull, or return its cached findings."""

    model_config = ConfigDict(frozen=True)

    job: Literal["review_pull"] = "review_pull"
    pull_key: str = Field(min_length=1)
    depth: Depth = "standard"
    # The analyst model id the caller expects (the one it priced the review for). The job fails, before any model
    # call, when the configured analyst model is another one. None accepts whatever is configured.
    model: str | None = None


JobSpec = Annotated[IngestReport | ProcessPull | ReviewPull, Field(discriminator="job")]
