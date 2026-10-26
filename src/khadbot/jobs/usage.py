"""
jobs/usage.py

The usage ledger: one row per WCL spend or LLM call, with the user, WCL guild, report and job it was for. It answers
"what did this cost, and who spent it" (``main.py usage``).

- **WCL rows** are written when an ingest job settles (``JobQueue``), from the same measured ``IngestResult.points_spent``
  the WCL budget settles to, so the ledger and the budget agree. A job has at most one WCL row.
- **LLM rows** come from ``record_llm_usage``, which review and conversation jobs call after a model call with the
  call's token counts. The cost is estimated from the price table (``model_prices``); a model without a price is
  recorded with its tokens and a null cost.

The guild is null for now: nothing fills ``reports.guild_id`` yet. A row never holds a token or any secret.
``PostgresUsageLedger`` (migration 0008) is the hosted store; ``MemoryUsageLedger`` serves unit tests and tools.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, LiteralString, Protocol

from pydantic import BaseModel, ConfigDict, Field

from khadbot.jobs.model_prices import PriceTable, get_price_table

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

GroupBy = Literal["user", "guild", "kind"]
GROUPINGS: tuple[GroupBy, ...] = ("user", "guild", "kind")
NO_KEY = "(none)"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class UsageKind(StrEnum):
    WCL = "wcl"
    LLM = "llm"


class LlmUsage(BaseModel):
    """
    One model call's tokens. ``input_tokens`` counts every input token, cached ones included, as Anthropic and
    LangChain report it; ``cached_input_tokens`` were read from the prompt cache and ``cache_write_tokens`` written
    to it.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)

    @classmethod
    def from_usage_metadata(cls, meta: Mapping[str, Any]) -> LlmUsage:
        """Read a LangChain ``AIMessage.usage_metadata`` (``input_token_details`` carries the cache counts)."""
        details = meta.get("input_token_details") or {}
        return cls(
            input_tokens=int(meta.get("input_tokens") or 0),
            output_tokens=int(meta.get("output_tokens") or 0),
            cached_input_tokens=int(details.get("cache_read") or 0),
            cache_write_tokens=int(details.get("cache_creation") or 0),
        )


class UsageEntry(BaseModel):
    """One ledger row."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: UsageKind
    user_id: str | None = None
    guild_id: int | None = None
    report_code: str | None = None
    job_id: str | None = None
    wcl_points: float | None = None
    model: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class UsageTotal(BaseModel):
    """The ledger summed over one group."""

    model_config = ConfigDict(frozen=True)

    key: str
    calls: int
    wcl_points: float
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float


# ---------------------------------------------------------------------------
# Building entries
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def wcl_entry(
    points: float,
    *,
    job_id: str | None = None,
    user_id: str | None = None,
    report_code: str | None = None,
    guild_id: int | None = None,
    at: datetime | None = None,
) -> UsageEntry:
    return UsageEntry(
        at=at or _utc_now(),
        kind=UsageKind.WCL,
        user_id=user_id,
        guild_id=guild_id,
        report_code=report_code,
        job_id=job_id,
        wcl_points=points,
    )


def llm_entry(
    model: str,
    usage: LlmUsage,
    *,
    job_id: str | None = None,
    user_id: str | None = None,
    report_code: str | None = None,
    guild_id: int | None = None,
    prices: PriceTable | None = None,
    at: datetime | None = None,
) -> UsageEntry:
    """A model call's row, its cost estimated from ``prices`` (the default table). Null cost for an unpriced model."""
    cost = (prices or get_price_table()).estimate_cost(
        model,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
    )
    return UsageEntry(
        at=at or _utc_now(),
        kind=UsageKind.LLM,
        user_id=user_id,
        guild_id=guild_id,
        report_code=report_code,
        job_id=job_id,
        model=model,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=cost,
    )


# ---------------------------------------------------------------------------
# Ledgers
# ---------------------------------------------------------------------------


class UsageLedger(Protocol):
    def record(self, entry: UsageEntry) -> bool:
        """Write a row. False when it was a repeat: a job's WCL spend is written once."""
        ...

    def totals(self, since: datetime, by: GroupBy) -> list[UsageTotal]:
        """Rows at or after ``since`` summed by user, guild or kind, largest cost first."""
        ...


