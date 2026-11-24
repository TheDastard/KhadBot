"""
api/app.py

The FastAPI application: ``create_app`` wires the read router to an ``ApiContext`` (encounter data, findings
store, data directory) and maps errors to one ``{code, message}`` body.

Local mode (``create_app()`` with no ``auth``) is the dashboard API as it has always been: GET only, no sign-in, and
CORS for the local dev origins (``CORS_ORIGINS``). It serves the read routes and cached findings, on 127.0.0.1
(``main.py serve``); the hosted-only routes below are not mounted there and answer 404 like any unknown route.

Hosted mode is ``create_app(auth=..., jobs=..., reports=...)``, built by ``main.py api`` from the composition root:

- ``auth`` mounts the sign-in routes (``/v1/auth/*``, ``/v1/me``), and every report route then checks that the caller
  may read the report (``deps.readable_report``; an unreadable report answers 404);
- ``jobs`` (the ``JobQueue``) mounts the ingest, watch and review routes of ``api/jobs.py``, ``api/watch.py`` and ``api/reviews.py`` and is opened for the life of the app;
- ``reports`` (``deps.ReportDirectory``) answers ``GET /v1/reports`` with the signed-in user's reports;
- CORS allows the web origins (``KHADBOT_WEB_ORIGINS``) with credentials, ``GET``, ``POST`` and ``DELETE``, and the
  ``Content-Type``, ``X-KhadBot-CSRF`` and ``Last-Event-ID`` request headers, never a wildcard.

In every mode ``RequestLog`` (``api/request_log.py``) gives each request an ID, returns it in ``X-Request-ID`` (exposed
through CORS) and logs one ``http.request`` line; only CORS wraps it, so even its error answers carry CORS headers.

``GET /v1/health`` reports ``mode`` and ``features`` (``schemas.Features``). ``mode`` is the label the caller gives
(``main.py serve`` says ``local``; ``main.py api`` and the documentation app say ``hosted``); nothing infers it from what
is mounted, and clients never branch on it. ``features`` is what is mounted: each area sets its flag where it mounts
its routes.

The boss art routes (``api/art.py``) are public and mounted in every mode.

More routers plug in through ``create_app``'s ``routers`` argument, so serving and the generated contract stay in one
place. ``openapi_document`` renders the contract committed as ``contracts/openapi.json``: the hosted API, with every
route, so the web client is generated from one document. The document needs no database, queue or WCL credentials.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from khadbot import wiring
from khadbot.api.abilities import router as abilities_router
from khadbot.api.art import ArtService, IconSource, ThumbnailCache
from khadbot.api.art import router as art_router
from khadbot.api.boss_art import BossArt, load_boss_art
from khadbot.api.deps import ReportDirectory
from khadbot.api.names import router as names_router
from khadbot.api.night import ReadSource
from khadbot.api.request_log import REQUEST_ID_HEADER, RequestLog
from khadbot.api.routes import router
from khadbot.api.schemas import API_VERSION, Features
from khadbot.api.views import ApiContext, ApiError
from khadbot.blizzard import BlizzardClient
from khadbot.config import ApiConfig, AuthConfig, BlizzardConfig, EncounterConfig
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import FindingsStore, SqliteFindingsStore
from khadbot.storage import BlobStore, LocalBlobStore

if TYPE_CHECKING:
    from khadbot.auth.service import AuthService
    from khadbot.jobs.budget_store import BudgetManager
    from khadbot.jobs.queue import JobQueue

CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
HOSTED_CORS_METHODS = ["GET", "POST", "DELETE"]


def create_app(
    data: EncounterData | None = None,
    findings: FindingsStore | None = None,
    *,
    mode: Literal["local", "hosted"],
    data_dir: Path | str | None = None,
    blobs: BlobStore | None = None,
    routers: Sequence[APIRouter] = (),
    read_source: ReadSource | None = None,
    auth: AuthService | None = None,
    jobs: JobQueue | None = None,
    reports: ReportDirectory | None = None,
    budget: BudgetManager | None = None,
    boss_art: Mapping[int, BossArt] | None = None,
    icon_source: IconSource | None = None,
) -> FastAPI:
    """
    The dashboard API over ``data`` and ``findings``.

    Without them, events, annotations and report metadata are read from ``blobs`` and findings from ``data_dir``
    (default ``KHADBOT_DATA_DIR``), with the authored files under ``config/``. ``blobs`` defaults to a local store
    over ``data_dir`` when that is given, and to ``wiring.blob_store()`` otherwise. Nothing is read until a
    request arrives, so building the app for its OpenAPI document needs no data. ``routers`` are mounted after the
    read router. ``read_source`` serves the report detail and night summary (``night.SqlSource`` reads the read-model
    tables); without it they are built from ``data``'s records.

    ``auth`` (hosted: ``wiring.auth_service()``) makes this the hosted API: it mounts the sign-in routes, gates every
    report route on report access, and sets the CORS policy for the web origins. ``jobs`` (a ``JobQueue``; needs
    ``auth``) mounts the ingest routes and is opened and closed with the app. ``reports`` supplies ``GET /v1/reports``
    for signed-in users; without it every ingested report is listed, which is right only without sign-in.
    ``budget`` is the WCL budget the lookup, search and budget routes use and that uncached report access checks
    reserve their points from; ``main.py api`` always passes it. Without it those routes build one from the
    configuration on first use, and access checks are not counted until ``app.state.budget`` is set.

    ``mode`` is what ``GET /v1/health`` reports, given by the entry point: ``local`` for ``main.py serve`` (and the
    API's tests), ``hosted`` for ``main.py api`` in hosted mode and the documentation app. It is a label for people
    and logs; the web app reads ``features``, never ``mode``.

    The boss art routes (``api/art.py``) are mounted in every mode. ``boss_art`` is the registry (default
    ``config/registries/boss_art.yaml``, loaded here so a malformed file stops the app) and ``icon_source`` what
    resolves a boss's thumbnail URL (default a ``BlizzardClient`` from the ``BLIZZARD_*`` settings, none without
    credentials, and then the listing has no thumbnails).

    Raises:
        ValueError: ``jobs`` or ``reports`` without ``auth``: only the hosted API has users to own them; or ``mode``
            that disagrees with ``auth`` (a hosted API has sign-in, a local one has none).
    """
    if (mode == "hosted") != (auth is not None):
        raise ValueError(f"mode {mode!r} does not fit auth={'given' if auth is not None else 'missing'}")
    if auth is None and (jobs is not None or reports is not None or budget is not None):
        raise ValueError("jobs, reports and budget belong to the hosted API: pass auth with them")
    root = (Path(data_dir) if data_dir is not None else Path(EncounterConfig().data_dir)).resolve()
    if blobs is None:
        blobs = LocalBlobStore(root) if data_dir is not None else wiring.blob_store()
    if data is None:
        data = wiring.encounter_data(blobs)
    if findings is None:
        findings = SqliteFindingsStore(root / "khadbot.sqlite")

    owned_blizzard = BlizzardClient.from_config(BlizzardConfig()) if icon_source is None else None
    art = ArtService(
        boss_art if boss_art is not None else load_boss_art(), blobs, ThumbnailCache(icon_source or owned_blizzard)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            if jobs is None:
                yield
                return
            async with jobs.open():  # the queue's own connection pool lives as long as the app
                yield
        finally:
            if owned_blizzard is not None:
                await owned_blizzard.aclose()

    app = FastAPI(
        title="KhadBot Dashboard API",
        version=API_VERSION,
        description="Access to ingested WarcraftLogs reports: digests, night summaries and evidence; "
        "in hosted mode also sign-in and ingest jobs. In hosted mode any route may also answer 413 "
        "`body_too_large` (a request body over the server's limit) or 429 `rate_limited` with `Retry-After` (too "
        "many requests from one address), both with the usual `{code, message}` body.",
        lifespan=lifespan,
    )
    # Hosted mode serves no local path: its data is in Postgres and the bucket.
    shown_dir = root if auth is None or data_dir is not None else None
    app.state.ctx = ApiContext(
        data=data, findings=findings, data_dir=shown_dir, raw=wiring.raw_cache(blobs), read_source=read_source
    )
    app.state.directory = reports
    app.state.art = art
    if budget is not None:
        app.state.budget = budget
    if auth is None:
        app.add_middleware(RequestLog)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=CORS_ORIGINS,
            allow_methods=["GET"],
            allow_headers=["*"],
            expose_headers=[REQUEST_ID_HEADER],
        )
    else:
        from khadbot.api.limits import RequestLimits  # the body cap and the per-client request rate (api/limits.py)

        settings = ApiConfig()
        local = not getattr(auth.config, "cookie_secure", False)
        app.add_middleware(
            RequestLimits, max_body_bytes=settings.max_body_bytes, per_minute=settings.rate_limit(local=local)
        )
        app.add_middleware(RequestLog)  # outside the limits, so their answers are logged and carry the request ID
        _add_hosted_cors(app, auth.config)  # added last, so it wraps the limits and their answers carry CORS headers

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        request.state.log_error_code = exc.code  # the request's http.request line carries it (api/request_log.py)
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message})

    @app.exception_handler(RequestValidationError)
    async def _bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        request.state.log_error_code = "bad_request"
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return JSONResponse(status_code=422, content={"code": "bad_request", "message": problems})

    # What /v1/health offers is what is mounted here: each optional area sets its flag where it mounts its routes, so
    # a test app or a partial deploy reports itself correctly. An area added later sets its own flag the same way.
    # The mode is not among them: the caller names it.
    offered: dict[str, bool] = {}
    app.include_router(router)
    app.include_router(abilities_router)  # report abilities with WCL's icon names (api/abilities.py), every mode
    app.include_router(art_router)  # boss portraits and thumbnail URLs (api/art.py): public, every mode
    app.include_router(names_router)  # display names, cooldowns and players (api/names.py): read routes, every mode
    if auth is not None:
        from khadbot.auth.routes import mount  # hosted only: loads the crypto library

        mount(app, auth)
        offered["sign_in"] = True
    if jobs is not None:
        from khadbot.api.jobs import router as jobs_router  # hosted only: loads the queue library

        app.state.jobs = jobs
        app.include_router(jobs_router)
        offered["ingest_jobs"] = True
        from khadbot.api.watch import router as watch_router  # live report watch routes, hosted only

        app.include_router(watch_router)
        offered["live_watch"] = True
        from khadbot.api.reviews import router as reviews_router  # pull reviews on the review queue, hosted only

        app.include_router(reviews_router)
        offered["reviews"] = True
    for extra in routers:
        app.include_router(extra)
    if auth is not None:  # the WCL budget router (api/budget.py): GET /v1/budget, hosted only
        from khadbot.api.budget import AccessCheckBudget
        from khadbot.api.budget import router as budget_router

        access = getattr(auth, "access", None)  # the documentation app's stand-in service has none
        if access is not None and access.budget is None:
            # Uncached access checks ask WCL: they reserve their points like every other request.
            access.budget = AccessCheckBudget(lambda: getattr(app.state, "budget", None))

        app.include_router(budget_router)
        offered["budget"] = True
        from khadbot.api.raids.lookup import router as lookup_router  # report lookup: boss pulls from WCL, hosted only

        app.include_router(lookup_router)
        offered["report_lookup"] = True
        from khadbot.api.raids.search import router as search_router  # guild and character search, hosted only
        from khadbot.api.raids.servers import router as servers_router
        from khadbot.api.raids.servers import server_list_response

        # config/registries/servers.yaml, built once into the response and its ETag; a malformed file fails here
        app.state.server_list, app.state.server_list_body, app.state.server_list_etag = server_list_response()
        app.include_router(servers_router)
        app.include_router(search_router)
        offered["raid_search"] = True
    app.state.mode = mode
    app.state.features = Features(**offered)
    return app


def _add_hosted_cors(app: FastAPI, config: AuthConfig) -> None:
    """
    The web app's origins, with credentials (the session cookie), the methods the API uses and the CSRF header. The
    origins are listed, never ``*``: with credentials the middleware would echo any origin that asks, and listing is
    what keeps a foreign page from reading the API as the signed-in user. ``AuthService`` refuses a list that holds
    ``*`` or anything that is not an origin (``auth.service.check_web_origins``).
    """
    from khadbot.auth.routes import CSRF_HEADER

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.web_origins),
        allow_credentials=True,
        allow_methods=HOSTED_CORS_METHODS,
        allow_headers=["Content-Type", CSRF_HEADER, "Last-Event-ID"],
        expose_headers=[REQUEST_ID_HEADER],
    )


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def hosted_documentation_app(data_dir: Path | str = ".") -> FastAPI:
    """
    The hosted API with stand-ins for the services it would be given, for rendering its OpenAPI document. Nothing in
    it is ever called: no request is served, so the stand-ins need no database, queue or credentials.
    """
    config = replace(AuthConfig(), token_keys=())
    stand_in: Any = type("DocumentationService", (), {"config": config})()
    queue: Any = type("DocumentationQueue", (), {})()
    return create_app(
        data_dir=data_dir, mode="hosted", auth=cast("AuthService", stand_in), jobs=cast("JobQueue", queue)
    )


def openapi_document(app: FastAPI | None = None) -> str:
    """
    The OpenAPI document as sorted, indented JSON with a final newline (``contracts/openapi.json``). Without an app it
    is the hosted API's, which holds every route.
    """
    app = app or hosted_documentation_app()
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
