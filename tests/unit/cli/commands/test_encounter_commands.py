"""
tests/unit/cli/commands/test_encounter_commands.py

/ingest argument parsing, result formatting, the command handler wired to a patched fetch and process job,
and the ingest path end to end over SYNTH01;
/digest, /pulls and /review against SYNTH01 ingested into a temp store.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from helpers.encounter.synth import add_friendly_damage

from khadbot.cli.commands import encounter as commands
from khadbot.cli.commands.encounter import (
    UsageError,
    digest_command,
    format_ingest_result,
    ingest_and_process,
    ingest_command,
    ingest_parser,
    parse_fights,
    pulls_command,
    resolve_pull_key,
    review_command,
)
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import SqliteFindingsStore
from khadbot.encounter.ingest import EventsOutcome, FetchedReport
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.models import IngestResult
from khadbot.encounter.records_store import RecordsStore
from khadbot.jobs import IngestReport, JobContext, ProcessPullResult, RecordingProgress


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


def fetched(written=(), cached=(), failed=None) -> FetchedReport:
    """What the ingest job's fetch returns: the pulls it left ready, as written or already cached."""
    return FetchedReport(
        report_code="ABC", meta={}, written=list(written), cached=list(cached), failed=dict(failed or {})
    )


def processed(events: EventsOutcome = EventsOutcome.WRITTEN):
    """A stand-in for the process job that reports ``events`` for every pull it is given."""

    async def run(spec, ctx=None, progress=None) -> ProcessPullResult:
        return ProcessPullResult(pull_key=spec.pull_key, events=events, records_written=True, projected=False)

    return AsyncMock(side_effect=run)


class TestHandler:
    async def test_runs_ingest_with_parsed_args_then_processes_each_pull(self):
        process = processed()
        with (
            patch("khadbot.jobs.run.fetch_report", new=AsyncMock(return_value=fetched(written=["ABC:3"]))) as m,
            patch.object(commands, "run_process_pull", new=process),
        ):
            out = await ingest_command("ABC --fights 3 --offline")
        assert m.await_args.args == ("ABC", [3])
        assert m.await_args.kwargs["offline"] is True
        assert [c.args[0].pull_key for c in process.await_args_list] == ["ABC:3"]
        assert "| Ingested | 3 |" in out
        # Fetch filters come from the cooldown registry (and any authored encounter files).
        assert 98008 in m.await_args.kwargs["defensive_spell_ids"]
        assert isinstance(m.await_args.kwargs["enemy_cast_ids"], dict)

    async def test_outcome_comes_from_the_process_job(self):
        """A newly fetched pull whose events were already stored reads as unchanged, as it did before the split."""
        outcomes = {"ABC:1": EventsOutcome.UNCHANGED, "ABC:2": EventsOutcome.WRITTEN, "ABC:3": EventsOutcome.KEPT}

        async def run(spec, ctx=None, progress=None) -> ProcessPullResult:
            if spec.pull_key == "ABC:4":
                raise KeyError("boom")
            events = outcomes[spec.pull_key]
            return ProcessPullResult(pull_key=spec.pull_key, events=events, records_written=False, projected=False)

        ready = fetched(written=["ABC:1", "ABC:2"], cached=["ABC:3", "ABC:4"], failed={"ABC:5": "no pages"})
        with (
            patch("khadbot.jobs.run.fetch_report", new=AsyncMock(return_value=ready)),
            patch.object(commands, "run_process_pull", new=AsyncMock(side_effect=run)),
        ):
            out = await ingest_command("ABC")
        assert "| Ingested | 2 |" in out
        assert "| Skipped (unchanged) | 1, 3 |" in out
        assert "| Failed | 5, 4 |" in out
        assert "`ABC:4`: processing failed" in out

    async def test_bad_args_return_usage_without_ingesting(self):
        with patch("khadbot.jobs.run.fetch_report", new=AsyncMock()) as m:
            out = await ingest_command("")
        m.assert_not_awaited()
        assert "Usage: /ingest" in out

    async def test_refresh_meta_is_passed_through(self):
        with (
            patch("khadbot.jobs.run.fetch_report", new=AsyncMock(return_value=fetched(written=["ABC:3"]))) as m,
            patch.object(commands, "run_process_pull", new=processed()),
        ):
            await ingest_command("ABC --refresh-meta")
        assert m.await_args.kwargs["refresh_meta"] is True
        assert m.await_args.kwargs["offline"] is False

    async def test_refresh_meta_with_offline_is_a_usage_error(self):
        with patch("khadbot.jobs.run.fetch_report", new=AsyncMock()) as m:
            out = await ingest_command("ABC --refresh-meta --offline")
        m.assert_not_awaited()
        assert "--refresh-meta" in out and "Usage: /ingest" in out


