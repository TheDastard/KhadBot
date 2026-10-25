"""
tests/helpers/api/hosted.py

The hosted API for tests: ``create_app(auth=..., jobs=..., reports=...)`` over SYNTH01's stores, the in-memory
sign-in store, the in-memory ``jobs`` table and procrastinate's in-memory connector. WCL is faked (respx), so a test
decides who can read which report with ``wcl.readable``. ``Actor`` is a browser: a TestClient with (or without) a
session cookie.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate.testing import InMemoryConnector
from pydantic import SecretStr

from helpers.jobs.ingest import fetch_and_normalize
from helpers.jobs.synth_queue import SynthEnv, synth_env
from khadbot.api import create_app
from khadbot.api.schemas import ReportList
from khadbot.api.views import ApiContext, report_info
from khadbot.auth.routes import CSRF_HEADER, SESSION_COOKIE
from khadbot.auth.service import AuthService
from khadbot.auth.store import User
from khadbot.config import QueueConfig
from khadbot.encounter.findings import SqliteFindingsStore
from khadbot.jobs import IngestReport, UserTokenResolver
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobStore, MemoryJobBackend
from khadbot.storage import LocalBlobStore
from khadbot.wcl.oauth import TokenSet

CSRF = {CSRF_HEADER: "1"}
CONFIG = QueueConfig(max_retries=2, retry_backoff_s=0, progress_interval_s=0.01)
BASE = "http://localhost:8000"


class StubDirectory:
    """``ReportDirectory`` over the app's data: each user sees the codes listed for them."""

    def __init__(self, ctx: ApiContext) -> None:
        self.ctx = ctx
        self.codes: dict[str, list[str]] = {}

    def reports_for(self, user_id: str, limit: int) -> ReportList:
        codes = self.codes.get(user_id, [])
        reports = [report_info(self.ctx, c) for c in codes[:limit]]
        return ReportList(reports=reports, truncated=len(codes) > limit, total=len(codes))


@dataclass
class Actor:
    """A browser. ``user`` is None for a visitor."""

    client: TestClient
    user: User | None = None

    def get(self, url: str, **kw):
        return self.client.get(url, **kw)

    def post(self, url: str, *, csrf: bool = True, **kw):
        headers = {**(CSRF if csrf else {}), **kw.pop("headers", {})}
        return self.client.post(url, headers=headers, **kw)


@dataclass
class Harness:
    app: FastAPI
    service: AuthService
    env: SynthEnv
    queue: JobQueue
    backend: MemoryJobBackend
    connector: InMemoryConnector
    directory: StubDirectory
    wcl: object
    store: object
    clock: object
    actors: list[Actor] = field(default_factory=list)

    def actor(self, name: str | None = None, *, tokens: bool = True) -> Actor:
        """A browser signed in as a new user (``name``), or a visitor when ``name`` is None."""
        client = TestClient(self.app, base_url=BASE)
        actor = Actor(client)
        if name is not None:
            wcl_id = 1000 + len(self.actors)
            actor.user = self.store.upsert_user(wcl_id, name, self.clock())
            if tokens:
                token = f"fake-access-{name}"
                self.wcl.users[token] = (wcl_id, name)  # type: ignore[attr-defined]
                asyncio.run(
                    self.service.tokens.save(
                        actor.user.id, TokenSet(SecretStr(token), SecretStr(f"fake-refresh-{name}"), 3600)
                    )
                )
            sid = asyncio.run(self.service.sessions.create(actor.user.id))
            client.cookies.set(SESSION_COOKIE, sid)
        self.actors.append(actor)
        return actor

    def token_of(self, name: str) -> str:
        return f"fake-access-{name}"

    def ingest_offline(self) -> None:
        """Put SYNTH01's pulls in the stores directly (the ingest job, then each pull's events normalized)."""
        result = asyncio.run(fetch_and_normalize(IngestReport(code="SYNTH01", offline=True), self.env.context))
        assert not result.failed

    def run_worker(self) -> threading.Thread:
        """Run queued jobs on a thread (the API's TestClient loop is busy serving), until none is left."""

        async def go() -> None:
            for _ in range(10):
                async with self.queue.open():
                    await self.queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)
                if not any(j.active and not j.awaiting_children for j in self.backend.rows.values()):
                    return

        thread = threading.Thread(target=lambda: asyncio.run(go()), daemon=True)
        thread.start()
        return thread


def build_harness(tmp_path: Path, service: AuthService, wcl, store, clock) -> Harness:
    env = synth_env(tmp_path)
    env.context = replace(env.context, resolver=UserTokenResolver(lambda: service.tokens))
    backend = MemoryJobBackend()
    connector = InMemoryConnector()
    queue = JobQueue(JobStore(backend), connector, config=CONFIG, context_factory=lambda: env.context)
    directory = StubDirectory.__new__(StubDirectory)
    app = create_app(
        env.context.data,
        SqliteFindingsStore(tmp_path / "findings.sqlite"),
        mode="hosted",
        data_dir=tmp_path,
        blobs=LocalBlobStore(tmp_path / "blobs"),
        auth=service,
        jobs=queue,
        reports=directory,
    )
    StubDirectory.__init__(directory, app.state.ctx)
    return Harness(app, service, env, queue, backend, connector, directory, wcl, store, clock)
