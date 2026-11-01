"""
api/raids/lookup.py

``GET /v1/lookup/report?source=<code or URL>``: a report's boss pulls, grouped by encounter and difficulty, straight
from WCL's report metadata, before anything is loaded. Hosted only: ``create_app`` mounts this router with the
sign-in routes and sets ``features.report_lookup``.

The answer comes from the same metadata ingest reads (``GET_REPORT_META``), through the raw page cache
(``raw/<code>/report.json``), so a lookup followed by opening a pull spends no second metadata request.

- **Access first.** The caller must be able to read the report (``deps.require_readable``) before anything cached is
  served: a private report's cached metadata reaches only callers who may read it, and an unreadable or missing
  report answers 404 ``report_not_found`` with one message.
- **How long a cached answer stands** (``config.LookupConfig``): a log that was already finished when it was fetched
  stands for good; one that may still be live stands ``meta_max_age_s``, then the next lookup fetches it again so
  new pulls appear. Concurrent lookups of one report share one fetch.
- **Whose token.** The signed-in user's own WCL token when they have one, else the app's (public reports; a visitor
  can look up a public log). A refused user token falls back to the app's.
- **Budget.** A fetch reserves ``LookupConfig.points`` from the WCL budget like any other request. With no points
  free the route answers 429 ``budget_exhausted`` with ``wait_s`` and ``Retry-After``; nothing is queued.
- **Ready.** A pull is ``ready`` when its digest is written (the read model has it), so a client can open it without
  loading it first.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from khadbot.api.budget import get_budget
from khadbot.api.deps import Caller, auth_service, require_readable
from khadbot.api.night import read_source
from khadbot.api.routes import get_context
from khadbot.api.schemas import (
    BudgetWaitBody,
    DifficultyName,
    ErrorBody,
    LookupGroup,
    LookupPull,
    LookupZone,
    ReportLookup,
)
from khadbot.api.views import ApiContext, ApiError, difficulty_name, group_pulls
from khadbot.auth.clock import Clock, utc_now
from khadbot.auth.errors import WclSignInRequiredError
from khadbot.auth.store import User
from khadbot.config import LookupConfig
from khadbot.encounter.ingest.fetch import (
    FETCHED_AT_KEY,
    FETCHED_WITH_KEY,
    RawPageCache,
    TokenProvider,
    UserTokenRejectedError,
    query_wcl,
)
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.encounter.ingest.wcl_queries import GET_REPORT_META
from khadbot.encounter.models import Pull
from khadbot.jobs.budget import Ask
from khadbot.jobs.budget_store import BudgetManager
from khadbot.wcl.client import (
    WarcraftLogsAPIError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
    WarcraftLogsRateLimitError,
)
from khadbot.wcl.reports import ReportSource, parse_report_source

logger = logging.getLogger(__name__)

NOT_FOUND_MESSAGE = "Report not found or not shared with your Warcraft Logs account."
RATE_LIMITED_WAIT_S = 60.0  # what a client is told when WCL itself refused and did not say when to come back
MAX_LOCKS = 256

# ---------------------------------------------------------------------------
# Settings, clock and locks
# ---------------------------------------------------------------------------


def get_lookup_config(request: Request) -> LookupConfig:
    config: LookupConfig | None = getattr(request.app.state, "lookup_config", None)
    if config is None:
        config = LookupConfig()
        request.app.state.lookup_config = config
    return config


def get_clock(request: Request) -> Clock:
    clock: Clock | None = getattr(request.app.state, "lookup_clock", None)
    return clock if clock is not None else utc_now


def _lock_for(request: Request, code: str) -> asyncio.Lock:
    """The lock that makes concurrent lookups of one report share a fetch. Least recently used ones are dropped."""
    locks: OrderedDict[str, asyncio.Lock] | None = getattr(request.app.state, "lookup_locks", None)
    if locks is None:
        locks = OrderedDict()
        request.app.state.lookup_locks = locks
    lock = locks.setdefault(code, asyncio.Lock())
    locks.move_to_end(code)
    while len(locks) > MAX_LOCKS:
        oldest = next(iter(locks))
        if locks[oldest].locked():
            break
        del locks[oldest]
    return lock


Settings = Annotated[LookupConfig, Depends(get_lookup_config)]
Now = Annotated[Clock, Depends(get_clock)]
Ctx = Annotated[ApiContext, Depends(get_context)]
Budget = Annotated[BudgetManager, Depends(get_budget)]

# ---------------------------------------------------------------------------
# Cached metadata
# ---------------------------------------------------------------------------


def _fetched_at(meta: dict[str, Any]) -> datetime | None:
    value = meta.get(FETCHED_AT_KEY)
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def last_activity(meta: dict[str, Any]) -> datetime | None:
    """When the report's last fight ended (its start when it has none yet); None without a usable start time."""
    start = meta.get("startTime")
    if not isinstance(start, int | float):
        return None
    ends = [f["endTime"] for f in meta.get("fights") or [] if isinstance(f.get("endTime"), int | float)]
    return datetime.fromtimestamp((start + max(ends, default=0)) / 1000, UTC)


