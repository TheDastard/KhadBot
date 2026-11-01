"""
api/schemas/health.py

``GET /v1/health``: the server's status and the optional areas it offers (``Features``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from khadbot.api.schemas.common import API_VERSION


class Features(BaseModel):
    """
    The optional areas of the API and whether this server offers each. A flag is true only when the area's routes are
    mounted, so a client that hides what is off never links to a route that answers 404.
    """

    sign_in: bool = Field(default=False, description="Warcraft Logs sign-in: /v1/auth/* and /v1/me")
    ingest_jobs: bool = Field(
        default=False, description="Ingest jobs and their event streams: POST /v1/reports and /v1/jobs/*"
    )
    live_watch: bool = Field(default=False, description="Live report watch")
    budget: bool = Field(default=False, description="The WCL point budget: /v1/budget")
    reviews: bool = Field(default=False, description="Pull reviews on the queue")
    chat: bool = Field(default=False, description="Chat with the conversation agent")
    report_lookup: bool = Field(
        default=False, description="Report lookup: a report's boss pulls before any is loaded, GET /v1/lookup/report"
    )


class Health(BaseModel):
    """
    Status, the data directory and what this server offers. ``mode`` is the entry point's label: ``local`` for
    ``main.py serve`` (read routes over a data directory, no sign-in) and ``hosted`` for the hosted API. It is shown to
    people; clients branch on ``features``, never on ``mode``.
    """

    status: Literal["ok"] = "ok"
    data_dir: str
    api_version: str = API_VERSION
    mode: Literal["local", "hosted"] = "local"
    features: Features = Field(default_factory=Features)  # type: ignore[arg-type]
