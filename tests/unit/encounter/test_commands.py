"""
tests/unit/encounter/test_commands.py

/ingest argument parsing, result formatting, and the command handler wired to a patched ingest_report;
/digest, /pulls and /review against SYNTH01 ingested into a temp store.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from khadbot.encounter import commands
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.commands import (
    UsageError,
    digest_command,
    format_ingest_result,
    ingest_command,
    ingest_parser,
    parse_fights,
    pulls_command,
    resolve_pull_key,
    review_command,
)
from khadbot.encounter.findings import FindingsStore
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
        # Fetch filters come from the cooldown registry (and any authored encounter files).
        assert 98008 in m.await_args.kwargs["registry_spell_ids"]
        assert isinstance(m.await_args.kwargs["enemy_cast_ids"], dict)

    async def test_bad_args_return_usage_without_ingesting(self):
        with patch("khadbot.encounter.commands.ingest_report", new=AsyncMock()) as m:
            out = await ingest_command("")
        m.assert_not_awaited()
        assert "Usage: /ingest" in out


class TestIngestRemembersReport:
    async def test_sets_last_report(self, monkeypatch):
        monkeypatch.setattr(commands, "_last_report", None)
        with patch("khadbot.encounter.commands.ingest_report", new=AsyncMock(return_value=result(skipped=["ABC:3"]))):
            await ingest_command("ABC")
        assert commands._last_report == "ABC"

    async def test_failed_ingest_does_not(self, monkeypatch):
        monkeypatch.setattr(commands, "_last_report", None)
        failed = result(failed={"ABC:3": "boom"})
        with patch("khadbot.encounter.commands.ingest_report", new=AsyncMock(return_value=failed)):
            await ingest_command("ABC")
        assert commands._last_report is None


# ===========================================================================
# /digest and /pulls
# ===========================================================================


@pytest.fixture
def local_store(synth_ingested, tmp_path, monkeypatch):
    """Point the command handlers at the ingested SYNTH01 store and a temp annotation cache."""
    monkeypatch.setattr(commands, "default_store", lambda: synth_ingested)
    monkeypatch.setattr(commands, "default_annotation_cache", lambda: AnnotationCache(tmp_path / "annotated"))
    monkeypatch.setattr(commands, "_last_report", "SYNTH01")
    return synth_ingested


class TestResolvePullKey:
    def test_forms(self):
        assert resolve_pull_key(["ABC", "3"], None) == "ABC:3"
        assert resolve_pull_key(["3"], "XYZ") == "XYZ:3"

    @pytest.mark.parametrize(
        ("target", "last", "match"),
        [
            (["3"], None, "No report ingested"),
            (["ABC", "x"], None, "Invalid fight"),
            (["A", "B", "3"], None, "Usage: /digest"),
        ],
    )
    def test_errors(self, target, last, match):
        with pytest.raises(UsageError, match=match):
            resolve_pull_key(target, last)


class TestDigestCommand:
    async def test_text_uses_last_report(self, local_store):
        out = await digest_command("1")
        assert out.startswith("```text\nPULL SYNTH01:1 Synthetic Boss | wipe")
        assert "ev:SYNTH01:1:death:1" in out

    async def test_explicit_report_and_json(self, local_store):
        out = await digest_command("SYNTH01 2 --json")
        assert out.startswith("```json")
        assert '"pull_key": "SYNTH01:2"' in out

    async def test_unregistered(self, local_store):
        out = await digest_command("1 --unregistered")
        # No encounter file for the synthetic boss in config/, so everything is unregistered.
        assert "### Unregistered damage `SYNTH01:1`" in out
        assert "| 900201 | Synth Cataclysm |" in out
        assert out.count("\n| 9") == 5  # every damaging ability, up to the 20-row limit

    async def test_unknown_pull(self, local_store):
        assert "not in the event store" in await digest_command("9")

    async def test_usage(self, local_store, monkeypatch):
        assert "Usage: /digest" in await digest_command("")
        monkeypatch.setattr(commands, "_last_report", None)
        assert "No report ingested" in await digest_command("1")


class TestPullsCommand:
    async def test_lists_pulls(self, local_store):
        out = await pulls_command("")
        assert "### Pulls `SYNTH01`" in out
        assert "| 1 | Synthetic Boss | wipe | 60.0s | 35.0 | 2 |" in out
        assert "| 2 | Synthetic Boss | kill | 80.0s | 0.0 | 2 |" in out

    async def test_unknown_report(self, local_store):
        assert "No pulls stored for `NOPE`" in await pulls_command("NOPE")

    async def test_usage(self, local_store, monkeypatch):
        assert "Usage: /pulls" in await pulls_command("A B")
        monkeypatch.setattr(commands, "_last_report", None)
        assert "No report ingested" in await pulls_command("")


class TestReviewCommand:
    """/review against SYNTH01 with a reactive fake analyst; the estimate prints before any model call."""

    @pytest.fixture
    def events(self, monkeypatch) -> list[str]:
        log: list[str] = []
        from khadbot.cli.console import console

        monkeypatch.setattr(console, "print", lambda *a, **k: log.append(f"print: {a[0]}"))
        return log

    @pytest.fixture
    def fake(self, role_fakes, events):
        from helpers.fake_llm import ReactiveFakeModel
        from helpers.review_fakes import respond

        def logging_respond(messages, tools):
            events.append("model call")
            return respond(messages, tools)

        role_fakes["analyst"] = ReactiveFakeModel(logging_respond)
        return role_fakes["analyst"]

    async def test_estimate_flag_returns_estimate_without_a_model_call(self, synth_data, tmp_path, fake):
        out = await review_command(
            "--report SYNTH01 --estimate", data=synth_data, findings=FindingsStore(tmp_path / "f.sqlite")
        )
        assert out.startswith("Estimated cost for 2 pulls: about ")
        assert fake.calls == []

    async def test_prints_estimate_before_the_first_model_call(self, synth_data, tmp_path, fake, events):
        out = await review_command(
            "--report SYNTH01 What repeated?", data=synth_data, findings=FindingsStore(tmp_path / "f.sqlite")
        )
        assert events[0].startswith("print: [ui.muted]Estimated cost for 2 pulls")
        assert "model call" in events[1:]
        assert "### Review" in out
        assert "The pulls were reviewed" in out

    async def test_fights_option_limits_pulls(self, synth_data, tmp_path, fake, events):
        out = await review_command(
            "--report SYNTH01 --fights 1 --estimate", data=synth_data, findings=FindingsStore(tmp_path / "f.sqlite")
        )
        assert "for 1 pulls" in out

    async def test_unknown_report(self, synth_data, tmp_path):
        out = await review_command("--report NOPE", data=synth_data, findings=FindingsStore(tmp_path / "f.sqlite"))
        assert "No pulls stored for `NOPE`" in out

    async def test_usage_errors(self, synth_data, tmp_path, monkeypatch):
        monkeypatch.setattr(commands, "_last_report", None)
        assert "No report ingested" in await review_command("", data=synth_data)
        assert "Usage: /review" in await review_command("--depth huge", data=synth_data)
