"""
tests/integration/hosted/test_api_pg.py

The hosted API's Postgres-backed parts against a real database: the report directory (reports a user opened, through
a cached "readable" access answer, or ingested, through a job they requested, newest first, never another user's),
and a job's event stream over a real ``LISTEN job_events`` subscription, closed cleanly when the stream ends.

Each test runs its coroutine on a selector event loop, which psycopg's async connections need on Windows.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from khadbot.api.deps import SqlReportDirectory
from khadbot.api.jobs import event_stream
from khadbot.auth.pg import PostgresAuthStore
from khadbot.auth.store import AccessRecord
from khadbot.db import migrate
from khadbot.db.read_models import project_pull
from khadbot.jobs.status import JobKind, JobState, JobStore, NewJob, PostgresJobBackend


@pytest.fixture
def db(pool):
    migrate(pool)
    return pool


def project_synth(pool, ds) -> None:
    for p in ds.store.pulls(ds.code):
        project_pull(pool, ds.ctx.data.records(p.pull_key), ds.ctx.raw.report_meta(ds.code))


def test_directory_lists_opened_and_ingested_reports_only(db, synth_dataset):
    project_synth(db, synth_dataset)
    code = synth_dataset.code
    auth = PostgresAuthStore(db)
    now = datetime.now(UTC)
    opener = auth.upsert_user(1, "Opener", now)
    ingester = auth.upsert_user(2, "Ingester", now)
    stranger = auth.upsert_user(3, "Stranger", now)
    auth.put_access(opener.id, code, AccessRecord(readable=True, via="user", checked_at=now))
    auth.put_access(stranger.id, code, AccessRecord(readable=False, via=None, checked_at=now))
    store = JobStore(PostgresJobBackend(db))
    new = NewJob(id="job1", kind=JobKind.INGEST, report_code=code, requested_by=ingester.id, spec={})
    asyncio.run(store.create(new), loop_factory=asyncio.SelectorEventLoop)

    directory = SqlReportDirectory(db)
    for user in (opener, ingester):
        listed = directory.reports_for(user.id, 10)
        assert [r.code for r in listed.reports] == [code]
        assert listed.total == 1 and not listed.truncated
        assert listed.reports[0].pulls == len(synth_dataset.fights)
        assert listed.reports[0].groups
    assert directory.reports_for(stranger.id, 10).reports == []  # a denied answer is not "opened"
    assert directory.reports_for(opener.id, 0).truncated  # total counts every report, the list is capped


def test_the_event_stream_follows_a_job_over_listen_notify(db):
    async def go() -> list[str]:
        store = JobStore(PostgresJobBackend(db))
        await store.create(NewJob(id="job1", kind=JobKind.INGEST, report_code="ABC", spec={}))
        await store.begin("job1")
        stream = event_stream(store, "job1", keepalive_s=5)
        chunks = [await asyncio.wait_for(anext(stream), 10)]
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0.2)
        await store.report_progress("job1", "ABC:1", "normalizing", 0.5)
        chunks.append(await asyncio.wait_for(pending, 10))
        await store.finish("job1", JobState.DONE)
        chunks.extend([c async for c in stream])
        return chunks

    chunks = asyncio.run(go(), loop_factory=asyncio.SelectorEventLoop)
    assert [c.split("\n")[1] for c in chunks] == [
        "event: job.state",
        "event: pull.progress",
        "event: job.state",
        "event: job.done",
    ]
