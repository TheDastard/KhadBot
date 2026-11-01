"""
api/raids/nights.py

The nights a guild or character search lists, each in the report lookup's shape (``lookup.build_lookup``), built from
the same cached report metadata as a lookup. Every listed report passes the report access check
(``ReportAccess.can_read``) on every read before its metadata is served, so a private report never reaches a caller
who can't read it; a report the caller can't read is left out and counted, not an error. A character's nights keep
only the boss pulls a player of that name took part in (``character_actor_ids``, ``only_pulls_of``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.concurrency import run_in_threadpool

from khadbot.api.deps import auth_service
from khadbot.api.raids.lookup import (
    build_lookup,
    ready_fights,
    report_metadata,
)
from khadbot.api.schemas import (
    LookupGroup,
    ReportLookup,
)
from khadbot.api.views import ApiContext, ApiError
from khadbot.auth.clock import Clock
from khadbot.auth.errors import WclUnavailableError
from khadbot.auth.store import User
from khadbot.config import LookupConfig
from khadbot.jobs.budget_store import BudgetManager
from khadbot.wcl.search import (
    ListedReport,
)

logger = logging.getLogger(__name__)

NIGHT_CONCURRENCY = 4  # report metadata requests one search has in flight


@dataclass
class Nights:
    nights: list[ReportLookup]
    left_out: int


async def build_nights(
    request: Request,
    user: User | None,
    ctx: ApiContext,
    budget: BudgetManager,
    lookup: LookupConfig,
    now: Clock,
    reports: list[ListedReport],
    keep: Callable[[str, dict[str, Any], ReportLookup], ReportLookup | None] | None = None,
) -> Nights:
    """
    The nights of ``reports`` the caller may read, newest first, each built from its report metadata (cached, or
    fetched like a report lookup's). Nights without a boss pull are dropped. ``keep`` may narrow or drop a night.

    Raises:
        BudgetWaitError: The budget ran out before every night was loaded.
    """
    service = auth_service(request)
    assert service is not None
    user_id = user.id if user is not None else None
    gate = asyncio.Semaphore(NIGHT_CONCURRENCY)

    async def one(report: ListedReport) -> tuple[ReportLookup | None, bool]:
        """The night (None when it has nothing to show) and whether it was left out (unreadable or not loadable)."""
        async with gate:
            try:
                if not await service.access.can_read(user_id, report.code):
                    return None, True
                meta = await report_metadata(request, report.code, user, ctx, budget, lookup, now)
            except (WclUnavailableError, ApiError) as exc:
                if isinstance(exc, ApiError) and exc.status not in (404, 503):
                    raise
                logger.info("Search left out report %s: %s", report.code, exc)
                return None, True
        ready = await run_in_threadpool(ready_fights, ctx, report.code)
        night: ReportLookup | None = build_lookup(report.code, meta, None, ready)
        if keep is not None and night is not None:
            night = keep(report.code, meta, night)
        return (night if night is not None and night.groups else None), False

    built = await asyncio.gather(*(one(r) for r in reports))
    nights = [n for n, _ in built if n is not None]
    nights.sort(key=lambda n: (n.started_at, n.code), reverse=True)
    return Nights(nights, sum(1 for _, left in built if left))


def character_actor_ids(meta: dict[str, Any], name: str) -> set[int]:
    """
    The player actors of a report named ``name`` (any case). The report metadata gives no server per actor, so two
    same-named players from different servers in one report would both match; a raid rarely has them.
    """
    wanted = name.casefold()
    return {
        a["id"]
        for a in (meta.get("masterData") or {}).get("actors") or []
        if a.get("type") == "Player" and isinstance(a.get("id"), int) and str(a.get("name") or "").casefold() == wanted
    }


def only_pulls_of(night: ReportLookup, fights: set[int], encounter: int | None) -> ReportLookup:
    """``night`` with only ``fights`` (and only ``encounter``'s, if given); each group's best pull is recomputed."""
    groups: list[LookupGroup] = []
    for group in night.groups:
        if encounter is not None and group.encounter_id != encounter:
            continue
        pulls = [p for p in group.pulls if p.fight in fights]
        if not pulls:
            continue
        kill = next((p.fight for p in pulls if p.outcome == "kill"), None)
        best = kill if kill is not None else min(pulls, key=lambda p: 101.0 if p.boss_pct is None else p.boss_pct).fight
        groups.append(group.model_copy(update={"pulls": pulls, "best_fight": best}))
    return night.model_copy(update={"groups": groups})


__all__ = [
    "Nights",
    "build_nights",
    "character_actor_ids",
    "only_pulls_of",
]
