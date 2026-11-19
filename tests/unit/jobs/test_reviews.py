"""
tests/unit/jobs/test_reviews.py

Review jobs on the queue: the configured analyst model, the rolling quota, claiming a review (quota boundaries,
refunds, an identical review returned instead of a second), and a worker running one with the usage ledger writing
a row per model call with the job, user and model.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest
from helpers.encounter.review_fakes import respond
from helpers.encounter.usage_fake import UsageFakeModel
from helpers.jobs.synth_queue import synth_env
from procrastinate.testing import InMemoryConnector

from khadbot.config import QueueConfig, reset_config
from khadbot.encounter.findings import SqliteFindingsStore
from khadbot.jobs import ReviewPull
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.reviews import REVIEW_WINDOW, analyst_model_id, quota_from
from khadbot.jobs.status import JobKind, JobState, JobStore, MemoryJobBackend, NewJob
from khadbot.jobs.usage import MemoryUsageLedger

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
PULL = "SYNTH01:1"
_ids = itertools.count()

# ---------------------------------------------------------------------------
# Model and quota
# ---------------------------------------------------------------------------


def test_the_analyst_model_is_the_role_override_else_the_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-x")
    monkeypatch.delenv("ANALYST_MODEL", raising=False)
    reset_config()
    assert analyst_model_id() == "claude-x"
    monkeypatch.setenv("ANALYST_MODEL", "claude-y")
    reset_config()
    assert analyst_model_id() == "claude-y"
    reset_config()


def test_a_quota_with_room_has_no_next_slot():
    q = quota_from([NOW, NOW + timedelta(hours=1)], limit=3)
    assert (q.limit, q.used, q.remaining, q.next_slot_at) == (3, 2, 1, None)


def test_a_full_quota_frees_a_slot_when_the_oldest_counted_review_ages_out():
    times = [NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=2)]
    q = quota_from(times, limit=2)  # three counted against two: the second-oldest must age out
    assert (q.remaining, q.next_slot_at) == (0, NOW + timedelta(hours=1) + REVIEW_WINDOW)


def test_a_limit_of_zero_allows_nothing_and_never_frees():
    q = quota_from([], limit=0)
    assert (q.remaining, q.next_slot_at) == (0, None)


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------


def new(user: str = "u1", pull: str = PULL, **spec) -> NewJob:
    return NewJob(
        id=f"job{next(_ids)}",
        kind=JobKind.REVIEW,
        report_code=pull.rpartition(":")[0],
        pull_key=pull,
        requested_by=user,
        spec=ReviewPull(pull_key=pull, **spec).model_dump(mode="json"),
    )


@pytest.fixture
def store() -> JobStore:
    return JobStore(MemoryJobBackend())


async def claim(store, job, limit=2):
    return await store.claim_review(job, limit=limit, since=datetime.now(UTC) - REVIEW_WINDOW)


async def test_reviews_are_claimed_until_the_limit_then_refused(store):
    first = await claim(store, new(pull="SYNTH01:1"))
    second = await claim(store, new(pull="SYNTH01:2"))
    third = await claim(store, new(pull="SYNTH01:3"))
    assert [c.created for c in (first, second, third)] == [True, True, False]
    assert third.job is None and len(third.counted) == 2


async def test_another_users_quota_is_their_own(store):
    await claim(store, new("u1", "SYNTH01:1"), limit=1)
    assert (await claim(store, new("u1", "SYNTH01:2"), limit=1)).job is None
    assert (await claim(store, new("u2", "SYNTH01:2"), limit=1)).created


async def test_an_identical_active_review_is_returned_and_costs_nothing(store):
    mine = await claim(store, new("u1"), limit=1)
    theirs = await claim(store, new("u2"), limit=1)
    assert not theirs.created and theirs.job is not None and theirs.job.id == mine.job.id
    assert theirs.counted == []  # u2 used nothing
    assert await store.reviews_since("u2", NOW) == []
    # a review at another depth is a different job, and the limit-1 user has no slot left for it
    assert (await claim(store, new("u1", depth="deep"), limit=1)).job is None


async def test_failed_and_cancelled_reviews_give_the_slot_back(store):
    first = await claim(store, new(), limit=1)
    assert (await claim(store, new(pull="SYNTH01:2"), limit=1)).job is None
    await store.finish(first.job.id, JobState.FAILED, error_code="internal_error")
    again = await claim(store, new(pull="SYNTH01:2"), limit=1)
    assert again.created
    await store.finish(again.job.id, JobState.CANCELLED)
    assert (await claim(store, new(pull="SYNTH01:3"), limit=1)).created


async def test_a_review_older_than_the_window_is_not_counted(store):
    await claim(store, new(), limit=1)
    later = datetime.now(UTC) + REVIEW_WINDOW + timedelta(minutes=1)
    assert await store.reviews_since("u1", later - REVIEW_WINDOW) == []


# ---------------------------------------------------------------------------
# A worker running a review
# ---------------------------------------------------------------------------


@pytest.fixture
def analyst(role_fakes, monkeypatch):
    monkeypatch.setenv("ANALYST_MODEL", "test-analyst")
    reset_config()
    model = UsageFakeModel(respond=respond, model_name="test-analyst")
    role_fakes["analyst"] = model
    yield model
    reset_config()


@pytest.fixture
def queue_env(tmp_path, analyst):
    env = synth_env(tmp_path)
    env.context.findings = SqliteFindingsStore(tmp_path / "findings.sqlite")
    ledger = MemoryUsageLedger()
    backend = MemoryJobBackend()
    queue = JobQueue(
        JobStore(backend),
        InMemoryConnector(),
        config=QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.01),
        context_factory=lambda: env.context,
        usage=ledger,
    )
    return env, queue, backend, ledger


async def drain(queue):
    async with queue.open():
        await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)


async def ingest(env):
    from helpers.jobs.ingest import fetch_and_normalize

    from khadbot.jobs import IngestReport

    await fetch_and_normalize(IngestReport(code="SYNTH01", offline=True), env.context)


async def test_a_review_job_writes_findings_and_one_usage_row_per_model_call(queue_env, analyst):
    env, queue, backend, ledger = queue_env
    await ingest(env)
    spec = ReviewPull(pull_key=PULL, model="test-analyst")
    async with queue.open():
        queued = await queue.enqueue_review(spec, requested_by="u1", limit=5)
    assert queued.created and queued.quota.used == 1
    await drain(queue)

    row = backend.rows[queued.job.id]
    assert row.state is JobState.DONE and row.progress.pulls[PULL].fraction == 1.0
    assert [f.claim for pf in env.context.findings.for_pull(PULL) for f in pf.findings] == ["The pull was reviewed"]
    assert len(ledger.entries) == analyst.calls > 0
    for e in ledger.entries:
        assert (e.kind.value, e.model, e.user_id, e.job_id, e.report_code) == (
            "llm",
            "test-analyst",
            "u1",
            queued.job.id,
            "SYNTH01",
        )
        assert (e.input_tokens, e.cached_input_tokens, e.cache_write_tokens, e.output_tokens) == (1000, 400, 100, 200)
        assert e.cost_usd is None  # no price for the test model: tokens recorded, cost unknown


async def test_a_review_on_another_model_fails_before_any_model_call(queue_env, analyst):
    env, queue, backend, ledger = queue_env
    await ingest(env)
    async with queue.open():
        queued = await queue.enqueue_review(ReviewPull(pull_key=PULL, model="other"), requested_by="u1", limit=5)
    await drain(queue)
    row = backend.rows[queued.job.id]
    assert (row.state, row.error_code) == (JobState.FAILED, "model_mismatch")
    assert analyst.calls == 0 and ledger.entries == []
    assert await queue.store.reviews_since("u1", NOW) == []  # failed: the slot is back


async def test_enqueue_review_is_refused_over_the_limit(queue_env):
    env, queue, backend, ledger = queue_env
    async with queue.open():
        a = await queue.enqueue_review(ReviewPull(pull_key="SYNTH01:1"), requested_by="u1", limit=1)
        b = await queue.enqueue_review(ReviewPull(pull_key="SYNTH01:2"), requested_by="u1", limit=1)
    assert a.job is not None and b.job is None
    assert (b.quota.used, b.quota.remaining, b.quota.next_slot_at is not None) == (1, 0, True)
    assert len(backend.rows) == 1


def test_the_fake_provider_is_named_fake_by_the_config_and_by_the_model(monkeypatch):
    from khadbot import llm_factory
    from khadbot.encounter.analyst.graph import model_id_of

    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("ANALYST_MODEL", "some-real-model")  # an override that the fake provider ignores
    reset_config()
    try:
        assert analyst_model_id() == "fake"
        assert model_id_of(llm_factory.get_llm(role="analyst")) == "fake"  # what the job compares it with
    finally:
        reset_config()