class MemoryUsageLedger:
    """The ledger in memory. ``guilds`` maps report codes to WCL guild IDs, as ``reports.guild_id`` does."""

    def __init__(self, guilds: dict[str, int] | None = None) -> None:
        self.entries: list[UsageEntry] = []
        self.guilds = dict(guilds or {})
        self._lock = threading.Lock()

    def record(self, entry: UsageEntry) -> bool:
        if entry.guild_id is None and entry.report_code in self.guilds:
            entry = entry.model_copy(update={"guild_id": self.guilds[entry.report_code]})
        with self._lock:
            if entry.kind is UsageKind.WCL and entry.job_id is not None:
                if any(e.kind is UsageKind.WCL and e.job_id == entry.job_id for e in self.entries):
                    return False
            self.entries.append(entry)
        return True

    def totals(self, since: datetime, by: GroupBy) -> list[UsageTotal]:
        groups: dict[str, list[UsageEntry]] = {}
        with self._lock:
            rows = [e for e in self.entries if e.at >= since]
        for e in rows:
            if by == "kind":
                key = e.kind.value
            elif by == "user":
                key = e.user_id or NO_KEY
            else:
                key = str(e.guild_id) if e.guild_id is not None else NO_KEY
            groups.setdefault(key, []).append(e)
        out = [
            UsageTotal(
                key=key,
                calls=len(es),
                wcl_points=sum(e.wcl_points or 0.0 for e in es),
                input_tokens=sum(e.input_tokens or 0 for e in es),
                cached_input_tokens=sum(e.cached_input_tokens or 0 for e in es),
                output_tokens=sum(e.output_tokens or 0 for e in es),
                cost_usd=sum(e.cost_usd or 0.0 for e in es),
            )
            for key, es in groups.items()
        ]
        return sorted(out, key=lambda t: (-t.cost_usd, -t.wcl_points, t.key))


_INSERT: LiteralString = """
INSERT INTO usage (at, user_id, guild_id, report_code, job_id, kind, wcl_points, model, input_tokens,
                   cached_input_tokens, cache_write_tokens, output_tokens, cost_usd)
VALUES (%(at)s, %(user_id)s, COALESCE(%(guild_id)s, (SELECT guild_id FROM reports WHERE code = %(report_code)s)),
        %(report_code)s, %(job_id)s, %(kind)s, %(wcl_points)s, %(model)s, %(input_tokens)s, %(cached_input_tokens)s,
        %(cache_write_tokens)s, %(output_tokens)s, %(cost_usd)s)
ON CONFLICT (job_id) WHERE kind = 'wcl' AND job_id IS NOT NULL DO NOTHING
"""

_TOTALS_BY_USER: LiteralString = """
SELECT COALESCE(user_id, '(none)') AS key, count(*), COALESCE(sum(wcl_points), 0), COALESCE(sum(input_tokens), 0),
       COALESCE(sum(cached_input_tokens), 0), COALESCE(sum(output_tokens), 0), COALESCE(sum(cost_usd), 0)
FROM usage WHERE at >= %s GROUP BY 1 ORDER BY 7 DESC, 3 DESC, 1
"""
_TOTALS_BY_GUILD: LiteralString = """
SELECT COALESCE(guild_id::text, '(none)') AS key, count(*), COALESCE(sum(wcl_points), 0), COALESCE(sum(input_tokens), 0),
       COALESCE(sum(cached_input_tokens), 0), COALESCE(sum(output_tokens), 0), COALESCE(sum(cost_usd), 0)
FROM usage WHERE at >= %s GROUP BY 1 ORDER BY 7 DESC, 3 DESC, 1
"""
_TOTALS_BY_KIND: LiteralString = """
SELECT kind AS key, count(*), COALESCE(sum(wcl_points), 0), COALESCE(sum(input_tokens), 0),
       COALESCE(sum(cached_input_tokens), 0), COALESCE(sum(output_tokens), 0), COALESCE(sum(cost_usd), 0)
FROM usage WHERE at >= %s GROUP BY 1 ORDER BY 7 DESC, 3 DESC, 1
"""
_TOTALS: dict[str, LiteralString] = {"user": _TOTALS_BY_USER, "guild": _TOTALS_BY_GUILD, "kind": _TOTALS_BY_KIND}


