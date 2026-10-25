"""
jobs/budget_store.py

Where the WCL budget's state lives and how it changes: ``BudgetManager`` applies the pure policy in ``jobs/budget.py``
inside one transaction per change, so two workers deciding at once see each other's reservations and can never
over-reserve the bucket. It also reads WCL's ``rateLimitData`` to keep the bucket in step with WCL's own count.

The state is one document (``BudgetState``): the bucket, this window's reservations and the waiting jobs. Hosted
mode keeps it in the ``wcl_budget`` row (migration 0006) and changes it under ``SELECT ... FOR UPDATE``;
``MemoryBudgetBackend`` keeps it in memory under a lock, for unit tests and tools. The policy never sees which one.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, LiteralString, Protocol

from khadbot.config import BudgetConfig, WarcraftLogsConfig
from khadbot.jobs import budget
from khadbot.jobs.budget import Admission, Ask, BudgetState, BudgetView, Wait

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

# ---------------------------------------------------------------------------
# Reading WCL's count
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitReading:
    """One ``rateLimitData`` read: ``limitPerHour``, ``pointsSpentThisHour`` and ``pointsResetIn`` (seconds)."""

    limit_per_hour: float
    spent: float
    reset_in_s: float

    @classmethod
    def parse(cls, data: dict[str, Any]) -> RateLimitReading:
        return cls(
            limit_per_hour=float(data["limitPerHour"]),
            spent=float(data["pointsSpentThisHour"]),
            reset_in_s=float(data["pointsResetIn"]),
        )


RateLimitReader = Callable[[], Awaitable[RateLimitReading]]


async def read_app_rate_limit() -> RateLimitReading:
    """Read the app's ``rateLimitData`` with its own client credentials (about 1 point)."""
    from khadbot.wcl.client import WarcraftLogsClient

    wcl = WarcraftLogsConfig()
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret) as client:
        return RateLimitReading.parse(await client.rate_limit())


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class BudgetTx(Protocol):
    def load(self) -> BudgetState | None: ...

    def save(self, state: BudgetState) -> None: ...


class BudgetBackend(Protocol):
    def transaction(self) -> AbstractContextManager[BudgetTx]:
        """A transaction that holds the budget's lock until it ends."""
        ...

    def guild_of(self, report_code: str) -> str | None:
        """The report's WCL guild, when the read models know it."""
        ...


class MemoryBudgetBackend:
    """The budget state in memory, one writer at a time. ``guilds`` maps report codes to WCL guild IDs."""

    def __init__(self, guilds: dict[str, str] | None = None) -> None:
        self.state: BudgetState | None = None
        self.guilds = dict(guilds or {})
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator[BudgetTx]:
        with self._lock:
            yield _MemoryTx(self)

    def guild_of(self, report_code: str) -> str | None:
        return self.guilds.get(report_code)


class _MemoryTx:
    def __init__(self, backend: MemoryBudgetBackend) -> None:
        self._backend = backend

    def load(self) -> BudgetState | None:
        return self._backend.state

    def save(self, state: BudgetState) -> None:
        self._backend.state = state


_ENSURE: LiteralString = (
    "INSERT INTO wcl_budget (bucket, state, updated_at) VALUES ('app', NULL, now()) ON CONFLICT (bucket) DO NOTHING"
)
_LOAD: LiteralString = "SELECT state FROM wcl_budget WHERE bucket = 'app' FOR UPDATE"
_SAVE: LiteralString = "UPDATE wcl_budget SET state = %s, updated_at = now() WHERE bucket = 'app'"
_GUILD: LiteralString = "SELECT guild_id FROM reports WHERE code = %s"


