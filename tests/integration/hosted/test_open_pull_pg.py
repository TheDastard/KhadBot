"""
tests/integration/hosted/test_open_pull_pg.py

Opening one pull on the spot through the queue on real Postgres, with WCL answered by replay: the job covers all three
steps of the pull, its event stream shows them finish and describes the pull, a second visitor asking for the same
pull while it is queued gets the same job, and once it has run the pull counts as loaded.

Each test runs its coroutine on a selector event loop, which psycopg's async connections need on Windows.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from helpers.wcl.replay import WCL_FIXTURES, guard_network, replay_client, replay_env
from procrastinate import PsycopgConnector

from khadbot.api.jobs import event_stream, job_view
from khadbot.auth.pg import PostgresAuthStore
from khadbot.config import QueueConfig
from khadbot.db import PostgresReadModelProjector, migrate
from khadbot.jobs import IngestReport
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobStore, PostgresJobBackend
from khadbot.wcl.replay import ReplayTransport

CONFIG = QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.05)


def run[T](coro: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(coro(), loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]


@pytest.fixture
def no_network(monkeypatch):
    attempts = guard_network(monkeypatch)
    yield
    assert attempts == []


def test_one_pull_opens_through_three_done_steps_and_a_second_visitor_follows_the_job(
    pool, database_url, schema_name, tmp_path, no_network
):
    migrate(pool)
    users = PostgresAuthStore(pool)
    visitor_a = users.upsert_user(1001, "Jaina", datetime.now(UTC)).id
    visitor_b = users.upsert_user(1002, "Thrall", datetime.now(UTC)).id

    async def go():
        async with replay_client(ReplayTransport(WCL_FIXTURES)) as client:
            env = replay_env(tmp_path / "env", client, synthetic=True)
            env.context.projector = PostgresReadModelProjector(env.raw, pool=lambda: pool)
            connector = PsycopgConnector(
                conninfo=database_url, min_size=1, max_size=6, kwargs={"options": f"-c search_path={schema_name}"}
            )
            store = JobStore(PostgresJobBackend(pool))
            queue = JobQueue(store, connector, config=CONFIG, context_factory=lambda: env.context)
            async with queue.open():
                spec = IngestReport(code="SYNTH01", fights=[1])
                first = await queue.enqueue(spec, requested_by=visitor_a)
                second = await queue.enqueue(spec, requested_by=visitor_b)  # opens the same pull mid-run
                assert second.job.id == first.job.id and not second.created
                before = job_view(await store.get(first.job.id), own=False)
                assert [(p.pull_key, p.status.value) for p in before.pulls] == [("SYNTH01:1", "waiting")]
                assert not before.can_cancel

                stream = event_stream(store, first.job.id, keepalive_s=5, own=False)
                events: list[tuple[str, dict]] = []

                async def follow() -> None:
                    async for chunk in stream:
                        lines = chunk.strip().split("\n")
                        events.append((lines[1].removeprefix("event: "), json.loads(lines[2].removeprefix("data: "))))

                follower = asyncio.ensure_future(follow())
                for _ in range(12):
                    await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)
                    if follower.done():
                        break
                await asyncio.wait_for(follower, 30)
            return env, events, await store.get(first.job.id)

    env, events, row = run(go)
    assert events[0][0] == "job.state" and events[-1][0] == "job.done"
    final = events[-1][1]
    (pull,) = final["pulls"]
    assert pull["encounter_name"] and pull["encounter_id"]
    assert [(s["name"], s["state"], s["pct"]) for s in pull["steps"]] == [
        ("download", "done", 100.0),
        ("prepare", "done", 100.0),
        ("analyse", "done", 100.0),
    ]
    assert final["can_cancel"] is False and final["points_spent"] is None  # the follower is not the requester
    assert row.state.value == "done" and row.progress.info["SYNTH01:1"].encounter_id == pull["encounter_id"]

    # The pull is loaded now: its digest answers, and its records are stored (what a repeat open checks).
    assert env.context.encounter_data().records("SYNTH01:1").digest
    assert any(k.rsplit("/", 1)[-1].startswith("1__") for k in env.records.keys("SYNTH01"))
