"""
tests/unit/jobs/test_usage.py

The usage ledger on its in-memory store: WCL rows are written once per job, LLM rows get a cost from the price table
(fake usage, no model call), totals group by user, guild and kind, and the report formats them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from khadbot.jobs.model_prices import PriceTable
from khadbot.jobs.usage import (
    LlmUsage,
    MemoryUsageLedger,
    format_usage_report,
    record_llm_usage,
    record_wcl_points,
)

PRICES = PriceTable.model_validate(
    {
        "checked": "2026-01-01",
        "source": "https://example.test/pricing",
        "models": {"m": {"input": 2, "cache_write": 2.5, "cache_read": 0.1, "output": 10}},
    }
)
EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


async def test_a_jobs_wcl_points_are_written_once():
    ledger = MemoryUsageLedger()
    assert await record_wcl_points(ledger, 42.5, job_id="j1", user_id="u1", report_code="R1")
    assert not await record_wcl_points(ledger, 42.5, job_id="j1", user_id="u1", report_code="R1")
    (row,) = ledger.entries
    assert (row.kind.value, row.wcl_points, row.job_id, row.user_id, row.report_code) == ("wcl", 42.5, "j1", "u1", "R1")


@pytest.mark.parametrize("points", [None, 0, 0.0])
async def test_a_job_that_spent_nothing_writes_no_row(points):
    ledger = MemoryUsageLedger()
    assert not await record_wcl_points(ledger, points, job_id="j1")
    assert ledger.entries == []


async def test_an_llm_call_is_costed_from_the_price_table():
    ledger = MemoryUsageLedger()
    entry = await record_llm_usage(
        ledger,
        model="m",
        usage=LlmUsage(input_tokens=1_000_000, cached_input_tokens=500_000, output_tokens=100_000),
        job_id="j1",
        user_id="u1",
        prices=PRICES,
    )
    assert entry.cost_usd == pytest.approx(1.0 + 0.05 + 1.0)
    assert ledger.entries == [entry] and entry.kind.value == "llm" and entry.model == "m"


async def test_a_langchain_usage_mapping_is_read_with_its_cache_counts():
    ledger = MemoryUsageLedger()
    entry = await record_llm_usage(
        ledger,
        model="m",
        usage={
            "input_tokens": 1000,
            "output_tokens": 200,
            "total_tokens": 1200,
            "input_token_details": {"cache_read": 600, "cache_creation": 100},
        },
        prices=PRICES,
    )
    assert (entry.input_tokens, entry.cached_input_tokens, entry.cache_write_tokens, entry.output_tokens) == (
        1000,
        600,
        100,
        200,
    )
    assert entry.cost_usd == pytest.approx((300 * 2 + 600 * 0.1 + 100 * 2.5 + 200 * 10) / 1e6)


async def test_a_model_without_a_price_is_recorded_with_a_null_cost():
    ledger = MemoryUsageLedger()
    entry = await record_llm_usage(
        ledger, model="unlisted", usage=LlmUsage(input_tokens=5, output_tokens=5), prices=PRICES
    )
    assert entry.cost_usd is None and entry.input_tokens == 5


async def test_totals_group_by_user_guild_and_kind():
    ledger = MemoryUsageLedger(guilds={"R1": 7})
    await record_wcl_points(ledger, 30, job_id="j1", user_id="u1", report_code="R1")
    await record_wcl_points(ledger, 12, job_id="j2", user_id="u2", report_code="R2")
    await record_llm_usage(
        ledger, model="m", usage=LlmUsage(input_tokens=1_000_000, output_tokens=0), user_id="u1", prices=PRICES
    )

    by_kind = {t.key: t for t in ledger.totals(EPOCH, "kind")}
    assert by_kind["wcl"].wcl_points == 42 and by_kind["llm"].cost_usd == pytest.approx(2.0)
    by_user = {t.key: t for t in ledger.totals(EPOCH, "user")}
    assert by_user["u1"].wcl_points == 30 and by_user["u1"].calls == 2 and by_user["u2"].wcl_points == 12
    by_guild = {t.key: t for t in ledger.totals(EPOCH, "guild")}
    assert by_guild["7"].wcl_points == 30  # R1's guild came from the reports
    assert by_guild["(none)"].calls == 2  # R2 has none, and the model call named no report


async def test_totals_skip_rows_before_since():
    ledger = MemoryUsageLedger()
    await record_wcl_points(ledger, 5, job_id="j1")
    assert ledger.totals(datetime.now(UTC) + timedelta(hours=1), "kind") == []


async def test_the_report_lists_each_group_and_a_total():
    ledger = MemoryUsageLedger()
    await record_wcl_points(ledger, 30, job_id="j1", user_id="u1")
    await record_llm_usage(ledger, model="m", usage=LlmUsage(input_tokens=1_000_000, output_tokens=0), prices=PRICES)
    text = format_usage_report(ledger.totals(EPOCH, "kind"), "kind", datetime(2026, 10, 1, tzinfo=UTC))
    lines = text.splitlines()
    assert lines[0] == "Usage since 2026-10-01 by kind"
    assert lines[1].split()[:2] == ["kind", "calls"]
    assert [ln.split()[0] for ln in lines[2:4]] == ["llm", "wcl"]
    assert lines[-1].startswith("total") and "$2.0000" in lines[-1] and "30.0" in lines[-1]


def test_an_empty_ledger_says_so():
    assert "No usage recorded." in format_usage_report([], "user", EPOCH)