class TestIngestRemembersReport:
    async def test_sets_last_report(self, monkeypatch):
        monkeypatch.setattr(commands, "_last_report", None)
        with (
            patch("khadbot.jobs.run.fetch_report", new=AsyncMock(return_value=fetched(cached=["ABC:3"]))),
            patch.object(commands, "run_process_pull", new=processed(EventsOutcome.UNCHANGED)),
        ):
            await ingest_command("ABC")
        assert commands._last_report == "ABC"

    async def test_failed_ingest_does_not(self, monkeypatch):
        monkeypatch.setattr(commands, "_last_report", None)
        with patch("khadbot.jobs.run.fetch_report", new=AsyncMock(return_value=fetched(failed={"ABC:3": "boom"}))):
            await ingest_command("ABC")
        assert commands._last_report is None


class TestIngestEndToEnd:
    """The CLI's ingest path over SYNTH01's recorded pages: the ingest job, then a process job per pull."""

    @pytest.fixture
    def ctx(self, synth_cache, synth_store, synth_enc_root, synth_defensive, synth_offensive, synth_utility, tmp_path):
        from helpers.encounter.synth import (
            SYNTH_DEFENSIVE_COOLDOWN_IDS,
            SYNTH_ENEMY_CAST_IDS,
            SYNTH_OFFENSIVE_COOLDOWN_IDS,
        )

        filters = {
            "defensive_spell_ids": SYNTH_DEFENSIVE_COOLDOWN_IDS,
            "offensive_spell_ids": SYNTH_OFFENSIVE_COOLDOWN_IDS,
            "enemy_cast_ids": SYNTH_ENEMY_CAST_IDS,
        }
        data = EncounterData(
            synth_store,
            enc_root=synth_enc_root,
            defensive=synth_defensive,
            offensive=synth_offensive,
            utility=synth_utility,
            cache=AnnotationCache(tmp_path / "annotated"),
            records_store=RecordsStore(tmp_path / "records"),
        )
        return JobContext(data=data, store=synth_store, cache=synth_cache, filters=lambda: filters, projector=None)

    async def test_leaves_ingested_digestible_pulls_and_a_repeat_writes_nothing(self, ctx, synth_store, tmp_path):
        spec = IngestReport(code="SYNTH01", offline=True)
        first = await ingest_and_process(spec, ctx, RecordingProgress())
        assert sorted(first.pulls) == ["SYNTH01:1", "SYNTH01:2"] and first.skipped == [] and not first.failed
        assert "| Ingested | 1, 2 |" in format_ingest_result(first)
        assert [p.fight_id for p in synth_store.pulls("SYNTH01")] == [1, 2]
        assert ctx.encounter_data().digest("SYNTH01:1").pull_key == "SYNTH01:1"
        assert len(ctx.encounter_data().records_store.keys("SYNTH01")) == 2

        before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        again = await ingest_and_process(spec, ctx, RecordingProgress())
        assert again.pulls == [] and sorted(again.skipped) == ["SYNTH01:1", "SYNTH01:2"]
        assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


# ===========================================================================
# /digest and /pulls
# ===========================================================================