def is_fresh(meta: dict[str, Any], now: datetime, config: LookupConfig) -> bool:
    """
    Whether cached metadata can answer a lookup now.

    Metadata fetched at least ``settled_after_s`` after the log's last fight is of a finished log and stands for good.
    Otherwise it stands for ``meta_max_age_s`` from when it was fetched. Metadata without a fetch time (older caches,
    recorded fixtures) is of unknown age: it is fetched again once, and stamped.
    """
    fetched = _fetched_at(meta)
    if fetched is None:
        return False
    activity = last_activity(meta)
    if activity is not None and fetched - activity >= timedelta(seconds=config.settled_after_s):
        return True
    return now - fetched < timedelta(seconds=config.meta_max_age_s)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


class BudgetWaitError(Exception):
    """The budget has no points for a lookup now (or WCL refused for lack of them): try again in ``wait_s``."""

    def __init__(self, wait_s: float) -> None:
        super().__init__("no points free")
        self.wait_s = wait_s


def _not_found() -> ApiError:
    return ApiError(404, "report_not_found", NOT_FOUND_MESSAGE)


async def _user_provider(request: Request, user: User | None) -> TokenProvider | None:
    """The user's own WCL token as a provider (a second ask forces a refresh), or None for the app token."""
    service = auth_service(request)
    if service is None or user is None or not await service.tokens.has_tokens(user.id):
        return None
    asked = 0

    async def provider() -> str:
        nonlocal asked
        asked += 1
        return await service.tokens.access_token(user.id, force_refresh=asked > 1)

    return provider


async def _query(client: WarcraftLogsClient, code: str, provider: TokenProvider | None) -> tuple[dict[str, Any], str]:
    """The report metadata on the user's token (when there is a usable one), else on the app's; and which it was."""
    if provider is not None:
        try:
            return await query_wcl(client, GET_REPORT_META, {"code": code}, provider), "user"
        except (UserTokenRejectedError, WclSignInRequiredError):
            logger.info("The user's WCL token was not usable for a lookup; using the app's")
    return await query_wcl(client, GET_REPORT_META, {"code": code}), "app"


async def _fetch(
    request: Request,
    code: str,
    user: User | None,
    cache: RawPageCache,
    budget: BudgetManager,
    config: LookupConfig,
    now: Clock,
) -> dict[str, Any]:
    """Reserve the points, ask WCL for the metadata and cache it with who fetched it (a kind of token) and when."""
    service = auth_service(request)
    assert service is not None  # the route is mounted with sign-in only
    provider = await _user_provider(request, user)
    ask = Ask(
        job_id=f"lookup:{uuid.uuid4().hex}",
        report_code=code,
        user_id=user.id if user is not None else None,
        user_token=provider is not None,
        points=config.points,
        asked_at=now(),
    )
    admission = await budget.request(ask)
    if ask.job_id not in admission.granted:
        wait = admission.waits.get(ask.job_id)
        await budget.release(ask.job_id)  # a lookup never waits in line: the client is told when to come back
        raise BudgetWaitError(max(0.0, (wait.eta - ask.asked_at).total_seconds()) if wait else RATE_LIMITED_WAIT_S)
    answered = False  # whether WCL got the request and counted it
    try:
        async with service.wcl_client() as client:
            data, fetched_with = await _query(client, code, provider)
        answered = True
    except WarcraftLogsRateLimitError:
        answered = True
        raise BudgetWaitError(RATE_LIMITED_WAIT_S) from None
    except WarcraftLogsPrivateReportError:
        answered = True
        raise _not_found() from None
    except WarcraftLogsAPIError as exc:
        if exc.errors:  # a GraphQL answer about this report: missing, or not readable after all
            answered = True
            raise _not_found() from None
        raise ApiError(503, "wcl_unavailable", "Warcraft Logs could not be reached. Try again shortly.") from None
    finally:
        if answered:
            await budget.settle(ask.job_id, None)
        else:
            await budget.release(ask.job_id)
    report = (data.get("reportData") or {}).get("report")
    if not isinstance(report, dict):
        raise _not_found()
    meta = {**report, FETCHED_WITH_KEY: fetched_with, FETCHED_AT_KEY: now().isoformat()}
    await asyncio.to_thread(cache.write_report_meta, code, meta)
    return meta


async def report_metadata(
    request: Request,
    code: str,
    user: User | None,
    ctx: ApiContext,
    budget: BudgetManager,
    config: LookupConfig,
    now: Clock,
) -> dict[str, Any]:
    """The report's metadata: cached while fresh (``is_fresh``), else fetched. The caller has already been checked."""
    cache = ctx.raw
    if cache is None:
        raise ApiError(503, "lookup_unavailable", "Report lookup has no report cache to use.")

    async def cached() -> dict[str, Any] | None:
        meta = await asyncio.to_thread(cache.report_meta, code)
        return meta if meta is not None and is_fresh(meta, now(), config) else None

    if (meta := await cached()) is not None:
        return meta
    async with _lock_for(request, code):
        if (meta := await cached()) is not None:  # another lookup fetched it while this one waited
            return meta
        return await _fetch(request, code, user, cache, budget, config, now)


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def _pct(value: float | None) -> float:
    return value if value is not None else 101.0  # no figure ranks last


