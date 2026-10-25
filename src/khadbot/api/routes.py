"""
api/routes.py

The read endpoints under ``/v1``. Every one is a GET that reads the event store, the authored files and the
findings cache; none calls WCL or an LLM. Pulls are addressed by WCL fight ID.

Handlers are plain functions (FastAPI runs them in its thread pool) and hold the context lock while they build
records, because the ``EncounterData`` LRU is shared.

In hosted mode every route that names a report depends on ``deps.readable_report`` (the evidence route on
``readable_ref``): the caller must be able to read the report, or the route answers 404 before it reads anything.
``GET /reports`` lists the reports the signed-in user opened or ingested. Local mode has no sign-in, so these
dependencies pass every request and the routes read exactly what they always did.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from khadbot.api.deps import Caller, Directory, readable_ref, readable_report
from khadbot.api.night import build_night, build_report_detail, read_source
from khadbot.api.schemas import (
    ErrorBody,
    EventsPage,
    Evidence,
    FindingsList,
    Health,
    NightSummary,
    PullTimeline,
    RefKind,
    ReportDetail,
    ReportList,
)
from khadbot.api.views import (
    ApiContext,
    ApiError,
    cached_findings,
    events_page,
    full_digest,
    report_info,
    timeline,
)
from khadbot.encounter.evidence import UnresolvableRef, parse_ref
from khadbot.encounter.models import PullDigest

REPORT_CAP = 200
FINDINGS_CAP = 50


def get_context(request: Request) -> ApiContext:
    ctx: ApiContext = request.app.state.ctx
    return ctx


Ctx = Annotated[ApiContext, Depends(get_context)]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "Report or pull not ingested, or a report the caller cannot read"},
    503: {"model": ErrorBody, "description": "Warcraft Logs could not answer the access check (wcl_unavailable)"},
}
_GATE = [Depends(readable_report)]

router = APIRouter(
    prefix="/v1", responses={422: {"model": ErrorBody, "description": "Invalid path or query parameter (bad_request)"}}
)


@router.get("/health", response_model=Health, tags=["meta"])
def health(ctx: Ctx, request: Request) -> Health:
    """
    Status, the data directory in use, the API version, and what this server offers: ``mode`` and one ``features``
    flag per optional area, true only when that area's routes are mounted. Clients branch on ``features``.
    """
    app = request.app
    return Health(
        data_dir=str(ctx.data_dir) if ctx.data_dir is not None else "",
        mode=app.state.mode,
        features=app.state.features,
    )


@router.get("/reports", response_model=ReportList, tags=["reports"])
def list_reports(ctx: Ctx, directory: Directory, user: Caller) -> ReportList:
    """
    Ingested reports with their encounter and difficulty groups. Signed in (hosted), the reports the user opened or
    ingested, newest first; a visitor has none. Local mode lists every ingested report.
    """
    if directory is not None:
        return (
            directory.reports_for(user.id, REPORT_CAP)
            if user is not None
            else ReportList(reports=[], truncated=False, total=0)
        )
    with ctx.lock:
        codes = ctx.data.store.reports()
        reports = [report_info(ctx, code) for code in codes[:REPORT_CAP]]
    return ReportList(reports=reports, truncated=len(codes) > REPORT_CAP, total=len(codes))


@router.get("/reports/{code}", response_model=ReportDetail, responses=_NOT_FOUND, tags=["reports"], dependencies=_GATE)
def get_report(code: str, ctx: Ctx) -> ReportDetail:
    """The report's groups in the order first pulled, each with its pull summaries in time order."""
    with ctx.lock:
        return build_report_detail(read_source(ctx), code)


@router.get(
    "/reports/{code}/night", response_model=NightSummary, responses=_NOT_FOUND, tags=["reports"], dependencies=_GATE
)
def get_night(code: str, ctx: Ctx) -> NightSummary:
    """Report totals plus one section per encounter and difficulty: KPIs, trend, mechanic table and briefing."""
    with ctx.lock:
        return build_night(read_source(ctx), code)


