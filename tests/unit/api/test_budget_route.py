"""
tests/unit/api/test_budget_route.py

``GET /v1/budget``: mounted only with sign-in (hosted), 401 for a visitor, and for a signed-in user which token
their next ingest uses, whose points WCL charges, their allowance left and the current wait. The budget manager is
in memory with an injected clock; sign-in is replaced by dependency overrides.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers.jobs.budget import FakeReader, MovableClock, budget_config

from khadbot.api import create_app
from khadbot.auth.routes import current_user, optional_user
from khadbot.auth.store import User
from khadbot.config import WclLimitScheme
from khadbot.jobs.budget import Ask
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend

USER = User(id="u1", wcl_user_id=42, name="Tester")


class FakeTokens:
    def __init__(self, has: bool) -> None:
        self.has = has

    async def has_tokens(self, user_id: str) -> bool:
        return self.has


class FakeAuth:
    """Stands in for ``AuthService``: only what the budget route and ``mount`` touch."""

    def __init__(self, has_tokens: bool) -> None:
        self.tokens = FakeTokens(has_tokens)


def client_for(tmp_path, manager: BudgetManager, *, signed_in: bool = True, has_tokens: bool = True) -> TestClient:
    app = create_app(data_dir=tmp_path, auth=FakeAuth(has_tokens))  # type: ignore[arg-type]
    app.state.budget = manager
    app.dependency_overrides[optional_user] = lambda: USER if signed_in else None
    if signed_in:
        app.dependency_overrides[current_user] = lambda: USER
    return TestClient(app)


def manager(**cfg) -> tuple[BudgetManager, MovableClock]:
    clock = MovableClock()
    return BudgetManager(MemoryBudgetBackend(), budget_config(**cfg), clock=clock, reader=FakeReader()), clock


def test_local_mode_has_no_budget_route(tmp_path):
    assert TestClient(create_app(data_dir=tmp_path)).get("/v1/budget").status_code == 404


def test_a_visitor_gets_401(tmp_path):
    m, _ = manager()
    resp = client_for(tmp_path, m, signed_in=False).get("/v1/budget")
    assert resp.status_code == 401 and resp.json()["code"] == "not_signed_in"


def test_a_signed_in_wcl_user_fetches_with_their_token_on_the_shared_points(tmp_path):
    m, _ = manager()
    resp = client_for(tmp_path, m).get("/v1/budget")
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert (body["fetches_with"], body["charged_to"], body["scheme"]) == ("user", "shared", "current")
    assert body["limit_per_hour"] == 3600 and body["wait_s"] == 0 and body["waiting_jobs"] == 0
    assert body["allowance"] == {"limit": 360.0, "used": 0.0, "left": 360.0}


def test_a_user_without_a_wcl_token_uses_the_app_token(tmp_path):
    m, _ = manager()
    assert client_for(tmp_path, m, has_tokens=False).get("/v1/budget").json()["fetches_with"] == "app"


def test_a_known_user_pool_on_the_new_scheme_is_charged_first(tmp_path):
    m, _ = manager(scheme=WclLimitScheme.NEW, user_tokens_spend_app_budget=False, user_points_per_hour=50.0)
    assert client_for(tmp_path, m).get("/v1/budget").json()["charged_to"] == "user_first"


async def test_allowance_used_and_the_wait_show(tmp_path):
    m, clock = manager()
    await m.sync()  # 1000 points, window resets in 1800 s
    await m.request(Ask(job_id="a", report_code="R", user_id="u1", points=60, asked_at=clock()))
    body = client_for(tmp_path, m).get("/v1/budget").json()
    assert body["allowance"] == {"limit": 100.0, "used": 60.0, "left": 40.0}
    assert body["held"] == 60 and body["wait_s"] == 1800  # a 184-point night needs the allowance unused
