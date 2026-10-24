"""
api/app.py

The FastAPI application: ``create_app`` wires the read router to an ``ApiContext`` (encounter data, findings
store, data directory), maps errors to one ``{code, message}`` body and allows only the local dev origins.

More routers (ingest) and their dependencies plug in through ``create_app``'s ``routers`` argument and the
context, so serving and the generated contract stay in one place. ``openapi_document`` renders the contract
committed as ``contracts/openapi.json``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from khadbot import wiring
from khadbot.api.night import ReadSource
from khadbot.api.routes import router
from khadbot.api.schemas import API_VERSION
from khadbot.api.views import ApiContext, ApiError
from khadbot.config import EncounterConfig
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import FindingsStore, SqliteFindingsStore
from khadbot.storage import BlobStore, LocalBlobStore

CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app(
    data: EncounterData | None = None,
    findings: FindingsStore | None = None,
    *,
    data_dir: Path | str | None = None,
    blobs: BlobStore | None = None,
    routers: Sequence[APIRouter] = (),
    read_source: ReadSource | None = None,
) -> FastAPI:
    """
    The dashboard API over ``data`` and ``findings``.

    Without them, events, annotations and report metadata are read from ``blobs`` and findings from ``data_dir``
    (default ``KHADBOT_DATA_DIR``), with the authored files under ``config/``. ``blobs`` defaults to a local store
    over ``data_dir`` when that is given, and to ``wiring.blob_store()`` otherwise. Nothing is read until a
    request arrives, so building the app for its OpenAPI document needs no data. ``routers`` are mounted after the
    read router. ``read_source`` serves the report detail and night summary (``night.SqlSource`` reads the read-model
    tables); without it they are built from ``data``'s records.
    """
    root = (Path(data_dir) if data_dir is not None else Path(EncounterConfig().data_dir)).resolve()
    if blobs is None:
        blobs = LocalBlobStore(root) if data_dir is not None else wiring.blob_store()
    if data is None:
        data = wiring.encounter_data(blobs)
    if findings is None:
        findings = SqliteFindingsStore(root / "khadbot.sqlite")

    app = FastAPI(
        title="KhadBot Dashboard API",
        version=API_VERSION,
        description="Read-only access to ingested WarcraftLogs reports: digests, night summaries and evidence.",
    )
    app.state.ctx = ApiContext(
        data=data, findings=findings, data_dir=root, raw=wiring.raw_cache(blobs), read_source=read_source
    )
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET"], allow_headers=["*"])

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message})

    @app.exception_handler(RequestValidationError)
    async def _bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return JSONResponse(status_code=422, content={"code": "bad_request", "message": problems})

    app.include_router(router)
    for extra in routers:
        app.include_router(extra)
    return app


def openapi_document(app: FastAPI | None = None) -> str:
    """The OpenAPI document as sorted, indented JSON with a final newline (``contracts/openapi.json``)."""
    app = app or create_app(data_dir=Path("."))
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