@router.get(
    "/reports/{code}/pulls/{fight}/digest",
    response_model=PullDigest,
    responses=_NOT_FOUND,
    tags=["pulls"],
    dependencies=_GATE,
)
def get_digest(code: str, fight: int, ctx: Ctx, full: bool = False) -> PullDigest:
    """The pull's digest as built. ``full=true`` replaces the capped lists with every record."""
    with ctx.lock:
        r = ctx.records(code, fight)
        return full_digest(r) if full else r.digest


@router.get(
    "/reports/{code}/pulls/{fight}/timeline",
    response_model=PullTimeline,
    responses=_NOT_FOUND,
    tags=["pulls"],
    dependencies=_GATE,
)
def get_timeline(code: str, fight: int, ctx: Ctx) -> PullTimeline:
    """Phase spans and markers for deaths, mechanic clusters, cooldown uses and damage windows, each with its ref."""
    with ctx.lock:
        return timeline(ctx.records(code, fight))


@router.get(
    "/reports/{code}/pulls/{fight}/events",
    response_model=EventsPage,
    dependencies=_GATE,
    responses={**_NOT_FOUND, 400: {"model": ErrorBody, "description": "Bad window or unknown actor"}},
    tags=["pulls"],
)
def get_events(
    code: str,
    fight: int,
    ctx: Ctx,
    t_from_ms: Annotated[int, Query(description="Window start, fight-relative ms (inclusive)")],
    t_to_ms: Annotated[int, Query(description="Window end, fight-relative ms (inclusive); at most 30 s after start")],
    actor: Annotated[str | None, Query(description="Actor name or ID, as source or target")] = None,
    ability_id: int | None = None,
    types: Annotated[list[str] | None, Query(description="Event types to keep (repeat the parameter)")] = None,
) -> EventsPage:
    """Event rows in a window of at most 30 s, exactly as their ``event`` refs resolve, capped at 200 rows."""
    with ctx.lock:
        r = ctx.records(code, fight)
        return events_page(r, t_from_ms, t_to_ms, actor=actor, ability_id=ability_id, types=types)


@router.get(
    "/evidence/{ref}",
    response_model=Evidence,
    dependencies=[Depends(readable_ref)],
    responses={
        404: {
            "model": ErrorBody,
            "description": "The ref is malformed, names no record or a report the caller cannot read",
        },
        503: {"model": ErrorBody, "description": "Warcraft Logs could not answer the access check (wcl_unavailable)"},
        501: {"model": ErrorBody, "description": "Raid-plan refs cannot be resolved yet"},
    },
    tags=["evidence"],
)
def get_evidence(ref: str, ctx: Ctx) -> Evidence:
    """The record a ref names (URL-encode the ref), as the analyst's verify step resolves it."""
    try:
        parsed = parse_ref(ref)
    except UnresolvableRef as exc:
        raise ApiError(404, "unresolvable_ref", str(exc)) from None
    if parsed.kind == "plan":
        raise ApiError(501, "plan_refs_unavailable", "Raid plans aren't available yet, so plan refs cannot resolve.")
    try:
        with ctx.lock:
            record = ctx.data.resolve(ref)
    except UnresolvableRef as exc:
        raise ApiError(404, "unresolvable_ref", str(exc)) from None
    return Evidence(ref=parsed.raw, kind=RefKind(parsed.kind), record=record)


@router.get(
    "/reports/{code}/pulls/{fight}/findings",
    response_model=FindingsList,
    responses=_NOT_FOUND,
    tags=["pulls"],
    dependencies=_GATE,
)
def get_findings(code: str, fight: int, ctx: Ctx) -> FindingsList:
    """Cached findings for the pull with ``stale`` set; empty when none are cached. Never runs the analyst."""
    with ctx.lock:
        pull = next((p for p in ctx.pulls(code) if p.fight_id == fight), None)
        if pull is None:
            raise ApiError(404, "pull_not_found", f"Fight {fight} of report {code} has not been ingested.")
        found = cached_findings(ctx, pull)
    return FindingsList(findings=found[:FINDINGS_CAP], truncated=len(found) > FINDINGS_CAP, total=len(found))
