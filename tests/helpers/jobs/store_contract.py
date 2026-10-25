"""
tests/helpers/jobs/store_contract.py

The behaviour every ``jobs`` table backend must have, as test classes. tests/unit/jobs/test_status.py runs them on the
in-memory backend and tests/integration/hosted/test_jobs_pg.py on Postgres; each defines a ``store`` fixture.
"""

from __future__ import annotations

import asyncio

from khadbot.jobs import IngestReport, ProcessPull
from khadbot.jobs.status import JobKind, JobState, NewJob


def ingest(job_id: str, code: str = "ABC", **spec) -> NewJob:
    return NewJob(
        id=job_id,
        kind=JobKind.INGEST,
        report_code=code,
        spec=IngestReport(code=code, **spec).model_dump(mode="json"),
    )


def process(job_id: str, parent: str | None, pull: str = "ABC:1") -> NewJob:
    return NewJob(
        id=job_id,
        kind=JobKind.PROCESS,
        report_code=pull.rpartition(":")[0],
        pull_key=pull,
        parent_id=parent,
        spec=ProcessPull(pull_key=pull).model_dump(mode="json"),
    )


class TestClaimIngest:
    async def test_a_second_request_returns_the_active_job(self, store):
        first, created = await store.claim_ingest(ingest("a"))
        again, created_again = await store.claim_ingest(ingest("b"))
        assert (created, created_again) == (True, False)
        assert again.id == first.id == "a"
        assert await store.get("b") is None

    async def test_a_different_report_is_a_different_job(self, store):
        await store.claim_ingest(ingest("a", "ABC"))
        _, created = await store.claim_ingest(ingest("b", "XYZ"))
        assert created

    async def test_a_running_ingest_still_answers(self, store):
        await store.claim_ingest(ingest("a"))
        await store.begin("a")
        job, created = await store.claim_ingest(ingest("b"))
        assert (job.id, created) == ("a", False)

    async def test_a_finished_ingest_does_not(self, store):
        await store.claim_ingest(ingest("a"))
        await store.begin("a")
        await store.finish("a", JobState.DONE)
        job, created = await store.claim_ingest(ingest("b"))
        assert (job.id, created) == ("b", True)

    async def test_an_ingest_that_does_not_cover_the_request_is_not_returned(self, store):
        await store.claim_ingest(ingest("a", fights=[1]))
        job, created = await store.claim_ingest(ingest("b", fights=[2]))
        assert (job.id, created) == ("b", True)


