"""
api/budget.py

``GET /v1/budget``: which WCL budget the signed-in user's next ingest uses, and on the shared one their allowance
left and the current wait. Hosted only: ``create_app`` mounts this router with the sign-in routes.

The budget manager comes from ``app.state.budget`` when a caller set one (tests), else it is built once from the
configuration (``jobs.budget_store.build_budget_manager``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from khadbot.auth.routes import Auth, CurrentUser
from khadbot.config import WclLimitScheme
from khadbot.errors import ErrorBody
from khadbot.jobs.budget_store import BudgetManager, build_budget_manager

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class AllowanceView(BaseModel):
    """One allowance in WCL points for the current window."""

    limit: float
    used: float
    left: float


class BudgetResponse(BaseModel):
    """The WCL budget as the signed-in user sees it."""

    fetches_with: Literal["user", "app"] = Field(
        description="Whose WCL token the next ingest fetches with: the user's own when they are signed in to WCL."
    )
    charged_to: Literal["shared", "user_first"] = Field(
        description="Whose points WCL charges: the app's shared points, or the user's own pool first."
    )
    scheme: Literal["current", "new"] = Field(description="WCL's limit scheme for the app's account.")
    limit_per_hour: float = Field(description="The app's WCL points per window.")
    spent: float = Field(description="Points spent this window, as WCL last reported plus jobs settled since.")
    held: float = Field(description="Points reserved by jobs running now.")
    resets_at: datetime = Field(description="When WCL's current window ends and the allowances start again.")
    allowance: AllowanceView = Field(description="The user's own allowance of the shared points.")
    waiting_jobs: int = Field(description="Ingests waiting for points, across all users.")
    wait_s: float = Field(description="Seconds an ingest of a typical night asked for now would wait; 0 starts now.")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def get_budget(request: Request) -> BudgetManager:
    manager: BudgetManager | None = getattr(request.app.state, "budget", None)
    if manager is None:
        manager = build_budget_manager()
        request.app.state.budget = manager
    return manager


Budget = Annotated[BudgetManager, Depends(get_budget)]

router = APIRouter(prefix="/v1", tags=["budget"])


@router.get(
    "/budget",
    response_model=BudgetResponse,
    responses={401: {"model": ErrorBody, "description": "Not signed in (not_signed_in)"}},
)
async def budget(user: CurrentUser, auth: Auth, manager: Budget, response: Response) -> BudgetResponse:
    """Which WCL budget the next ingest uses, the user's allowance left and how long an ingest would wait now."""
    response.headers["Cache-Control"] = "no-store"
    cfg = manager.config
    user_token = await auth.tokens.has_tokens(user.id)
    view = await manager.view(user_id=user.id, user_token=user_token)
    user_first = (
        user_token
        and cfg.scheme is WclLimitScheme.NEW
        and not cfg.user_tokens_spend_app_budget
        and cfg.user_points_per_hour is not None
    )
    return BudgetResponse(
        fetches_with="user" if user_token else "app",
        charged_to="user_first" if user_first else "shared",
        scheme=cfg.scheme.value,
        limit_per_hour=view.limit_per_hour,
        spent=view.spent,
        held=view.held,
        resets_at=view.resets_at,
        allowance=AllowanceView(limit=view.user.limit, used=view.user.used, left=view.user.left),
        waiting_jobs=view.waiting_jobs,
        wait_s=max(0.0, (view.eta - view.as_of).total_seconds()),
    )


__all__ = ["AllowanceView", "BudgetResponse", "get_budget", "router"]
