"""
api/budget.py

``GET /v1/budget``: which WCL budget the signed-in user's next ingest uses, and on the shared one their allowance
left and the current wait. Hosted only: ``create_app`` mounts this router with the sign-in routes.

The budget manager comes from ``app.state.budget`` when a caller set one (tests), else it is built once from the
configuration (``jobs.budget_store.build_budget_manager``).

``AccessCheckBudget`` is the budget as the report access check uses it: ``create_app`` gives it to the sign-in
service, so an access check that has to ask WCL reserves its point first.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from pydantic import BaseModel, Field

from khadbot.auth.errors import AccessBudgetError
from khadbot.auth.routes import Auth, CurrentUser
from khadbot.config import WclLimitScheme
from khadbot.errors import ErrorBody
from khadbot.jobs.budget import Ask
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
# Access checks
# ---------------------------------------------------------------------------

ACCESS_CHECK_POINTS = 1.0  # one ReportReadable request; WCL charges every request at least 1 point
ACCESS_WAIT_S = 60.0  # what a caller is told when the budget gives no estimate


class AccessCheckBudget:
    """
    The WCL budget as the report access check uses it (``auth.access.AccessBudget``): each uncached check reserves
    its point as the caller, a signed-out one against the visitors' shared allowance, so nobody can spend the
    app's points by naming report codes. A check never waits in line: without a point it is refused with the wait.

    Args:
        manager: Gives the app's budget manager, or None when the app has none (an app built without a budget,
            as most tests are): checks are then not counted.
    """

    def __init__(self, manager: Callable[[], BudgetManager | None]) -> None:
        self._manager = manager

    async def reserve(self, user_id: str | None, code: str) -> str:
        manager = self._manager()
        if manager is None:
            return ""
        ask = Ask(
            job_id=f"access:{uuid.uuid4().hex}",
            report_code=code,
            user_id=user_id,
            points=ACCESS_CHECK_POINTS,
            visitor=user_id is None,
            asked_at=datetime.now(UTC),
        )
        admission = await manager.request(ask)
        if ask.job_id in admission.granted:
            return ask.job_id
        wait = admission.waits.get(ask.job_id)
        await manager.release(ask.job_id)
        raise AccessBudgetError(
            "Warcraft Logs has no points to spare right now. Try again shortly.",
            wait_s=max(0.0, (wait.eta - ask.asked_at).total_seconds()) if wait else ACCESS_WAIT_S,
        )

    async def finish(self, reservation: str, *, answered: bool) -> None:
        manager = self._manager()
        if manager is None or not reservation:
            return
        if answered:
            await manager.settle(reservation, None)
        else:
            await manager.release(reservation)


def app_budget(app: FastAPI) -> BudgetManager:
    """The app's budget manager, built on first use (tests put their own on ``app.state.budget``)."""
    manager: BudgetManager | None = getattr(app.state, "budget", None)
    if manager is None:
        manager = build_budget_manager()
        app.state.budget = manager
    return manager


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def get_budget(request: Request) -> BudgetManager:
    return app_budget(request.app)


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


__all__ = ["AccessCheckBudget", "AllowanceView", "BudgetResponse", "app_budget", "get_budget", "router"]