class PostgresBudgetBackend:
    """The budget state in the ``wcl_budget`` row, locked with ``FOR UPDATE`` for the length of a transaction."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self) -> Iterator[BudgetTx]:
        with self._pool.connection() as conn:  # commits when the block ends, rolls back if it raises
            conn.execute(_ENSURE)
            yield _PostgresTx(conn)

    def guild_of(self, report_code: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute(_GUILD, (report_code,)).fetchone()
        return str(row[0]) if row and row[0] is not None else None


class _PostgresTx:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def load(self) -> BudgetState | None:
        row = self._conn.execute(_LOAD).fetchone()
        return BudgetState.model_validate(row[0]) if row and row[0] is not None else None

    def save(self, state: BudgetState) -> None:
        from psycopg.types.json import Jsonb

        self._conn.execute(_SAVE, (Jsonb(state.model_dump(mode="json")),))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BudgetManager:
    """
    The budget as the queue and the API use it. Every change is one transaction: load the state, apply the policy
    at the clock's ``now``, save. Calls run on a worker thread, so a worker's event loop never waits on the lock.

    Args:
        backend: Where the state lives.
        config: Allowances, headroom and the cost estimate.
        clock: The time; tests inject one.
        reader: Reads WCL's ``rateLimitData``; None turns syncing off.
    """

    def __init__(
        self,
        backend: BudgetBackend,
        config: BudgetConfig | None = None,
        *,
        clock: Clock = _utc_now,
        reader: RateLimitReader | None = read_app_rate_limit,
    ) -> None:
        self.backend = backend
        self.config = config or BudgetConfig()
        self._clock = clock
        self._reader = reader

    async def _change[T](self, fn: Callable[[BudgetState, datetime], tuple[BudgetState, T]]) -> T:
        def work() -> T:
            with self.backend.transaction() as tx:
                state = tx.load() or budget.initial_state(self.config)
                new, out = fn(state, self._clock())
                tx.save(new)
                return out

        return await asyncio.to_thread(work)

    async def state(self) -> BudgetState:
        """The state brought up to now (not saved)."""
        return await self._change(lambda s, now: (s, budget.roll(s, now, self.config)))

    async def request(self, ask: Ask) -> Admission:
        """Put a job in line and grant what fits; ``ask.job_id in result.granted`` means it may run now."""

        def fn(state: BudgetState, now: datetime) -> tuple[BudgetState, Admission]:
            out = budget.admit(state, now, self.config, ask)
            return out.state, out

        return await self._change(fn)

    async def admit_waiting(self) -> Admission:
        """Grant the waiting jobs that fit now (after a settlement, a sync or a new window)."""

        def fn(state: BudgetState, now: datetime) -> tuple[BudgetState, Admission]:
            out = budget.admit(state, now, self.config)
            return out.state, out

        return await self._change(fn)

    async def settle(self, job_id: str, actual: float | None) -> None:
        """Charge a finished job its measured spend (the estimate when ``actual`` is None) and close its reservation."""
        await self._change(lambda s, now: (budget.settle(s, job_id, actual, now, self.config), None))

    async def release(self, job_id: str) -> None:
        """Drop a job's reservation uncharged, and its place in line: it will not run."""
        await self._change(lambda s, now: (budget.forget(budget.release(s, job_id), job_id), None))

    async def wait_of(self, job_id: str) -> Wait | None:
        """Where a waiting job stands: its place in line and ETA. None when it is not waiting."""

        def fn(state: BudgetState, now: datetime) -> tuple[BudgetState, Wait | None]:
            rolled = budget.roll(state, now, self.config)
            return rolled, budget.places(rolled, now, self.config).get(job_id)

        return await self._change(fn)

    async def view(self, *, user_id: str, user_token: bool, guild_id: str | None = None) -> BudgetView:
        """The budget as one user sees it."""

        def fn(state: BudgetState, now: datetime) -> tuple[BudgetState, BudgetView]:
            rolled = budget.roll(state, now, self.config)
            out = budget.view(rolled, now, self.config, user_id=user_id, user_token=user_token, guild_id=guild_id)
            return rolled, out

        return await self._change(fn)

    async def sync(self) -> bool:
        """Read ``rateLimitData`` and take WCL's count. False (logged) when it can't be read; the budget carries on."""
        if self._reader is None:
            return False
        try:
            reading = await self._reader()
        except Exception:  # noqa: BLE001 - a failed read must never stop a worker; the local count stands
            logger.warning("Could not read WCL's rate limit; keeping the budget's own count", exc_info=True)
            return False

        def fn(state: BudgetState, now: datetime) -> tuple[BudgetState, None]:
            return (
                budget.sync(
                    state,
                    limit_per_hour=reading.limit_per_hour,
                    spent=reading.spent,
                    reset_in_s=reading.reset_in_s,
                    now=now,
                    cfg=self.config,
                ),
                None,
            )

        await self._change(fn)
        return True

    async def guild_of(self, report_code: str) -> str | None:
        return await asyncio.to_thread(self.backend.guild_of, report_code)


def build_budget_manager(pool: ConnectionPool | None = None, config: BudgetConfig | None = None) -> BudgetManager:
    """The hosted budget on the configured Postgres, syncing with the app's own WCL credentials."""
    from khadbot.db import get_pool

    return BudgetManager(PostgresBudgetBackend(pool or get_pool()), config or BudgetConfig())


__all__ = [
    "BudgetBackend",
    "BudgetManager",
    "BudgetTx",
    "MemoryBudgetBackend",
    "PostgresBudgetBackend",
    "RateLimitReader",
    "RateLimitReading",
    "build_budget_manager",
    "read_app_rate_limit",
]
