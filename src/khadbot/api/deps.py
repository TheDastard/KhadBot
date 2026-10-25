"""
api/deps.py

The request dependencies that tie the dashboard routes to sign-in and report access, and the report directory behind
``GET /v1/reports``.

Local mode has no sign-in: ``app.state.auth`` is absent, ``caller`` is always None and ``readable_report`` lets every
request through, so the routes behave exactly as they did before sign-in existed. Hosted mode mounts the sign-in
service (``create_app(auth=...)``); then

- ``caller`` is ``auth.routes.optional_user`` (the signed-in user, or None for a visitor);
- ``signed_in`` is ``auth.routes.current_user`` (401 ``not_signed_in`` for a visitor);
- ``csrf`` is ``auth.routes.require_csrf``, which every state-changing route depends on;
- ``readable_report`` and ``readable_ref`` call ``ReportAccess.require_readable``, the one access rule, for the
  report a route names. A report the caller cannot read answers 404 ``report_not_found``, so the API never confirms
  that a private report exists. When WCL cannot answer the check, nothing is cached and the route answers 503.

The sign-in modules load the crypto library, so they are imported inside the dependencies and local mode never
loads them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Protocol

from fastapi import Depends, Request

from khadbot.api.schemas import GroupInfo, ReportInfo, ReportList
from khadbot.api.views import difficulty_name
from khadbot.auth.errors import NotSignedInError
from khadbot.auth.store import User
from khadbot.encounter.evidence import UnresolvableRef, parse_ref

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from khadbot.auth.service import AuthService

# ---------------------------------------------------------------------------
# Who is calling
# ---------------------------------------------------------------------------


def auth_service(request: Request) -> AuthService | None:
    """The hosted sign-in service, or None in local mode."""
    service: AuthService | None = getattr(request.app.state, "auth", None)
    return service


async def caller(request: Request) -> User | None:
    """The signed-in user, or None for a visitor (and always None in local mode)."""
    service = auth_service(request)
    if service is None:
        return None
    from khadbot.auth.routes import optional_user  # hosted only: loads the crypto library

    return await optional_user(request, service)


async def signed_in(request: Request) -> User:
    """The signed-in user; a visitor gets 401 ``not_signed_in``. Job routes are hosted-only, so there is always a user."""
    user = await caller(request)
    if user is None:
        raise NotSignedInError("Sign in with Warcraft Logs first.")
    return user


async def csrf(request: Request) -> None:
    """The CSRF check of ``auth.routes.require_csrf``: the custom header, and a browser ``Origin`` of the web app."""
    service = auth_service(request)
    if service is None:
        return
    from khadbot.auth.routes import require_csrf

    await require_csrf(request, service)


Caller = Annotated[User | None, Depends(caller)]
SignedIn = Annotated[User, Depends(signed_in)]

# ---------------------------------------------------------------------------
# Report access
# ---------------------------------------------------------------------------


async def require_readable(request: Request, code: str) -> None:
    """Raise ``ReportNotFoundError`` (404) unless the caller may read report ``code``. A no-op in local mode."""
    service = auth_service(request)
    if service is None:
        return
    user = await caller(request)
    await service.access.require_readable(user.id if user is not None else None, code)


async def readable_report(request: Request, code: str) -> None:
    """The dependency of every route with a ``{code}`` path parameter."""
    await require_readable(request, code)


async def readable_ref(request: Request, ref: str) -> None:
    """
    The dependency of the evidence route: the caller must be able to read the ref's report. A ref that does not parse
    or names no report (a plan ref) needs no report, and the route answers it as it always did.
    """
    try:
        parsed = parse_ref(ref)
    except UnresolvableRef:
        return
    if parsed.pull_key is not None:
        await require_readable(request, parsed.pull_key.rpartition(":")[0])


# ---------------------------------------------------------------------------
# Report directory
# ---------------------------------------------------------------------------


class ReportDirectory(Protocol):
    """Which reports a signed-in user sees in ``GET /v1/reports``."""

    def reports_for(self, user_id: str, limit: int) -> ReportList:
        """The reports the user has opened or ingested, newest first, at most ``limit``; ``total`` counts them all."""
        ...


def get_directory(request: Request) -> ReportDirectory | None:
    directory: ReportDirectory | None = getattr(request.app.state, "directory", None)
    return directory


Directory = Annotated[ReportDirectory | None, Depends(get_directory)]


class SqlReportDirectory:
    """
    The directory over the read-model tables: reports the user opened (a "readable" ``report_access`` row) or
    ingested (a job they requested), that have at least one projected pull, newest report start first.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def reports_for(self, user_id: str, limit: int) -> ReportList:
        from khadbot.db import queries  # hosted only: loads psycopg

        with self.pool.connection() as conn:
            found = queries.user_reports(conn, user_id, limit)
        groups: dict[str, list[GroupInfo]] = {}
        for g in found.groups:
            groups.setdefault(g.report_code, []).append(
                GroupInfo(
                    encounter_id=g.encounter_id,
                    encounter_name=g.encounter_name,
                    difficulty=g.difficulty,
                    difficulty_name=difficulty_name(g.difficulty),
                    pulls=g.pulls,
                )
            )
        reports = [
            ReportInfo(
                code=r.code,
                title=r.title,
                pulls=sum(g.pulls for g in groups.get(r.code, [])),
                groups=groups.get(r.code, []),
            )
            for r in found.reports
        ]
        return ReportList(reports=reports, truncated=found.total > len(reports), total=found.total)
