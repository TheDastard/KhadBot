"""
api/raids/wcl_request.py

One guild or character search request to WCL, through the budget (``wcl_request``): it reserves points (and database
points for a report listing) like any other request, asks on the caller's own WCL token when they have one, else the
app's, and settles the reservation. A search never waits in line: with no points free it raises ``BudgetWaitError``,
which the routes turn into a 429 ``budget_exhausted`` with ``wait_s`` and ``Retry-After`` (``budget_response``).
"""

from __future__ import annotations

import logging
import math
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from khadbot.api.deps import auth_service
from khadbot.api.raids.lookup import (
    RATE_LIMITED_WAIT_S,
    BudgetWaitError,
    _user_provider,
)
from khadbot.api.schemas import (
    BudgetWaitBody,
)
from khadbot.api.views import ApiError
from khadbot.auth.clock import Clock
from khadbot.auth.errors import WclSignInRequiredError
from khadbot.auth.store import User
from khadbot.config import SearchConfig
from khadbot.encounter.ingest.fetch import TokenProvider, UserTokenRejectedError, query_wcl
from khadbot.jobs.budget import Ask
from khadbot.jobs.budget_store import BudgetManager
from khadbot.wcl.client import WarcraftLogsAPIError, WarcraftLogsRateLimitError

logger = logging.getLogger(__name__)


def get_search_config(request: Request) -> SearchConfig:
    config: SearchConfig | None = getattr(request.app.state, "search_config", None)
    if config is None:
        config = SearchConfig()
        request.app.state.search_config = config
    return config


class GraphQLAnswerError(Exception):
    """WCL answered with a GraphQL error (an unknown guild or character, for one): the request was made and counted."""


async def _run(client: Any, gql: str, variables: dict[str, Any], provider: TokenProvider | None) -> dict[str, Any]:
    if provider is not None:
        try:
            return await query_wcl(client, gql, variables, provider)
        except (UserTokenRejectedError, WclSignInRequiredError):
            logger.info("The user's WCL token was not usable for a search; using the app's")
    return await query_wcl(client, gql, variables)


async def wcl_request(
    request: Request,
    user: User | None,
    budget: BudgetManager,
    now: Clock,
    *,
    group: str,
    gql: str,
    variables: dict[str, Any],
    points: float,
    database_points: float = 0.0,
    user_token: bool = True,
) -> dict[str, Any]:
    """
    One search request: reserve ``points`` (and ``database_points``) from the budget, ask WCL on the caller's token
    (when ``user_token`` and they have one) else the app's, and settle the reservation.

    Raises:
        BudgetWaitError: No points free now, or WCL refused for lack of them.
        GraphQLAnswerError: WCL answered with a GraphQL error.
        ApiError: 503 ``wcl_unavailable`` when WCL could not be reached.
    """
    service = auth_service(request)
    assert service is not None  # mounted with sign-in only
    provider = await _user_provider(request, user) if user_token else None
    ask = Ask(
        job_id=f"search:{uuid.uuid4().hex}",
        report_code=group,  # not a report: names what is searched, so the budget's round-robin groups it
        user_id=user.id if user is not None else None,
        user_token=provider is not None,
        points=points,
        database_points=database_points,
        asked_at=now(),
    )
    admission = await budget.request(ask)
    if ask.job_id not in admission.granted:
        wait = admission.waits.get(ask.job_id)
        await budget.release(ask.job_id)  # a search never waits in line: the client is told when to come back
        raise BudgetWaitError(max(0.0, (wait.eta - ask.asked_at).total_seconds()) if wait else RATE_LIMITED_WAIT_S)
    answered = False
    try:
        async with service.wcl_client() as client:
            data = await _run(client, gql, variables, provider)
        answered = True
        return data
    except WarcraftLogsRateLimitError:
        answered = True
        raise BudgetWaitError(RATE_LIMITED_WAIT_S) from None
    except WarcraftLogsAPIError as exc:
        if exc.errors:
            answered = True
            raise GraphQLAnswerError(str(exc)) from None
        raise ApiError(503, "wcl_unavailable", "Warcraft Logs could not be reached. Try again shortly.") from None
    finally:
        if answered:
            await budget.settle(ask.job_id, None)
        else:
            await budget.release(ask.job_id)


def budget_response(wait: BudgetWaitError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(max(1, math.ceil(wait.wait_s))), "Cache-Control": "no-store"},
        content=BudgetWaitBody(
            code="budget_exhausted",
            message="Warcraft Logs has no points to spare right now. Try again shortly.",
            wait_s=wait.wait_s,
        ).model_dump(),
    )


__all__ = [
    "GraphQLAnswerError",
    "budget_response",
    "get_search_config",
    "wcl_request",
]
