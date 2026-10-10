"""
tests/unit/encounter/test_commands.py

/ingest argument parsing, result formatting, and the command handler wired to a patched ingest_report.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from khadbot.encounter.commands import (
    UsageError,
    format_ingest_result,
    ingest_command,
    ingest_parser,
    parse_fights,
)
from khadbot.encounter.models import IngestResult


def result(**kw) -> IngestResult:
    base = {"report_code": "ABC", "pulls": [], "skipped": [], "failed": {}, "points_spent": None}
    return IngestResult(**{**base, **kw})


class TestParsing:
    def test_parse_fights(self):
        assert parse_fights("3,4") == [3, 4]
        assert parse_fights(" 3, 4 ") == [3, 4]
        assert parse_fights(None) is None
        assert parse_fights("") is None

    def test_parse_fights_rejects_junk(self):
        with pytest.raises(UsageError):
            parse_fights("a,b")

    def test_parser_accepts_documented_forms(self):
        ns = ingest_parser().parse_args(["ABC", "--fights", "3,4", "--offline"])
        assert (ns.code, ns.fights, ns.offline) == ("ABC", "3,4", True)

    def test_parser_missing_code_is_a_usage_error(self):
        with pytest.raises(UsageError, match="Usage"):
            ingest_parser().parse_args([])


class TestFormat:
    def test_lists_fights_per_outcome(self):
        text = format_ingest_result(result(pulls=["ABC:1", "ABC:2"], skipped=["ABC:3"]))
        assert "| Ingested | 1, 2 |" in text
        assert "| Skipped (unchanged) | 3 |" in text
        assert "| Failed | - |" in text

    def test_failures_and_points(self):
        text = format_ingest_result(result(failed={"ABC:4": "boom"}, points_spent=12.34))
        assert "`ABC:4`: boom" in text
        assert "12.3" in text


class TestHandler:
    async def test_runs_ingest_with_parsed_args(self):
        with patch(
            "khadbot.encounter.commands.ingest_report", new=AsyncMock(return_value=result(pulls=["ABC:3"]))
        ) as m:
            out = await ingest_command("ABC --fights 3 --offline")
        assert m.await_args.args == ("ABC", [3])
        assert m.await_args.kwargs["offline"] is True
        assert "| Ingested | 3 |" in out

    async def test_bad_args_return_usage_without_ingesting(self):
        with patch("khadbot.encounter.commands.ingest_report", new=AsyncMock()) as m:
            out = await ingest_command("")
        m.assert_not_awaited()
        assert "Usage: /ingest" in out
