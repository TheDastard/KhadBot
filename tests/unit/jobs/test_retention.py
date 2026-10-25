"""
tests/unit/jobs/test_retention.py

The records cleanup: it keeps referenced and young records, removes the rest, and is wired to run on a schedule.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from procrastinate.testing import InMemoryConnector

from khadbot.config import QueueConfig, RetentionConfig
from khadbot.jobs.queue import CLEANUP_TASK, JobQueue
from khadbot.jobs.retention import CleanupSources, cleanup_records
from khadbot.jobs.status import JobStore, MemoryJobBackend
from khadbot.storage import LocalBlobStore

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
REFERENCED = {"records/A/1__old-referenced.json"}


class FakeIndex:
    def __init__(self, referenced: set[str]) -> None:
        self.referenced = referenced

    def pull_keys(self, encounter_id, difficulty=None, since_ms=None) -> list[str]:
        return []

    def records_keys(self) -> set[str]:
        return set(self.referenced)


def put(blobs: LocalBlobStore, key: str, age_days: float) -> None:
    blobs.put(key, b"{}")
    when = (NOW - age_days * DAY).timestamp()
    os.utime(blobs.path(key), (when, when))


@pytest.fixture
def blobs(tmp_path) -> LocalBlobStore:
    store = LocalBlobStore(tmp_path / "blobs")
    put(store, "records/A/1__old-referenced.json", 90)
    put(store, "records/A/1__old-orphan.json", 45)
    put(store, "records/A/2__young-orphan.json", 5)
    put(store, "records/B/1__just-under.json", 29.9)
    put(store, "records/B/1__just-over.json", 30.1)
    put(store, "events/A/1.parquet", 400)
    put(store, "raw/A/report.json", 400)
    put(store, "annotated/A/1.json", 400)
    return store


class TestCleanup:
    def test_removes_only_old_unreferenced_records(self, blobs):
        result = cleanup_records(blobs, FakeIndex(REFERENCED), min_age=30 * DAY, now=NOW)
        assert sorted(result.deleted) == ["records/A/1__old-orphan.json", "records/B/1__just-over.json"]
        assert sorted(blobs.list("records/")) == [
            "records/A/1__old-referenced.json",
            "records/A/2__young-orphan.json",
            "records/B/1__just-under.json",
        ]
        assert (result.examined, result.kept_referenced, result.kept_young) == (5, 1, 2)

    def test_never_touches_events_raw_or_annotated(self, blobs):
        cleanup_records(blobs, FakeIndex(set()), min_age=DAY, now=NOW)
        assert blobs.list("events/") and blobs.list("raw/") and blobs.list("annotated/")

    def test_a_second_pass_deletes_nothing(self, blobs):
        cleanup_records(blobs, FakeIndex(REFERENCED), min_age=30 * DAY, now=NOW)
        again = cleanup_records(blobs, FakeIndex(REFERENCED), min_age=30 * DAY, now=NOW)
        assert again.deleted == []

    def test_a_dry_run_lists_without_deleting(self, blobs):
        result = cleanup_records(blobs, FakeIndex(REFERENCED), min_age=30 * DAY, now=NOW, dry_run=True)
        assert len(result.deleted) == 2 and len(blobs.list("records/")) == 5

    def test_everything_is_kept_while_every_row_references_it(self, blobs):
        every = set(blobs.list("records/"))
        assert cleanup_records(blobs, FakeIndex(every), min_age=DAY, now=NOW).deleted == []

    def test_a_failed_delete_does_not_stop_the_pass(self, blobs, monkeypatch):
        real = blobs.delete

        def flaky(key: str) -> None:
            if key == "records/A/1__old-orphan.json":
                raise OSError("locked")
            real(key)

        monkeypatch.setattr(blobs, "delete", flaky)
        result = cleanup_records(blobs, FakeIndex(REFERENCED), min_age=30 * DAY, now=NOW)
        assert result.deleted == ["records/B/1__just-over.json"]


def make_queue(blobs, index, cron: str = "17 3 * * *") -> JobQueue:
    return JobQueue(
        JobStore(MemoryJobBackend()),
        InMemoryConnector(),
        config=QueueConfig(),
        retention=RetentionConfig(records_min_age_days=30, cleanup_cron=cron),
        cleanup=CleanupSources(blobs=lambda: blobs, index=lambda: index),
    )


class TestScheduling:
    def test_the_cleanup_is_a_periodic_task_on_the_process_queue(self, blobs):
        queue = make_queue(blobs, FakeIndex(set()))
        periodic = list(queue.app.periodic_registry.periodic_tasks.values())
        assert [p.task.name for p in periodic] == [CLEANUP_TASK]
        assert periodic[0].task.queue == "process"
        assert periodic[0].cron == "17 3 * * *"

    def test_an_empty_cron_turns_the_schedule_off(self, blobs):
        queue = make_queue(blobs, FakeIndex(set()), cron="")
        assert not queue.app.periodic_registry.periodic_tasks
        assert CLEANUP_TASK in queue.app.tasks

    async def test_run_cleanup_dry_run_examines_the_records(self, blobs):
        queue = make_queue(blobs, FakeIndex(REFERENCED))
        result = await queue.run_cleanup(dry_run=True)
        assert result is not None and result.examined == 5 and len(blobs.list("records/")) == 5

    async def test_no_index_means_nothing_is_removed(self, blobs):
        queue = make_queue(blobs, None)  # type: ignore[arg-type]
        assert await queue.run_cleanup() is None
        assert len(blobs.list("records/")) == 5