@pytest.fixture
def local_store(synth_ingested, tmp_path, monkeypatch):
    """Point the command handlers at the ingested SYNTH01 store and a temp annotation cache."""
    monkeypatch.setattr(commands.wiring, "event_store", lambda: synth_ingested)
    monkeypatch.setattr(commands.wiring, "annotation_cache", lambda: AnnotationCache(tmp_path / "annotated"))
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
        assert "### Unclassified friendly damage `SYNTH01:1`\n\nNo unclassified friendly damage." in out

    async def test_unregistered_lists_unclassified_friendly_damage(self, local_store):
        # The fake friendly abilities are not in config/'s registry, so friendly-sourced ones are unclassified.
        add_friendly_damage(local_store)
        out = await digest_command("1 --unregistered")
        unregistered, unclassified = out.split("### Unclassified friendly damage `SYNTH01:1`")
        assert "| 901003 | ? | 4,000 | 0.4% |" in unclassified
        assert "| 901002 | ? | 5,000 | 0.5% |" in unclassified  # Fido, a pet, counts as friendly
        assert "| 901001 | ? | 2,000 | 0.2% |" in unclassified  # Alphadps on herself
        assert "| 901001 | ? | 3,000 | 0.3% |" in unregistered  # the Environment-sourced row is not friendly

    async def test_unknown_pull(self, local_store, tmp_path, monkeypatch):
        monkeypatch.setattr(commands.wiring, "raw_cache", lambda: RawPageCache(tmp_path / "no-raw"))
        assert "neither fetched raw pages nor stored events" in await digest_command("9")

    async def test_usage(self, local_store, monkeypatch):
        assert "Usage: /digest" in await digest_command("")
        monkeypatch.setattr(commands, "_last_report", None)
        assert "No report ingested" in await digest_command("1")


class TestPullsCommand:
    async def test_lists_pulls(self, local_store):
        out = await pulls_command("")
        assert "### Pulls `SYNTH01`" in out
        assert "| Phase reached |" in out
        assert "| 1 | Synthetic Boss | wipe | 60.0s | 35.0 | 41.0 | Synth Stage Two |" in out
        assert "| 2 | Synthetic Boss | kill | 80.0s | 0.0 | 0.0 | Synth Stage Two |" in out

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
        from helpers.encounter.review_fakes import respond
        from helpers.fake_llm import ReactiveFakeModel

        def logging_respond(messages, tools):
            events.append("model call")
            return respond(messages, tools)

        role_fakes["analyst"] = ReactiveFakeModel(logging_respond)
        return role_fakes["analyst"]

    async def test_estimate_flag_returns_estimate_without_a_model_call(self, synth_data, tmp_path, fake):
        out = await review_command(
            "--report SYNTH01 --estimate", data=synth_data, findings=SqliteFindingsStore(tmp_path / "f.sqlite")
        )
        assert out.startswith("Estimated cost for 2 pulls: about ")
        assert fake.calls == []

    async def test_prints_estimate_before_the_first_model_call(self, synth_data, tmp_path, fake, events):
        out = await review_command(
            "--report SYNTH01 What repeated?", data=synth_data, findings=SqliteFindingsStore(tmp_path / "f.sqlite")
        )
        assert events[0].startswith("print: [ui.muted]Estimated cost for 2 pulls")
        assert "model call" in events[1:]
        assert "### Review" in out
        assert "The pulls were reviewed" in out

    async def test_fights_option_limits_pulls(self, synth_data, tmp_path, fake, events):
        out = await review_command(
            "--report SYNTH01 --fights 1 --estimate",
            data=synth_data,
            findings=SqliteFindingsStore(tmp_path / "f.sqlite"),
        )
        assert "for 1 pulls" in out

    async def test_unknown_report(self, synth_data, tmp_path):
        out = await review_command(
            "--report NOPE", data=synth_data, findings=SqliteFindingsStore(tmp_path / "f.sqlite")
        )
        assert "No pulls stored for `NOPE`" in out

    async def test_usage_errors(self, synth_data, tmp_path, monkeypatch):
        monkeypatch.setattr(commands, "_last_report", None)
        assert "No report ingested" in await review_command("", data=synth_data)
        assert "Usage: /review" in await review_command("--depth huge", data=synth_data)
