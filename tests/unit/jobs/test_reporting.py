"""
tests/unit/jobs/test_reporting.py

Progress writing and cancellation: ``ProgressWriter`` batches events onto the job row and notices the cancel flag;
``CancellingRawCache`` stops a fetch right after a page is written, and the next run resumes from the written pages.
"""

from __future__ import annotations

import asyncio

import pytest

from khadbot.encounter.ingest.fetch import RawPageCache, fetch_pull
from khadbot.encounter.models import FetchSpec, Pull
from khadbot.jobs.failures import JobCancelled
from khadbot.jobs.reporting import CancelFlag, CancellingRawCache, ProgressWriter
from khadbot.jobs.specs import IngestReport
from khadbot.jobs.status import JobKind, JobState, JobStore, MemoryJobBackend, NewJob
from khadbot.storage import LocalBlobStore

CODE = "ABC123"
DEATHS = FetchSpec(data_type="Deaths")


def pull() -> Pull:
    return Pull(
        report_code=CODE,
        fight_id=1,
        encounter_id=7,
        encounter_name="Boss",
        difficulty=5,
        kill=False,
        start_ms=1_000,
        end_ms=100_000,
        duration_ms=99_000,
        last_phase=1,
        boss_pct=50.0,
        friendly_player_ids=[1],
    )


class PagedClient:
    """Three pages of Deaths: each answer points at the next, the last ends the chain."""

    def __init__(self, on_request=None) -> None:
        self.calls = 0
        self.on_request = on_request

    async def query(self, gql, variables=None, **_):
        self.calls += 1
        if self.on_request:
            self.on_request(self.calls)
        nxt = None if self.calls >= 3 else variables["startTime"] + 10
        return {"reportData": {"report": {"events": {"data": [{"timestamp": self.calls}], "nextPageTimestamp": nxt}}}}


class TestCancellingRawCache:
    async def test_stops_after_the_page_that_was_being_written_and_resumes_from_it(self, tmp_path):
        base = RawPageCache(LocalBlobStore(tmp_path))
        flag = CancelFlag()
        cache = CancellingRawCache(base, flag)
        client = PagedClient(on_request=lambda n: setattr(flag, "requested", n == 2))

        with pytest.raises(JobCancelled):
            await fetch_pull(client, cache, CODE, pull(), [DEATHS])  # type: ignore[arg-type]
        assert client.calls == 2
        assert len(base.pages(CODE, 1, DEATHS)) == 2  # the page fetched when the flag rose was written
        assert not base.is_complete(CODE, 1, DEATHS)

        resumed = PagedClient()
        resumed.calls = 2  # the third page, as the real chain would answer
        await fetch_pull(resumed, base, CODE, pull(), [DEATHS])  # type: ignore[arg-type]
        assert resumed.calls == 3
        assert len(base.pages(CODE, 1, DEATHS)) == 3 and base.is_complete(CODE, 1, DEATHS)

    async def test_a_quiet_flag_changes_nothing(self, tmp_path):
        base = RawPageCache(LocalBlobStore(tmp_path))
        await fetch_pull(PagedClient(), CancellingRawCache(base, CancelFlag()), CODE, pull(), [DEATHS])  # type: ignore[arg-type]
        assert len(base.pages(CODE, 1, DEATHS)) == 3


@pytest.fixture
def store() -> JobStore:
    return JobStore(MemoryJobBackend())


async def running_job(store: JobStore) -> None:
    await store.create(
        NewJob(
            id="a",
            kind=JobKind.INGEST,
            report_code=CODE,
            spec=IngestReport(code=CODE).model_dump(mode="json"),
        )
    )
    await store.begin("a")


class TestProgressWriter:
    async def test_writes_the_latest_state_of_each_pull_and_flushes_on_exit(self, store):
        await running_job(store)
        writer = ProgressWriter(store, "a", CancelFlag(), interval=60)
        async with writer:
            writer.report("ingest", None, 0.0)
            writer.report("fetched Deaths", f"{CODE}:1", 0.25)
            writer.report("fetched Buffs", f"{CODE}:1", 0.5)
        job = await store.get("a")
        assert job is not None
        assert job.progress.stage == "ingest"
        assert job.progress.pulls[f"{CODE}:1"].stage == "fetched Buffs"

    async def test_writes_while_the_job_runs(self, store):
        await running_job(store)
        writer = ProgressWriter(store, "a", CancelFlag(), interval=0.01)
        async with writer:
            writer.report("ingest", None, 0.5)
            await asyncio.sleep(0.2)
            job = await store.get("a")
            assert job is not None and job.progress.fraction == 0.5

    async def test_notices_a_cancel_request_and_raises_on_the_next_event(self, store):
        await running_job(store)
        flag = CancelFlag()
        writer = ProgressWriter(store, "a", flag, interval=0.01)
        async with writer:
            writer.report("ingest", None, 0.1)  # fine
            await store.request_cancel("a")
            await asyncio.sleep(0.2)
            assert flag.requested
            with pytest.raises(JobCancelled):
                writer.report("ingest", None, 0.2)

    async def test_a_failed_write_does_not_fail_the_job(self, store):
        class Broken(JobStore):
            async def report_progress(self, *args, **kwargs):
                raise RuntimeError("database down")

        writer = ProgressWriter(Broken(MemoryJobBackend()), "a", CancelFlag(), interval=60)
        async with writer:
            writer.report("ingest", None, 0.1)  # the exit flush logs and drops the failure

    async def test_job_state_is_untouched_by_progress(self, store):
        await running_job(store)
        async with ProgressWriter(store, "a", CancelFlag(), interval=60) as writer:
            writer.report("x", None, 0.1)
        job = await store.get("a")
        assert job is not None and job.state is JobState.RUNNING
