"""
tests/unit/jobs/test_model_prices.py

The model price table: the shipped file loads and names its source and check date, the estimate bills plain input,
cache reads, cache writes and output at their own rates, a prompt over the long-context line gets the other rates, and
an unknown model gets no estimate.
"""

from __future__ import annotations

import pytest

from khadbot.errors import ConfigurationError
from khadbot.jobs.model_prices import PriceTable, load_price_table

TABLE = PriceTable.model_validate(
    {
        "checked": "2026-01-01",
        "source": "https://example.test/pricing",
        "models": {
            "model-a": {"input": 2, "cache_write": 2.5, "cache_read": 0.1, "output": 10},
            "model-b": {
                "input": 0.1,
                "cache_write": 0.125,
                "cache_read": 0.01,
                "output": 0.5,
                "long_context": {
                    "above_input_tokens": 100,
                    "input": 0.5,
                    "cache_write": 0.625,
                    "cache_read": 0.05,
                    "output": 2.5,
                },
            },
        },
    }
)


def test_the_shipped_table_names_its_source_and_date():
    table = load_price_table()
    assert table.source.startswith("https://platform.claude.com/") and table.checked.year >= 2026
    assert {"claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"} <= set(table.models)


def test_each_part_of_a_call_is_billed_at_its_own_rate():
    cost = TABLE.estimate_cost(
        "model-a", input_tokens=1_000_000, cached_input_tokens=400_000, cache_write_tokens=100_000, output_tokens=50_000
    )
    # 500k plain at 2, 400k read at 0.1, 100k written at 2.5, 50k out at 10
    assert cost == pytest.approx(1.0 + 0.04 + 0.25 + 0.5)


def test_a_long_prompt_uses_the_long_context_rates():
    short = TABLE.estimate_cost("model-b", input_tokens=100, output_tokens=1_000_000)
    long = TABLE.estimate_cost("model-b", input_tokens=101, output_tokens=1_000_000)
    assert short == pytest.approx(100 * 0.1 / 1e6 + 0.5)
    assert long == pytest.approx(101 * 0.5 / 1e6 + 2.5)


def test_a_dated_snapshot_uses_the_listed_model_and_an_unknown_one_has_no_estimate():
    assert TABLE.estimate_cost("model-a-20260101", input_tokens=1_000_000) == pytest.approx(2.0)
    assert TABLE.estimate_cost("some-other-model", input_tokens=10) is None
    assert TABLE.price_of("model-ax") is None  # a longer name is not the same model


def test_an_invalid_file_fails_at_load(tmp_path):
    bad = tmp_path / "models.yaml"
    bad.write_text("checked: not-a-date\nmodels: {}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_price_table(bad)
    with pytest.raises(ConfigurationError):
        load_price_table(tmp_path / "missing.yaml")
