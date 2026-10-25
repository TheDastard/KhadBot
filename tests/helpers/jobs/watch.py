"""
tests/helpers/jobs/watch.py

Pieces for the live report watch tests: a WCL stub whose report gains fights over time (and records which token
asked), a token resolver with a switch for a lost sign-in, and a queue wired the way the worker wires it, on
procrastinate's in-memory connector with a clock the test moves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from procrastinate.testing import InMemoryConnector

from helpers.encounter.synth import FIXTURES
from helpers.jobs.budget import MovableClock
from helpers.jobs.synth_queue import SynthEnv
from khadbot.config import QueueConfig, WatchConfig
from khadbot.jobs.budget_store import BudgetManager
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.run import UserAuthUnavailableError
from khadbot.jobs.specs import Auth, UserAuth
from khadbot.jobs.status import JobStore, MemoryJobBackend
from khadbot.jobs.watch_store import MemoryWatchBackend, WatchStore
from khadbot.wcl.client import WarcraftLogsAPIError

FAKE_TOKEN = "fake-user-token"
QUEUE_CONFIG = QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.01)
SYNTH_META = json.loads((Path(FIXTURES) / "wcl" / "SYNTH01" / "report.json").read_text(encoding="utf-8"))


def fight(fight_id: int, *, encounter: int = 90001, start: int = 0, end: int | None = None) -> dict[str, Any]:
    """A fight entry of the report metadata; ``end`` defaults to a minute after ``start``, ``end=start`` is running."""
    base = dict(SYNTH_META["fights"][0])
    end_ms = start + 60000 if end is None else end
    return {**base, "id": fight_id, "encounterID": encounter, "startTime": start, "endTime": end_ms}


class StubWcl:
    """The report metadata WCL would answer, with the fights visible so far. Counts and records every request."""

    def __init__(self, fights: list[dict[str, Any]] | None = None) -> None:
        self.fights = list(fights if fights is not None else [])
        self.tokens: list[str | None] = []
        self.missing = False  # WCL answers "no such report"
        self.error: Exception | None = None  # raised instead of an answer
        self.spent = 0.0

    @property
    def requests(self) -> int:
        return len(self.tokens)

    async def query(self, gql: str, variables: dict | None = None, *, user_token: str | None = None) -> dict:
        self.tokens.append(user_token)
        if self.error is not None:
            raise self.error
        if self.missing:
            return {"reportData": {"report": None}}
        return {"reportData": {"report": {**SYNTH_META, "fights": list(self.fights)}}}

    async def rate_limit(self, *, user_token: str | None = None) -> dict:
        self.spent += 1.0
        return {"pointsSpentThisHour": self.spent}


@dataclass
class FakeResolver:
    """Resolves a user's auth to a fake token; ``signed_out`` makes it refuse, as for a revoked sign-in."""

    signed_out: bool = False
    asked: list[str] = field(default_factory=list)

    async def provider_for(self, auth: Auth):
        if not isinstance(auth, UserAuth):
            return None
        if self.signed_out:
            raise UserAuthUnavailableError("signed out")
        self.asked.append(auth.user_id)

        async def provider() -> str:
            return FAKE_TOKEN

        return provider


@dataclass
class WatchEnv:
    queue: JobQueue
    jobs: MemoryJobBackend
    connector: InMemoryConnector
    watch_rows: MemoryWatchBackend
    wcl: StubWcl
    resolver: FakeResolver
    clock: MovableClock
    env: SynthEnv

    @property
    def service(self):
        return self.queue.watches

    def ingests(self) -> list:
        return sorted((r for r in self.jobs.rows.values() if r.kind.value == "ingest"), key=lambda r: r.created_at)


def make_watch_env(
    env: SynthEnv,
    *,
    fights: list[dict[str, Any]] | None = None,
    config: WatchConfig | None = None,
    budget: BudgetManager | None = None,
) -> WatchEnv:
    clock = MovableClock()
    wcl = StubWcl(fights)
    resolver = FakeResolver()
    env.context.client = wcl  # type: ignore[assignment]
    env.context.resolver = resolver
    jobs = MemoryJobBackend()
    rows = MemoryWatchBackend()
    connector = InMemoryConnector()
    queue = JobQueue(
        JobStore(jobs),
        connector,
        config=QUEUE_CONFIG,
        context_factory=lambda: env.context,
        budget=budget,
        watches=WatchStore(rows),
        watch_config=config or WatchConfig(poll_interval_s=180, quiet_period_s=2700, max_length_s=21600),
    )
    queue.watches.clock = clock
    return WatchEnv(queue, jobs, connector, rows, wcl, resolver, clock, env)


def api_error() -> WarcraftLogsAPIError:
    return WarcraftLogsAPIError("WCL is having a moment")
