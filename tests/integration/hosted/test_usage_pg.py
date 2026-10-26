"""
tests/integration/hosted/test_usage_pg.py

The usage ledger on real Postgres (migration 0008): rows round-trip, a job's WCL spend is written once, the guild
comes from ``reports.guild_id`` when the caller names none, totals group by user, guild and kind, and an ingest job run
by a worker leaves one WCL row that matches its ``jobs.points_spent``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from helpers.jobs.budget import CountingWclClient
from helpers.jobs.synth_queue import synth_env
from procrastinate.testing import InMemoryConnector

from khadbot.config import QueueConfig
from khadbot.db import migrate
from khadbot.jobs import IngestReport
from khadbot.jobs.queue import JobQueue
from khadbot.jobs.status import JobStore, PostgresJobBackend
from khadbot.jobs.usage import LlmUsage, PostgresUsageLedger, llm_entry, wcl_entry

EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


def seed(conn) -> None:
    conn.execute(
        "INSERT INTO users (id, wcl_user_id, name, created_at, last_login_at)"
        " VALUES ('u1', 1, 'One', now(), now()), ('u2', 2, 'Two', now(), now())"
    )
    conn.execute("INSERT INTO reports (code, guild_id) VALUES ('GUILDED', 77), ('LONER', NULL)")


def test_rows_round_trip_and_a_jobs_wcl_spend_is_written_once(pool):
    migrate(pool)
    ledger = PostgresUsageLedger(pool)
    with pool.connection() as conn:
        seed(conn)
        conn.execute(
            "INSERT INTO jobs (id, kind, report_code, auth_kind, spec, created_at, updated_at) "
            "VALUES ('j1', 'ingest', 'GUILDED', 'app', '{}', now(), now())"
        )

    assert ledger.record(wcl_entry(30.5, job_id="j1", user_id="u1", report_code="GUILDED"))
    assert not ledger.record(wcl_entry(30.5, job_id="j1", user_id="u1", report_code="GUILDED"))  # a repeat settlement
    assert ledger.record(llm_entry("claude-sonnet-5-5", LlmUsage(input_tokens=1000, output_tokens=100), job_id="j1"))

    with pool.connection() as conn:
        rows = conn.execute("SELECT kind, wcl_points, guild_id, cost_usd FROM usage ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["wcl", "llm"]
    assert rows[0][1] == 30.5 and rows[0][2] == 77  # the guild came from reports.guild_id
    assert rows[1][3] is not None and rows[1][3] > 0


def test_totals_group_by_user_guild_and_kind(pool):
    migrate(pool)
    ledger = PostgresUsageLedger(pool)
    with pool.connection() as conn:
        seed(conn)
    ledger.record(wcl_entry(30, user_id="u1", report_code="GUILDED"))
    ledger.record(wcl_entry(12, user_id="u2", report_code="LONER"))
    ledger.record(llm_entry("claude-sonnet-5-5", LlmUsage(input_tokens=1_000_000, output_tokens=0), user_id="u1"))

    assert {t.key: t.wcl_points for t in ledger.totals(EPOCH, "user")} == {"u1": 30, "u2": 12}
    by_guild = {t.key: t for t in ledger.totals(EPOCH, "guild")}
    assert by_guild["77"].wcl_points == 30 and by_guild["(none)"].calls == 2
    by_kind = {t.key: t for t in ledger.totals(EPOCH, "kind")}
    assert by_kind["wcl"].wcl_points == 42 and by_kind["llm"].cost_usd > 0
    assert ledger.totals(datetime(2999, 1, 1, tzinfo=UTC), "kind") == []


async def test_an_ingest_job_leaves_one_wcl_row_matching_its_points(pool, tmp_path):
    migrate(pool)
    env = synth_env(tmp_path)
    env.context.client = CountingWclClient(15.0)  # type: ignore[assignment]
    with pool.connection() as conn:
        seed(conn)
    ledger = PostgresUsageLedger(pool)
    queue = JobQueue(
        JobStore(PostgresJobBackend(pool)),
        InMemoryConnector(),
        config=QueueConfig(max_retries=1, retry_backoff_s=0, progress_interval_s=0.01),
        context_factory=lambda: env.context,
        usage=ledger,
    )
    async with queue.open():
        job = (await queue.enqueue(IngestReport(code="SYNTH01", fights=[1]), requested_by="u1")).job
        await queue.run_worker(wait=False, listen_notify=False, install_signal_handlers=False)

    with pool.connection() as conn:
        spent = conn.execute("SELECT points_spent FROM jobs WHERE id = %s", (job.id,)).fetchone()[0]
        rows = conn.execute(
            "SELECT wcl_points, user_id, report_code FROM usage WHERE job_id = %s", (job.id,)
        ).fetchall()
    assert rows == [(spent, "u1", "SYNTH01")] and spent == 15.0