def best_fight(pulls: list[Pull]) -> int:
    """A group's best pull: its first kill, else the wipe with the lowest fight % left (then boss health, then time)."""
    for p in pulls:
        if p.kill:
            return p.fight_id
    return min(pulls, key=lambda p: (_pct(p.boss_pct), _pct(p.boss_hp_pct), p.start_ms, p.fight_id)).fight_id


def ready_fights(ctx: ApiContext, code: str) -> set[int]:
    """The fights of ``code`` whose digest is written: the read model's pulls, else the stored ones (tests)."""
    try:
        with ctx.lock:
            if ctx.read_source is not None:
                return {f.fight_id for f in read_source(ctx).pulls(code)}
            return {p.fight_id for p in ctx.pulls(code)}
    except ApiError:  # nothing of the report is loaded
        return set()


def build_lookup(code: str, meta: dict[str, Any], focus: int | None, ready: set[int]) -> ReportLookup:
    """The lookup answer for report metadata: boss pulls in time order, grouped by encounter and difficulty."""
    pulls, _ = parse_report_meta(code, meta)
    bosses = sorted((p for p in pulls if p.encounter_id != 0), key=lambda p: (p.start_ms, p.fight_id))
    groups = []
    for (encounter_id, difficulty), members in group_pulls(bosses):
        diff_name: DifficultyName | None = difficulty_name(difficulty)
        groups.append(
            LookupGroup(
                encounter_id=encounter_id,
                encounter_name=members[0].encounter_name,
                difficulty=difficulty,
                difficulty_name=diff_name,
                pulls=[
                    LookupPull(
                        fight=p.fight_id,
                        ordinal=i,
                        label=f"P{i}",
                        outcome="kill" if p.kill else "wipe",
                        duration_ms=p.duration_ms,
                        boss_pct=p.boss_pct,
                        boss_hp_pct=p.boss_hp_pct,
                        ready=p.fight_id in ready,
                    )
                    for i, p in enumerate(members, start=1)
                ],
                best_fight=best_fight(members),
            )
        )
    zone = meta.get("zone")
    title = meta.get("title")
    return ReportLookup(
        code=code,
        title=title if isinstance(title, str) else None,
        started_at=datetime.fromtimestamp(float(meta.get("startTime") or 0) / 1000, UTC),
        zone=LookupZone(id=zone["id"], name=zone.get("name") or "")
        if isinstance(zone, dict) and zone.get("id") is not None
        else None,
        focus_fight=focus if focus is not None and any(p.fight_id == focus for p in bosses) else None,
        groups=groups,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/v1/lookup", tags=["lookup"])


@router.get(
    "/report",
    response_model=ReportLookup,
    responses={
        400: {"model": ErrorBody, "description": "Not a report code or URL (bad_source)"},
        422: {"model": ErrorBody, "description": "Missing or invalid query parameter (bad_request)"},
        404: {"model": ErrorBody, "description": "Report missing or not readable by the caller (report_not_found)"},
        429: {"model": BudgetWaitBody, "description": "No WCL points free now (budget_exhausted); see wait_s"},
        503: {"model": ErrorBody, "description": "Warcraft Logs could not answer (wcl_unavailable)"},
    },
)
async def lookup_report(
    request: Request,
    response: Response,
    user: Caller,
    ctx: Ctx,
    budget: Budget,
    config: Settings,
    now: Now,
    source: Annotated[str, Query(min_length=1, max_length=500, description="A report code or warcraftlogs.com URL")],
) -> Any:
    """
    A report's boss pulls from Warcraft Logs, grouped by encounter and difficulty, before any is loaded. A URL with
    ``#fight=N`` also names ``focus_fight``. The caller must be able to read the report.
    """
    try:
        parsed: ReportSource = parse_report_source(source)
    except ValueError:
        raise ApiError(
            400, "bad_source", "Enter a Warcraft Logs report code or a warcraftlogs.com report URL."
        ) from None
    await require_readable(request, parsed.code)
    try:
        meta = await report_metadata(request, parsed.code, user, ctx, budget, config, now)
    except BudgetWaitError as wait:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(max(1, math.ceil(wait.wait_s))), "Cache-Control": "no-store"},
            content=BudgetWaitBody(
                code="budget_exhausted",
                message="Warcraft Logs has no points to spare right now. Try again shortly.",
                wait_s=wait.wait_s,
            ).model_dump(),
        )
    ready = await run_in_threadpool(ready_fights, ctx, parsed.code)
    response.headers["Cache-Control"] = "private, no-store"
    return build_lookup(parsed.code, meta, parsed.fight, ready)


__all__ = ["BudgetWaitError", "build_lookup", "is_fresh", "router"]
