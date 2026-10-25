"""
tests/unit/jobs/test_backfill.py

Backfill on procrastinate's in-memory connector: which pulls it selects, that each is queued once at a low priority
with app auth, and that a pull whose raw pages are gone is rebuilt from stored events without any WCL call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from helpers.jobs.synth_queue import PULLS, synth_env
from test_queue import drain, make_queue

from khadbot.encounter import digest
from khadbot.jobs import IngestReport
from khadbot.jobs.backfill import BACKFILL_PRIORITY, parse_since, run_backfill
from khadbot.jobs.status import JobKind, JobState


class FakeIndex:
    """The read-model index over a fixed list of ``(pull_key, encounter, difficulty, report start ms)``."""

    def __init__(self, rows: list[tuple[str, int, int | None, int]]) -> None:
        self.rows = rows

    def pull_keys(self, encounter_id: int, difficulty: int | None = None, since_ms: int | None = None) -> list[str]:
        return [
            key
            for key, enc, diff, start in sorted(self.rows, key=lambda r: r[3])
            if enc == encounter_id
            and (difficulty is None or diff == difficulty)
            and (since_ms is None or start >= since_ms)
        ]

    def records_keys(self) -> set[str]:
        return set()


class RecordingProjector:
    def __init__(self) -> None:
        self.versions: list[str] = []

    async def project(self, records) -> None:
        self.versions.append(records.digest.produced_from["records_version"])


class CountingClient:
    """Counts the rate-limit read ingest makes around a run and fails the test on any other use."""

    def __init__(self) -> None:
        self.calls = 0

    async def rate_limit(self, *, user_token: str | None = None) -> dict:
        self.calls += 1
        return {"pointsSpentThisHour": 0.0}

    def __getattr__(self, name: str):
        raise AssertionError(f"backfill used the WCL client: {name}")


@pytest.fixture
def env(tmp_path):
    return synth_env(tmp_path, projector=RecordingProjector())


class TestSince:
    def test_a_date_is_midnight_utc(self):
        assert parse_since("2026-10-01") == int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000)

    def test_a_timestamp_with_an_offset_is_honoured(self):
        assert parse_since("2026-10-01T02:00:00+02:00") == parse_since("2026-10-01")

    def test_a_bad_date_is_a_value_error(self):
        with pytest.raises(ValueError):
            parse_since("last tuesday")


class TestSelection:
    ROWS = [
        ("A:1", 3000, 5, 1_000),
        ("A:2", 3000, 5, 1_000),
        ("B:1", 3000, 4, 2_000),
        ("C:1", 3000, 5, 3_000),
        ("C:2", 9999, 5, 3_000),
    ]

    async def test_every_pull_of_the_encounter_oldest_night_first(self, env):
        queue, _, _ = make_queue(lambda: env.context)
        async with queue.open():
            result = await run_backfill(queue, FakeIndex(self.ROWS), encounter_id=3000)
        assert result.matched == ["A:1", "A:2", "B:1", "C:1"]
        assert result.enqueued == result.matched and result.already_queued == []

    async def test_difficulty_and_since_narrow_the_pulls(self, env):
        queue, _, _ = make_queue(lambda: env.context)
        index = FakeIndex(self.ROWS)
        async with queue.open():
            by_difficulty = await run_backfill(queue, index, encounter_id=3000, difficulty=5, dry_run=True)
            since = await run_backfill(queue, index, encounter_id=3000, since_ms=2_000, dry_run=True)
        assert by_difficulty.matched == ["A:1", "A:2", "C:1"]
        assert since.matched == ["B:1", "C:1"]

    async def test_a_dry_run_queues_nothing(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            result = await run_backfill(queue, FakeIndex(self.ROWS), encounter_id=3000, dry_run=True)
        assert result.enqueued == [] and len(result.matched) == 4
        assert not backend.rows and not connector.jobs

    async def test_an_encounter_with_no_pulls_queues_nothing(self, env):
        queue, backend, _ = make_queue(lambda: env.context)
        async with queue.open():
            result = await run_backfill(queue, FakeIndex(self.ROWS), encounter_id=1)
        assert result.matched == [] and not backend.rows


class TestEnqueue:
    async def test_a_job_is_low_priority_app_auth_and_carries_only_its_id(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        async with queue.open():
            enqueued = await queue.enqueue_backfill("SYNTH01:1")
        assert enqueued.created
        job = backend.rows[enqueued.job.id]
        assert (job.kind, job.auth_kind, job.requested_by, job.parent_id) == (JobKind.PROCESS, "app", None, None)
        assert job.spec == {"job": "process_pull", "pull_key": "SYNTH01:1"}
        assert job.queue_job_id is not None
        row = connector.jobs[job.queue_job_id]
        assert row["queue_name"] == "process" and row["priority"] == BACKFILL_PRIORITY < 0
        assert row["args"] == {"job_id": job.id}
        assert row["lock"] == "process:SYNTH01:1"

    async def test_a_second_backfill_queues_each_pull_once(self, env):
        queue, backend, connector = make_queue(lambda: env.context)
        index = FakeIndex([("SYNTH01:1", 1, None, 1), ("SYNTH01:2", 1, None, 1)])
        async with queue.open():
            first = await run_backfill(queue, index, encounter_id=1)
            second = await run_backfill(queue, index, encounter_id=1)
        assert first.enqueued == ["SYNTH01:1", "SYNTH01:2"]
        assert second.enqueued == [] and second.already_queued == ["SYNTH01:1", "SYNTH01:2"]
        assert len(backend.rows) == 2 and len(connector.jobs) == 2

    async def test_a_pull_is_queued_again_once_its_job_has_finished(self, env):
        queue, backend, _ = make_queue(lambda: env.context)
        async with queue.open():
            await queue.enqueue_backfill("SYNTH01:1")
        await drain(queue, backend)
        async with queue.open():
            again = await queue.enqueue_backfill("SYNTH01:1")
        assert again.created

    async def test_a_backfill_job_is_picked_after_an_ordinary_one(self, env):
        queue, _, connector = make_queue(lambda: env.context)
        async with queue.open():
            await queue.enqueue_backfill("SYNTH01:1")
            await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
        order = sorted(connector.jobs.values(), key=lambda row: (-row["priority"], row["id"]))
        assert [row["queue_name"] for row in order] == ["ingest", "process"]


class TestRebuildWithoutWcl:
    async def _ingest(self, env):
        queue, backend, _ = make_queue(lambda: env.context)
        async with queue.open():
            await queue.enqueue(IngestReport(code="SYNTH01", offline=True))
        await drain(queue, backend)
        return queue, backend

    async def test_expired_raw_pages_are_rebuilt_from_stored_events_with_no_wcl_call(self, env, monkeypatch):
        client = CountingClient()
        env.context.client = client  # type: ignore[assignment]
        queue, backend = await self._ingest(env)
        old_records = set(env.records.keys("SYNTH01"))
        assert len(old_records) == len(PULLS)

        for key in env.raw.blobs.list("raw/"):  # the lifecycle rule has expired every raw page
            env.raw.blobs.delete(key)
        assert env.raw.blobs.list("raw/") == []
        calls_before = client.calls

        monkeypatch.setattr(digest, "RECORDS_VERSION", digest.RECORDS_VERSION + 1)
        async with queue.open():
            result = await run_backfill(queue, FakeIndex([(k, 1, None, 1) for k in PULLS]), encounter_id=1)
        assert result.enqueued == PULLS
        await drain(queue, backend)

        jobs = [j for j in backend.rows.values() if j.kind is JobKind.PROCESS and j.parent_id is None]
        assert sorted(j.pull_key or "" for j in jobs) == PULLS
        assert all(j.state is JobState.DONE for j in jobs)
        assert client.calls == calls_before  # no WCL call of any kind, not even the rate-limit read
        assert len(set(env.records.keys("SYNTH01")) - old_records) == len(PULLS)  # new records beside the old
        projector = env.context.projector
        assert projector.versions[-len(PULLS) :] == [str(digest.RECORDS_VERSION)] * len(PULLS)  # type: ignore[union-attr]

    async def test_a_backfill_of_current_pulls_writes_nothing(self, env):
        queue, backend = await self._ingest(env)
        before = set(env.records.keys("SYNTH01"))
        async with queue.open():
            await run_backfill(queue, FakeIndex([(k, 1, None, 1) for k in PULLS]), encounter_id=1)
        await drain(queue, backend)
        assert set(env.records.keys("SYNTH01")) == before