class TestLifecycle:
    async def test_begin_runs_a_queued_job_once(self, store):
        await store.create(ingest("a"))
        job = await store.begin("a")
        assert job is not None and job.state is JobState.RUNNING and job.attempts == 1
        assert await store.begin("a") is None  # already running

    async def test_begin_skips_a_missing_job(self, store):
        assert await store.begin("nope") is None

    async def test_finish_done(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        done = await store.finish("a", JobState.DONE)
        assert done is not None and done.state is JobState.DONE and done.finished_at is not None
        assert done.progress.fraction == 1.0

    async def test_finish_failed_keeps_the_typed_error(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.finish("a", JobState.FAILED, error_code="wcl_error", error_message="nope")
        job = await store.get("a")
        assert job is not None
        assert (job.state, job.error_code, job.error_message) == (JobState.FAILED, "wcl_error", "nope")

    async def test_a_finished_job_stays_finished(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.finish("a", JobState.DONE)
        await store.finish("a", JobState.FAILED, error_code="x", error_message="y")
        job = await store.get("a")
        assert job is not None and job.state is JobState.DONE

    async def test_requeue_then_begin_counts_the_attempt_and_clears_the_error(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.requeue("a", "wcl_rate_limited", "slow down")
        queued = await store.get("a")
        assert queued is not None and queued.state is JobState.QUEUED and queued.error_code == "wcl_rate_limited"
        again = await store.begin("a")
        assert again is not None and again.attempts == 2 and again.error_code is None

    async def test_progress_is_kept_per_pull(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.report_progress("a", None, "ingest", 0.0)
        await store.report_progress("a", "ABC:1", "fetched Deaths", 0.5)
        await store.report_progress("a", "ABC:2", "normalizing", 1.0)
        job = await store.get("a")
        assert job is not None
        assert (job.progress.stage, job.progress.fraction) == ("ingest", 0.0)
        assert job.progress.pulls["ABC:1"].stage == "fetched Deaths"
        assert job.progress.pulls["ABC:2"].fraction == 1.0

    async def test_progress_after_the_end_is_ignored(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.finish("a", JobState.DONE)
        await store.report_progress("a", "ABC:1", "late", 0.1)
        job = await store.get("a")
        assert job is not None and "ABC:1" not in job.progress.pulls


class TestChildren:
    async def start_ingest_with_children(self, store, pulls=("ABC:1", "ABC:2")):
        await store.create(ingest("a"))
        await store.begin("a")
        for i, pull in enumerate(pulls):
            await store.create(process(f"p{i}", "a", pull))
        await store.finish("a", JobState.DONE, awaiting_children=True, points_spent=12.5)

    async def test_the_ingest_waits_for_its_process_jobs(self, store):
        await self.start_ingest_with_children(store)
        waiting = await store.get("a")
        assert waiting is not None and waiting.state is JobState.RUNNING and waiting.awaiting_children

        await store.begin("p0")
        await store.report_progress("p0", "ABC:1", "records", 0.5)
        parent = await store.get("a")
        assert parent is not None and parent.progress.pulls["ABC:1"].stage == "records"

        await store.finish("p0", JobState.DONE)
        assert (await store.get("a")).state is JobState.RUNNING  # type: ignore[union-attr]
        await store.begin("p1")
        await store.finish("p1", JobState.DONE)

        done = await store.get("a")
        assert done is not None and done.state is JobState.DONE and done.points_spent == 12.5
        assert {p.state for p in done.progress.pulls.values()} == {"done"}

    async def test_a_failed_pull_fails_the_ingest_after_the_others_finish(self, store):
        await self.start_ingest_with_children(store)
        await store.begin("p0")
        await store.finish("p0", JobState.FAILED, error_code="pull_not_ingested", error_message="no pull")
        await store.begin("p1")
        await store.finish("p1", JobState.DONE)
        job = await store.get("a")
        assert job is not None and job.state is JobState.FAILED
        assert job.error_code == "pulls_failed" and job.error_message == "1 of 2 pulls failed."
        assert job.progress.pulls["ABC:1"].error == "no pull"

    async def test_pulls_that_failed_to_ingest_fail_the_job(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.finish("a", JobState.DONE, awaiting_children=True, failed_pulls={"ABC:3": "boom"})
        job = await store.get("a")
        assert job is not None and job.state is JobState.FAILED and job.progress.pulls["ABC:3"].state == "failed"

    async def test_no_children_finishes_at_once(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.finish("a", JobState.DONE, awaiting_children=True)
        assert (await store.get("a")).state is JobState.DONE  # type: ignore[union-attr]

    async def test_children_finishing_before_the_parent_still_settle_it(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.create(process("p0", "a"))
        await store.begin("p0")
        await store.finish("p0", JobState.DONE)
        assert (await store.get("a")).state is JobState.RUNNING  # type: ignore[union-attr]
        await store.finish("a", JobState.DONE, awaiting_children=True)
        assert (await store.get("a")).state is JobState.DONE  # type: ignore[union-attr]


class TestCancel:
    async def test_a_queued_job_is_cancelled_at_once(self, store):
        await store.create(ingest("a"))
        job, dropped = await store.request_cancel("a")
        assert job is not None and job.state is JobState.CANCELLED
        assert [d.id for d in dropped] == ["a"]
        assert await store.begin("a") is None

    async def test_a_running_job_is_flagged(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        job, dropped = await store.request_cancel("a")
        assert job is not None and job.state is JobState.RUNNING and job.cancel_requested
        assert dropped == []
        assert await store.cancel_requested("a")

    async def test_cancelling_an_ingest_cancels_its_queued_process_jobs_and_flags_the_running_ones(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.create(process("p0", "a", "ABC:1"))
        await store.create(process("p1", "a", "ABC:2"))
        await store.begin("p1")
        await store.finish("a", JobState.DONE, awaiting_children=True)

        job, dropped = await store.request_cancel("a")

        assert [d.id for d in dropped] == ["p0"]
        assert (await store.get("p0")).state is JobState.CANCELLED  # type: ignore[union-attr]
        assert await store.cancel_requested("p1") and await store.cancel_requested("a")
        assert job is not None and job.state is JobState.RUNNING  # waits for p1

        await store.finish("p1", JobState.CANCELLED)
        final = await store.get("a")
        assert final is not None and final.state is JobState.CANCELLED
        assert {p.state for p in final.progress.pulls.values()} == {"cancelled"}

    async def test_an_unknown_job(self, store):
        assert await store.request_cancel("nope") == (None, [])


class TestEvents:
    async def test_listeners_see_every_change_in_order(self, store):
        async with store.listen() as events:
            await store.create(ingest("a"))
            await store.begin("a")
            await store.report_progress("a", "ABC:1", "x", 0.1)
            await store.finish("a", JobState.DONE)
            seen = [await asyncio.wait_for(anext(events), 1) for _ in range(4)]
        assert seen == ["a", "a", "a", "a"]

    async def test_a_child_change_announces_the_parent_too(self, store):
        await store.create(ingest("a"))
        await store.begin("a")
        await store.create(process("p0", "a"))
        async with store.listen() as events:
            await store.begin("p0")
            await store.report_progress("p0", "ABC:1", "records", 0.5)
            seen = [await asyncio.wait_for(anext(events), 1) for _ in range(3)]
        assert seen == ["p0", "p0", "a"]


class TestActiveProcess:
    async def test_lists_only_the_active_process_jobs_of_the_report(self, store):
        await store.create(process("p1", None, "ABC:1"))
        await store.create(process("p2", None, "ABC:2"))
        await store.create(process("p3", None, "XYZ:1"))
        await store.create(ingest("i1"))
        await store.begin("p2")
        await store.finish("p2", JobState.DONE)

        active = await store.active_process("ABC")
        assert [(j.id, j.pull_key) for j in active] == [("p1", "ABC:1")]
        assert [j.id for j in await store.active_process("XYZ")] == ["p3"]
        assert await store.active_process("NONE") == []
