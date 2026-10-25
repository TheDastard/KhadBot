"""
tests/helpers/jobs/budget.py

Pieces for the WCL budget tests: a clock the test moves, a ``rateLimitData`` reader that answers what the test says
(no network), and a WCL stub whose rate-limit counter moves by a set amount on every read, so an ingest measures a
known spend.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers.jobs.synth_queue import StubWclClient
from khadbot.config import BudgetConfig, WclLimitScheme
from khadbot.jobs.budget_store import RateLimitReading


class MovableClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class FakeReader:
    """Answers ``rateLimitData`` with ``reading``; ``fail`` makes it raise. Counts its reads."""

    def __init__(self, limit: float = 1000.0, spent: float = 0.0, reset_in_s: float = 1800.0) -> None:
        self.reading = RateLimitReading(limit, spent, reset_in_s)
        self.fail = False
        self.reads = 0

    async def __call__(self) -> RateLimitReading:
        self.reads += 1
        if self.fail:
            raise RuntimeError("fake WCL is down")
        return self.reading


class CountingWclClient(StubWclClient):
    """A WCL stub whose ``pointsSpentThisHour`` grows by ``step`` per read: an ingest measures ``step`` points."""

    def __init__(self, step: float) -> None:
        self.step = step
        self.spent = 0.0

    async def rate_limit(self, *, user_token: str | None = None) -> dict:
        value = self.spent
        self.spent += self.step
        return {"pointsSpentThisHour": value}


def budget_config(**overrides) -> BudgetConfig:
    """Explicit settings, so no test depends on the environment."""
    base = dict(
        scheme=WclLimitScheme.CURRENT,
        user_tokens_spend_app_budget=True,
        guild_share=0.25,
        user_share=0.10,
        headroom_share=0.20,
        points_per_pull=7.2,
        points_per_single_pull=14.0,
        points_per_job=4.0,
        default_pulls=25,
        database_points_per_hour=250.0,
        user_points_per_hour=None,
        reservation_ttl_s=7200,
    )
    return BudgetConfig(**{**base, **overrides})
