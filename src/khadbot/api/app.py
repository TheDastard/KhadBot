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
- ``jobs`` (the ``JobQueue``) mounts the ingest and watch routes of ``api/jobs.py`` and is opened for the life of the app;
- ``reports`` (``deps.ReportDirectory``) answers ``GET /v1/reports`` with the signed-in user's reports;
- CORS allows the web origins (``KHADBOT_WEB_ORIGINS``) with credentials, ``GET``, ``POST`` and ``DELETE``, and the
  ``Content-Type``, ``X-KhadBot-CSRF`` and ``Last-Event-ID`` request headers, never a wildcard.

``GET /v1/health`` reports ``mode`` and ``features`` (``schemas.Features``). ``mode`` is the label the caller gives
(``main.py serve`` says ``local``; ``main.py api`` and the documentation app say ``hosted``); nothing infers it from what
is mounted, and clients never branch on it. ``features`` is what is mounted: each area sets its flag where it mounts
its routes.

More routers plug in through ``create_app``'s ``routers`` argument, so serving and the generated contract stay in one
place. ``openapi_document`` renders the contract committed as ``contracts/openapi.json``: the hosted API, with every
route, so the web client is generated from one document. The document needs no database, queue or WCL credentials.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from khadbot import wiring
from khadbot.api.deps import ReportDirectory
from khadbot.api.night import ReadSource
from khadbot.api.routes import router
from khadbot.api.schemas import API_VERSION, Features
from khadbot.api.views import ApiContext, ApiError
from khadbot.config import AuthConfig, EncounterConfig
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import FindingsStore, SqliteFindingsStore
from khadbot.storage import BlobStore, LocalBlobStore

if TYPE_CHECKING:
    from khadbot.auth.service import AuthService
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

    ``mode`` is what ``GET /v1/health`` reports, given by the entry point: ``local`` for ``main.py serve`` (and the
    API's tests), ``hosted`` for ``main.py api`` in hosted mode and the documentation app. It is a label for people
    and logs; the web app reads ``features``, never ``mode``.

    Raises:
        ValueError: ``jobs`` or ``reports`` without ``auth``: only the hosted API has users to own them; or ``mode``
            that disagrees with ``auth`` (a hosted API has sign-in, a local one has none).
    """
    if (mode == "hosted") != (auth is not None):
        raise ValueError(f"mode {mode!r} does not fit auth={'given' if auth is not None else 'missing'}")
    if auth is None and (jobs is not None or reports is not None):
        raise ValueError("jobs and reports belong to the hosted API: pass auth with them")
    root = (Path(data_dir) if data_dir is not None else Path(EncounterConfig().data_dir)).resolve()
    if blobs is None:
        blobs = LocalBlobStore(root) if data_dir is not None else wiring.blob_store()
    if data is None:
        data = wiring.encounter_data(blobs)
    if findings is None:
        findings = SqliteFindingsStore(root / "khadbot.sqlite")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if jobs is None:
            yield
            return
        async with jobs.open():  # the queue's own connection pool lives as long as the app
            yield

    app = FastAPI(
        title="KhadBot Dashboard API",
        version=API_VERSION,
        description="Access to ingested WarcraftLogs reports: digests, night summaries and evidence; "
        "in hosted mode also sign-in and ingest jobs.",
        lifespan=lifespan,
    )
    # Hosted mode serves no local path: its data is in Postgres and the bucket.
    shown_dir = root if auth is None or data_dir is not None else None
    app.state.ctx = ApiContext(
        data=data, findings=findings, data_dir=shown_dir, raw=wiring.raw_cache(blobs), read_source=read_source
    )
    app.state.directory = reports
    if auth is None:
        app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET"], allow_headers=["*"])
    else:
        _add_hosted_cors(app, auth.config)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message})

    @app.exception_handler(RequestValidationError)
    async def _bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return JSONResponse(status_code=422, content={"code": "bad_request", "message": problems})

    # What /v1/health offers is what is mounted here: each optional area sets its flag where it mounts its routes, so
    # a test app or a partial deploy reports itself correctly. An area added later sets its own flag the same way.
    # The mode is not among them: the caller names it.
    offered: dict[str, bool] = {}
    app.include_router(router)
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
    for extra in routers:
        app.include_router(extra)
    if auth is not None:  # the WCL budget router (api/budget.py): GET /v1/budget, hosted only
        from khadbot.api.budget import router as budget_router

        app.include_router(budget_router)
        offered["budget"] = True
    app.state.mode = mode
    app.state.features = Features(**offered)
    return app


def _add_hosted_cors(app: FastAPI, config: AuthConfig) -> None:
    """
    The web app's origins, with credentials (the session cookie), the methods the API uses and the CSRF header. The
    origins are listed, never ``*``: a wildcard origin cannot carry credentials, and listing is what keeps a foreign
    page from reading the API as the signed-in user.
    """
    from khadbot.auth.routes import CSRF_HEADER

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.web_origins),
        allow_credentials=True,
        allow_methods=HOSTED_CORS_METHODS,
        allow_headers=["Content-Type", CSRF_HEADER, "Last-Event-ID"],
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
