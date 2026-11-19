"""
tests/integration/hosted/test_jobs_pg.py

The ``jobs`` table against a real database: the same contract the in-memory backend passes
(tests/helpers/jobs/store_contract.py), plus what only Postgres does: the migration's constraints, a NOTIFY listener
that receives each change in commit order, and two writers racing to start an ingest of one report.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from helpers.jobs.store_contract import (  # noqa: F401 - collected here
    TestActiveProcess,
    TestCancel,
    TestChildren,
    TestClaimIngest,
    TestLifecycle,
    TestVisitorIngests,
    ingest,
)
from helpers.jobs.store_contract import TestEvents as ContractEvents

from khadbot.db import migrate
from khadbot.jobs.status import JobState, JobStore, PostgresJobBackend


@pytest.fixture
def store(pool) -> JobStore:
    migrate(pool)
    return JobStore(PostgresJobBackend(pool))


# psycopg's async connections (the listener) cannot run on the Proactor loop that Windows tests default to.
@pytest.mark.skipif(sys.platform == "win32", reason="psycopg async needs the selector event loop")
class TestEvents(ContractEvents):
    pass


@pytest.mark.skipif(sys.platform == "win32", reason="psycopg async needs the selector event loop")
class TestNotify:
    async def test_a_listener_receives_each_change_in_order_with_the_job_id(self, store):
        await store.create(ingest("a"))
        async with store.listen() as events:
            await store.begin("a")
            await store.report_progress("a", "ABC:1", "x", 0.5)
            await store.report_progress("a", "ABC:2", "y", 0.5)
            await store.finish("a", JobState.DONE)
            seen = [await asyncio.wait_for(anext(events), 5) for _ in range(4)]
        assert seen == ["a"] * 4

    async def test_changes_to_two_jobs_arrive_in_commit_order(self, store):
        await store.create(ingest("a", "AAA"))
        await store.create(ingest("b", "BBB"))
        async with store.listen() as events:
            await store.begin("a")
            await store.begin("b")
            await store.finish("a", JobState.DONE)
            seen = [await asyncio.wait_for(anext(events), 5) for _ in range(3)]
        assert seen == ["a", "b", "a"]


class TestTable:
    async def test_the_state_column_rejects_an_unknown_state(self, store, pool):
        await store.create(ingest("a"))
        with pytest.raises(psycopg.errors.CheckViolation), pool.connection() as conn:
            conn.execute("UPDATE jobs SET state = 'paused' WHERE id = 'a'")

    async def test_progress_and_spec_round_trip_as_json(self, store, pool):
        await store.create(ingest("a", fights=[3, 4]))
        await store.begin("a")
        await store.report_progress("a", "ABC:3", "fetched Deaths", 0.25)
        with pool.connection() as conn:
            progress, spec = conn.execute("SELECT progress, spec FROM jobs WHERE id = 'a'").fetchone()
        assert progress["pulls"]["ABC:3"] == {
            "stage": "fetched Deaths",
            "fraction": 0.25,
            "state": "running",
            "error": None,
        }
        assert spec["fights"] == [3, 4] and spec["auth"] == {"kind": "app"}

    async def test_two_writers_racing_to_start_an_ingest_make_one_job(self, store):
        def claim(job_id: str):
            return asyncio.run(store.claim_ingest(ingest(job_id)))

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, [f"j{i}" for i in range(8)]))
        assert sum(created for _, created in results) == 1
        assert len({job.id for job, _ in results}) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="psycopg async needs the selector event loop")
class TestSharedListener:
    """Every ``store.listen()`` of one store shares a single LISTEN connection (jobs/listener.py)."""

    @staticmethod
    def _listening(pool) -> int:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND query = 'LISTEN job_events'"
            ).fetchone()
        return int(row[0])

    async def test_many_listeners_hold_one_connection_and_all_hear_a_change(self, store, pool):
        before = await asyncio.to_thread(self._listening, pool)
        async with contextlib.AsyncExitStack() as stack:
            streams = [await stack.enter_async_context(store.listen()) for _ in range(25)]
            assert await asyncio.to_thread(self._listening, pool) == before + 1
            await store.create(ingest("a"))
            assert [await asyncio.wait_for(anext(s), 5) for s in streams] == ["a"] * 25
        for _ in range(50):  # the connection is closed once the last listener has left
            if await asyncio.to_thread(self._listening, pool) == before:
                break
            await asyncio.sleep(0.1)
        assert await asyncio.to_thread(self._listening, pool) == before

    async def test_a_listener_that_arrives_later_reuses_a_fresh_connection(self, store):
        for job_id in ("a", "b"):
            async with store.listen() as events:
                await store.create(ingest(job_id, job_id.upper() * 3))
                assert await asyncio.wait_for(anext(events), 5) == job_id


class TestReviewClaims:
    """``claim_review`` against Postgres: the quota counts SQL rows, and a per-user lock serializes racing requests."""

    @staticmethod
    def review(job_id: str, user: str, pull: str = "ABC:1"):
        from khadbot.jobs import ReviewPull
        from khadbot.jobs.status import JobKind, NewJob

        return NewJob(
            id=job_id,
            kind=JobKind.REVIEW,
            report_code=pull.rpartition(":")[0],
            pull_key=pull,
            requested_by=user,
            spec=ReviewPull(pull_key=pull).model_dump(mode="json"),
        )

    @staticmethod
    def users(pool, *ids: str) -> None:
        with pool.connection() as conn:
            for n, uid in enumerate(ids, start=1):
                conn.execute(
                    "INSERT INTO users (id, wcl_user_id, name, created_at, last_login_at)"
                    " VALUES (%s, %s, %s, now(), now())",
                    (uid, n, uid),
                )

    async def test_the_quota_is_enforced_refunded_and_shared_findings_cost_nothing(self, store, pool):
        from datetime import UTC, datetime, timedelta

        self.users(pool, "u1", "u2")
        since = datetime.now(UTC) - timedelta(hours=24)
        first = await store.claim_review(self.review("r1", "u1", "ABC:1"), limit=1, since=since)
        assert first.created and len(first.counted) == 1
        assert (await store.claim_review(self.review("r2", "u1", "ABC:2"), limit=1, since=since)).job is None
        theirs = await store.claim_review(self.review("r3", "u2", "ABC:1"), limit=1, since=since)
        assert not theirs.created and theirs.job is not None and theirs.job.id == "r1"  # in flight: followed
        assert await store.reviews_since("u2", since) == []
        await store.finish("r1", JobState.FAILED, error_code="internal_error")
        assert (await store.claim_review(self.review("r4", "u1", "ABC:2"), limit=1, since=since)).created

    async def test_racing_requests_cannot_both_take_the_last_slot(self, store, pool):
        from datetime import UTC, datetime, timedelta

        self.users(pool, "u1")
        since = datetime.now(UTC) - timedelta(hours=24)

        def claim(n: int):
            return asyncio.run(store.claim_review(self.review(f"r{n}", "u1", f"ABC:{n}"), limit=2, since=since))

        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(claim, range(8)))
        assert sum(r.created for r in results) == 2
