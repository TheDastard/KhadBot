"""
tests/unit/api/test_jobs.py

The pieces of the ingest router that need no HTTP: parsing a report code or URL, the typed errors a job row shows
(including a budget wait's ETA from the budget manager) and the event stream over the in-memory ``jobs`` table.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from helpers.jobs.budget import FakeReader, MovableClock, budget_config

from khadbot.api.jobs import budget_eta, event_stream, failure_for, job_view, parse_source, request_eta
from khadbot.api.views import ApiError
from khadbot.config import QueueConfig
from khadbot.jobs.budget import Ask
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend
from khadbot.jobs.failures import ErrorCode
from khadbot.jobs.status import JobKind, JobRecord, JobState, JobStore, MemoryJobBackend, NewJob

# ---------------------------------------------------------------------------
# parse_source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "fights", "expected"),
    [
        ("aBcD1234eFgH5678", None, ("aBcD1234eFgH5678", None)),
        ("  aBcD1234eFgH5678 ", [], ("aBcD1234eFgH5678", None)),
        ("https://www.warcraftlogs.com/reports/abc123", None, ("abc123", None)),
        ("https://www.warcraftlogs.com/reports/abc123#fight=7", None, ("abc123", [7])),
        ("https://www.warcraftlogs.com/reports/abc123?fight=7&type=damage-done", [], ("abc123", [7])),
        ("https://www.warcraftlogs.com/reports/abc123#fight=7", [2, 3, 2], ("abc123", [2, 3])),
        ("https://www.warcraftlogs.com/reports/abc123#fight=last", None, ("abc123", None)),
        ("https://classic.warcraftlogs.com/reports/abc123/", None, ("abc123", None)),
        ("warcraftlogs.com/reports/abc123", None, ("abc123", None)),
    ],
)
def test_parse_source(source, fights, expected):
    assert parse_source(source, fights) == expected


@pytest.mark.parametrize(
    "source",
    [
        "https://evil.example/reports/abc123",
        "https://warcraftlogs.com.evil.example/reports/abc123",
        "https://www.warcraftlogs.com/guild/id/1",
        "https://www.warcraftlogs.com/reports/",
        "not a report",
        "abc/../x",
    ],
)
def test_parse_source_refuses_other_sources(source):
    with pytest.raises(ApiError) as exc:
        parse_source(source, None)
    assert (exc.value.status, exc.value.code) == (400, "bad_source")


def test_parse_source_refuses_fight_ids_below_one():
    with pytest.raises(ApiError) as exc:
        parse_source("abc123", [0])
    assert exc.value.code == "bad_fights"


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def record(**kw) -> JobRecord:
    base = {
        "id": "j1",
        "kind": JobKind.INGEST,
        "report_code": "ABC",
        "spec": {},
        "created_at": NOW,
        "updated_at": NOW,
    }
    return JobRecord(**{**base, **kw})


@pytest.mark.parametrize(
    ("stored", "api"),
    [
        (ErrorCode.SIGN_IN_REQUIRED, "wcl_not_signed_in"),
        (ErrorCode.PRIVATE_REPORT, "report_not_found"),
        (ErrorCode.RATE_LIMITED, "rate_limited"),
        (ErrorCode.NETWORK, "wcl_unreachable"),
        (ErrorCode.WCL_ERROR, "wcl_error"),
        (ErrorCode.NOT_IN_CACHE, "not_in_cache"),
        (ErrorCode.PULLS_FAILED, "pulls_failed"),
        (ErrorCode.INTERRUPTED, "interrupted"),
        (ErrorCode.INTERNAL, "internal_error"),
        (ErrorCode.MODEL_MISMATCH, "internal_error"),
        ("something_new", "internal_error"),
    ],
)
def test_a_failed_job_shows_a_typed_error(stored, api):
    rec = record(state=JobState.FAILED, error_code=str(stored), error_message="why")
    error = failure_for(rec, QueueConfig(), NOW)
    assert error is not None and (error.code.value, error.message) == (api, "why")


def test_a_rate_limited_job_waiting_for_a_retry_shows_the_seconds_left():
    rec = record(
        state=JobState.QUEUED, error_code="wcl_rate_limited", attempts=2, updated_at=NOW - timedelta(seconds=4)
    )
    error = failure_for(rec, QueueConfig(retry_backoff_s=3), NOW)
    assert error is not None and error.code.value == "rate_limited"
    assert error.retry_after_s == pytest.approx(5.0)  # 3 ** 2 seconds, 4 already passed


def test_a_job_waiting_for_the_budget_shows_budget_wait_and_its_eta():
    rec = record(state=JobState.WAITING_BUDGET)
    error = failure_for(rec, QueueConfig(), NOW)
    assert error is not None and (error.code.value, error.eta_s) == ("budget_wait", None)  # no estimate given
    assert failure_for(rec, QueueConfig(), NOW, eta_s=42.0).eta_s == 42.0
    view = job_view(rec, eta_s=42.0)
    assert view.state.value == "waiting_budget" and view.error is not None and view.error.eta_s == 42.0


async def test_the_eta_of_a_waiting_job_comes_from_the_budget_manager():
    clock = MovableClock()
    manager = BudgetManager(MemoryBudgetBackend(), budget_config(), clock=clock, reader=FakeReader())
    await manager.sync()  # 1000 points, the window resets in 1800 s; one user may use 100 of them
    await manager.request(Ask(job_id="a", report_code="R", user_id="u1", points=60, asked_at=clock()))
    await manager.request(Ask(job_id="b", report_code="R", user_id="u1", points=60, asked_at=clock()))
    wait = await manager.wait_of("b")
    assert wait is not None  # b would take u1 past its allowance, so it waits for the window to turn

    eta_s = budget_eta(manager)
    assert await eta_s(record(id="b", state=JobState.WAITING_BUDGET)) == pytest.approx(
        (wait.eta - datetime.now(UTC)).total_seconds(), abs=5
    )
    assert await eta_s(record(id="a", state=JobState.RUNNING)) is None  # only a waiting job has an ETA
    assert await eta_s(record(id="gone", state=JobState.WAITING_BUDGET)) is None  # not in the budget's line


async def test_the_request_lookup_builds_no_budget_manager_for_a_job_that_is_not_waiting():
    # app.state has no budget: building one would open a Postgres pool, so only a waiting job may ask for it.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    lookup = request_eta(request)  # type: ignore[arg-type]
    for state in (JobState.QUEUED, JobState.RUNNING, JobState.DONE):
        assert await lookup(record(state=state)) is None
    assert not hasattr(request.app.state, "budget")


async def test_the_stream_shows_a_waiting_jobs_eta():
    store = JobStore(MemoryJobBackend())
    await store.create(new_job())
    assert await store.wait_for_budget("j1")

    async def eta(rec: JobRecord) -> float | None:
        return 42.0 if rec.state is JobState.WAITING_BUDGET else None

    stream = event_stream(store, "j1", keepalive_s=30, eta=eta)
    first = await asyncio.wait_for(anext(stream), 2)
    assert first.startswith("id: 1\nevent: job.state\n")
    assert '"code":"budget_wait"' in first and '"eta_s":42.0' in first
    await stream.aclose()


def test_finished_and_cancelled_jobs_show_no_error():
    assert failure_for(record(state=JobState.DONE), QueueConfig(), NOW) is None
    cancelled = record(state=JobState.CANCELLED, error_code="already_queued")
    assert failure_for(cancelled, QueueConfig(), NOW) is None


# ---------------------------------------------------------------------------
# The event stream
# ---------------------------------------------------------------------------


def new_job(job_id: str = "j1") -> NewJob:
    return NewJob(id=job_id, kind=JobKind.INGEST, report_code="ABC", spec={"job": "ingest_report", "code": "ABC"})


async def collect(stream, n: int) -> list[str]:
    out = []
    async for chunk in stream:
        out.append(chunk)
        if len(out) == n:
            break
    return out


async def test_a_change_reaches_the_stream_in_well_under_a_second():
    backend = MemoryJobBackend()
    store = JobStore(backend)
    await store.create(new_job())
    await store.begin("j1")
    stream = event_stream(store, "j1", keepalive_s=30)
    first = await asyncio.wait_for(anext(stream), 2)
    assert first.startswith("id: 1\nevent: job.state\n")
    pending = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.05)
    started = time.perf_counter()
    await store.report_progress("j1", "ABC:1", "normalizing", 0.5)
    second = await asyncio.wait_for(pending, 2)
    elapsed = time.perf_counter() - started
    assert second.startswith("id: 2\nevent: pull.progress\n") and '"status":"normalizing"' in second
    assert elapsed < 1.0
    await stream.aclose()
    assert backend._subscribers == []  # the subscription is closed with the stream


async def test_a_lost_notification_is_caught_by_the_keepalive_reread():
    backend = MemoryJobBackend()
    store = JobStore(backend)
    await store.create(new_job())
    await store.begin("j1")
    stream = event_stream(store, "j1", keepalive_s=0.05)
    await anext(stream)
    rec = backend.rows["j1"]
    backend.rows["j1"] = rec.model_copy(update={"state": JobState.CANCELLED})  # changed with no announcement
    chunks = [c async for c in stream]
    assert chunks[0] == ": keepalive\n\n"
    assert "event: job.state" in chunks[1] and '"state":"cancelled"' in chunks[1]
    assert len(chunks) == 2  # a cancelled job ends with its state event alone
    assert backend._subscribers == []


async def test_a_finished_job_streams_state_then_done_and_closes():
    backend = MemoryJobBackend()
    store = JobStore(backend)
    await store.create(new_job())
    await store.begin("j1")
    await store.finish("j1", JobState.DONE)
    chunks = [c async for c in event_stream(store, "j1", keepalive_s=30)]
    assert [c.split("\n")[1] for c in chunks] == ["event: job.state", "event: job.done"]
    assert backend._subscribers == []


async def test_a_failed_job_ends_with_the_error_event():
    backend = MemoryJobBackend()
    store = JobStore(backend)
    await store.create(new_job())
    await store.begin("j1")
    stream = event_stream(store, "j1", keepalive_s=30)
    await anext(stream)
    pending = asyncio.ensure_future(collect(stream, 2))
    await asyncio.sleep(0.05)
    await store.finish("j1", JobState.FAILED, error_code="wcl_private_report", error_message="Private.")
    chunks = await asyncio.wait_for(pending, 2)
    assert [c.split("\n")[1] for c in chunks] == ["event: job.state", "event: error"]
    assert '"code":"report_not_found"' in chunks[1]


async def test_abandoning_the_stream_closes_the_subscription():
    backend = MemoryJobBackend()
    store = JobStore(backend)
    await store.create(new_job())
    stream = event_stream(store, "j1", keepalive_s=0.01)
    await anext(stream)
    await anext(stream)  # a keepalive: a wait for the next notification is pending
    await stream.aclose()
    assert backend._subscribers == []


async def test_other_jobs_notifications_do_not_emit_events():
    backend = MemoryJobBackend()
    store = JobStore(backend)
    await store.create(new_job("j1"))
    await store.create(new_job("j2"))
    await store.begin("j1")
    stream = event_stream(store, "j1", keepalive_s=30)
    await anext(stream)
    pending = asyncio.ensure_future(anext(stream))
    await store.begin("j2")
    await store.report_progress("j2", "ABC:1", "normalizing", 0.5)
    await asyncio.sleep(0.1)
    assert not pending.done()
    await store.report_progress("j1", "ABC:2", "normalizing", 0.5)
    assert '"pull_key":"ABC:2"' in await asyncio.wait_for(pending, 2)
    await stream.aclose()