class PostgresUsageLedger:
    """The ``usage`` table (migration 0008). The guild comes from ``reports.guild_id`` when the caller gives none."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def record(self, entry: UsageEntry) -> bool:
        params = entry.model_dump()
        params["kind"] = entry.kind.value
        with self._pool.connection() as conn:
            return conn.execute(_INSERT, params).rowcount == 1

    def totals(self, since: datetime, by: GroupBy) -> list[UsageTotal]:
        with self._pool.connection() as conn:
            rows = conn.execute(_TOTALS[by], (since,)).fetchall()
        return [
            UsageTotal(
                key=r[0],
                calls=r[1],
                wcl_points=float(r[2]),
                input_tokens=int(r[3]),
                cached_input_tokens=int(r[4]),
                output_tokens=int(r[5]),
                cost_usd=float(r[6]),
            )
            for r in rows
        ]


# ---------------------------------------------------------------------------
# What jobs call
# ---------------------------------------------------------------------------


async def record_wcl_points(
    ledger: UsageLedger,
    points: float | None,
    *,
    job_id: str,
    user_id: str | None = None,
    report_code: str | None = None,
) -> bool:
    """Write an ingest job's measured WCL spend. Nothing is written for a job that spent none (offline, or unread)."""
    if not points or points <= 0:
        return False
    entry = wcl_entry(points, job_id=job_id, user_id=user_id, report_code=report_code)
    return await asyncio.to_thread(ledger.record, entry)


async def record_llm_usage(
    ledger: UsageLedger,
    *,
    model: str,
    usage: LlmUsage | Mapping[str, Any],
    job_id: str | None = None,
    user_id: str | None = None,
    report_code: str | None = None,
    guild_id: int | None = None,
    prices: PriceTable | None = None,
) -> UsageEntry:
    """
    Record one model call: the function review and conversation jobs call after each call.

    Args:
        ledger: Where to write.
        model: The model ID the provider reported (``response.response_metadata["model_name"]``).
        usage: The call's tokens, as ``LlmUsage`` or a LangChain ``usage_metadata`` mapping.
        job_id: The job the call belongs to, if any.
        user_id: Who it was for. An ID, never a token.
        report_code: The report it concerned.
        guild_id: The report's WCL guild when known; the ledger looks it up from the report otherwise.
        prices: The price table; the configured one by default.

    Returns:
        The row, with its estimated cost (None for a model without a price).
    """
    tokens = usage if isinstance(usage, LlmUsage) else LlmUsage.from_usage_metadata(usage)
    entry = llm_entry(
        model, tokens, job_id=job_id, user_id=user_id, report_code=report_code, guild_id=guild_id, prices=prices
    )
    await asyncio.to_thread(ledger.record, entry)
    return entry


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def format_usage_report(totals: list[UsageTotal], by: GroupBy, since: datetime) -> str:
    """The ``usage`` command's table: one line per group and a total."""
    head = (by, "calls", "wcl points", "input tok", "cached tok", "output tok", "est. cost")
    title = f"Usage since {since:%Y-%m-%d} by {by}"
    if not totals:
        return f"{title}\nNo usage recorded."

    def cells(key: str, rows: list[UsageTotal]) -> tuple[str, ...]:
        return (
            key,
            str(sum(t.calls for t in rows)),
            f"{sum(t.wcl_points for t in rows):,.1f}",
            f"{sum(t.input_tokens for t in rows):,}",
            f"{sum(t.cached_input_tokens for t in rows):,}",
            f"{sum(t.output_tokens for t in rows):,}",
            f"${sum(t.cost_usd for t in rows):,.4f}",
        )

    body = [cells(t.key, [t]) for t in totals]
    total = cells("total", totals)
    widths = [max(len(r[i]) for r in [head, total, *body]) for i in range(len(head))]

    def line(r: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(r, widths, strict=True)))

    return "\n".join([title, line(head), *[line(r) for r in body], "-" * len(line(head)), line(total)])


__all__ = [
    "GROUPINGS",
    "LlmUsage",
    "MemoryUsageLedger",
    "PostgresUsageLedger",
    "UsageEntry",
    "UsageKind",
    "UsageLedger",
    "UsageTotal",
    "format_usage_report",
    "llm_entry",
    "record_llm_usage",
    "record_wcl_points",
    "wcl_entry",
]
