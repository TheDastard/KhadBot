"""
tests/unit/jobs/test_run.py

The job functions against SYNTH01's recorded pages: the progress sequence each job emits, that a repeat run writes
nothing, that a user-auth job fails cleanly in local mode, and the read-model hook. WCL is replaced by a stub client
that only answers the rate-limit read (every page is cached); the analyst is a scripted fake model.
"""

from __future__ import annotations

import pytest
from helpers.encounter.review_fakes import respond
from helpers.fake_llm import ReactiveFakeModel

from khadbot import wiring
from khadbot.db import PostgresReadModelProjector
from khadbot.encounter.ingest.fetch import MissingFixtureError
from khadbot.encounter.models import IngestResult
from khadbot.jobs import (
    AppAuth,
    IngestReport,
    JobContext,
    LocalTokenResolver,
    ModelMismatchError,
    ProcessPull,
    RecordingProgress,
    ReviewPull,
    UserAuth,
    UserAuthUnavailableError,
    process_specs_for,
    run_ingest_report,
    run_job,
    run_process_pull,
    run_review_pull,
)

PULLS = ["SYNTH01:1", "SYNTH01:2"]


class StubClient:
    """Answers the rate-limit reads ingest makes around a run; records which token each used."""

    def __init__(self) -> None:
        self.tokens: list[str | None] = []

    async def rate_limit(self, *, user_token: str | None = None) -> dict:
        self.tokens.append(user_token)
        return {"pointsSpentThisHour": 0.0}


def tree(root) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# ===========================================================================
# IngestReport
# ===========================================================================


class TestIngestReport:
    async def test_ingests_every_pull_and_emits_the_progress_sequence(self, ingest_ctx):
        progress = RecordingProgress()
        result = await run_ingest_report(IngestReport(code="SYNTH01", offline=True), ingest_ctx, progress)
        assert sorted(result.pulls) == PULLS
        assert progress.events[0] == ("ingest", None, 0.0)
        assert progress.events[-1] == ("ingest", None, 1.0)
        per_pull = [e for e in progress.events if e[1] == "SYNTH01:1"]
        assert per_pull[-1] == ("normalizing", "SYNTH01:1", 1.0)
        assert [e[0] for e in per_pull][:2] == ["fetched Deaths", "fetched DamageTaken"]
        assert {e[1] for e in progress.events} == {None, *PULLS}

    async def test_repeat_run_writes_nothing(self, ingest_ctx, tmp_path):
        spec = IngestReport(code="SYNTH01", offline=True)
        await run_ingest_report(spec, ingest_ctx)
        before = tree(tmp_path / "events")
        again = await run_ingest_report(spec, ingest_ctx)
        assert again.pulls == [] and sorted(again.skipped) == PULLS
        assert tree(tmp_path / "events") == before

    async def test_fights_limit_the_pulls(self, ingest_ctx):
        result = await run_ingest_report(IngestReport(code="SYNTH01", fights=[2], offline=True), ingest_ctx)
        assert result.pulls == ["SYNTH01:2"]

    async def test_result_names_the_pulls_to_process(self, ingest_ctx):
        result = await run_ingest_report(IngestReport(code="SYNTH01", offline=True), ingest_ctx)
        assert sorted(s.pull_key for s in process_specs_for(result)) == PULLS
        skipped_only = IngestResult(report_code="X", pulls=[], skipped=["X:1"], failed={}, points_spent=None)
        assert process_specs_for(skipped_only) == []

    async def test_missing_pages_raise_as_ingest_does(self, ingest_ctx):
        with pytest.raises(MissingFixtureError):
            await run_ingest_report(IngestReport(code="NOPE", offline=True), ingest_ctx)

    async def test_app_auth_uses_no_user_token(self, raw_cache, event_store, filters):
        client = StubClient()
        ctx = JobContext(store=event_store, cache=raw_cache, filters=lambda: filters, client=client)  # type: ignore[arg-type]
        await run_ingest_report(IngestReport(code="SYNTH01", auth=AppAuth()), ctx)
        assert client.tokens == [None, None]

    async def test_user_auth_fails_cleanly_in_local_mode(self, ingest_ctx, event_store):
        spec = IngestReport(code="SYNTH01", offline=True, auth=UserAuth(user_id="u-1"))
        progress = RecordingProgress()
        with pytest.raises(UserAuthUnavailableError, match="local mode"):
            await run_ingest_report(spec, ingest_ctx, progress)
        assert progress.events == []  # failed before any work
        assert event_store.pulls("SYNTH01") == []

    async def test_resolved_user_token_reaches_ingest_through_a_provider(self, raw_cache, event_store, filters):
        class Resolver:
            async def provider_for(self, auth):
                assert auth == UserAuth(user_id="u-1")

                async def provider() -> str:
                    return "fake-user-token"

                return provider

        client = StubClient()
        ctx = JobContext(
            store=event_store,
            cache=raw_cache,
            filters=lambda: filters,
            client=client,  # type: ignore[arg-type]
            resolver=Resolver(),
        )
        await run_ingest_report(IngestReport(code="SYNTH01", auth=UserAuth(user_id="u-1")), ctx)
        assert client.tokens == ["fake-user-token", "fake-user-token"]

    async def test_local_resolver_resolves_app_auth_to_no_provider(self):
        assert await LocalTokenResolver().provider_for(AppAuth()) is None


# ===========================================================================
# ProcessPull
# ===========================================================================


class TestProcessPull:
    async def test_stores_records_and_emits_the_progress_sequence(self, make_data, tmp_path):
        data = make_data()
        progress = RecordingProgress()
        result = await run_process_pull(ProcessPull(pull_key="SYNTH01:1"), JobContext(data=data), progress)
        assert result.records_written and not result.projected
        assert progress.events == [("records", "SYNTH01:1", 0.0), ("records", "SYNTH01:1", 1.0)]
        assert len(data.records_store.keys("SYNTH01")) == 1
        assert any(k.startswith("annotated/") for k in tree(tmp_path / "blobs"))

    async def test_repeat_run_writes_nothing_new(self, make_data, tmp_path):
        spec = ProcessPull(pull_key="SYNTH01:1")
        await run_process_pull(spec, JobContext(data=make_data()))
        before = tree(tmp_path / "blobs")
        # A second process: a fresh in-process cache over the same blob store.
        again = await run_process_pull(spec, JobContext(data=make_data()))
        assert not again.records_written
        assert tree(tmp_path / "blobs") == before

    async def test_unknown_pull_raises_key_error(self, make_data):
        with pytest.raises(KeyError, match="not in the event store"):
            await run_process_pull(ProcessPull(pull_key="SYNTH01:99"), JobContext(data=make_data()))

    async def test_without_a_records_store_it_still_builds(self, make_data):
        data = make_data()
        data.records_store = None
        result = await run_process_pull(ProcessPull(pull_key="SYNTH01:2"), JobContext(data=data))
        assert not result.records_written
        assert data.digest("SYNTH01:2").pull_key == "SYNTH01:2"

    async def test_projector_runs_after_the_records_and_sees_them(self, make_data):
        seen: list[str] = []

        class Projector:
            async def project(self, records) -> None:
                seen.append(records.pull.pull_key)

        progress = RecordingProgress()
        ctx = JobContext(data=make_data(), projector=Projector())
        result = await run_process_pull(ProcessPull(pull_key="SYNTH01:1"), ctx, progress)
        assert result.projected and seen == ["SYNTH01:1"]
        assert [e[0] for e in progress.events] == ["records", "records", "project", "project"]

    async def test_projector_runs_on_repeat_runs_too(self, make_data):
        calls: list[int] = []

        class Projector:
            async def project(self, records) -> None:
                calls.append(1)

        ctx = JobContext(data=make_data(), projector=Projector())
        await run_process_pull(ProcessPull(pull_key="SYNTH01:1"), ctx)
        await run_process_pull(ProcessPull(pull_key="SYNTH01:1"), ctx)
        assert len(calls) == 2  # the projector is idempotent by contract; the job does not decide for it


class TestReadModelHook:
    def test_no_projector_in_local_mode(self, monkeypatch):
        monkeypatch.delenv("KHADBOT_MODE", raising=False)
        assert wiring.read_model_projector() is None

    def test_hosted_mode_projects_into_postgres_without_connecting_yet(self, monkeypatch):
        monkeypatch.setenv("KHADBOT_MODE", "hosted")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert isinstance(wiring.read_model_projector(), PostgresReadModelProjector)  # no pool opened to build it

    async def test_hosted_process_pull_calls_the_writer(self, make_data, raw_cache, monkeypatch):
        """ProcessPull in hosted mode hands the records and the report metadata to the read-model writer."""
        monkeypatch.setenv("KHADBOT_MODE", "hosted")
        calls: list[tuple] = []
        pool = object()

        def write(p, records, meta) -> bool:
            calls.append((p, records.pull.pull_key, meta))
            return len(calls) == 1  # the real writer writes once, then finds the rows current

        default = wiring.read_model_projector()
        assert isinstance(default, PostgresReadModelProjector)
        projector = PostgresReadModelProjector(raw_cache, pool=lambda: pool, write=write)
        ctx = JobContext(data=make_data(), projector=projector)
        spec = ProcessPull(pull_key="SYNTH01:1")
        assert (await run_process_pull(spec, ctx)).projected
        assert (await run_process_pull(spec, ctx)).projected
        meta = raw_cache.report_meta("SYNTH01")
        assert meta is not None
        assert calls == [(pool, "SYNTH01:1", meta), (pool, "SYNTH01:1", meta)]


# ===========================================================================
# ReviewPull
# ===========================================================================


class TestReviewPull:
    @pytest.fixture
    def fake(self, role_fakes) -> ReactiveFakeModel:
        role_fakes["analyst"] = ReactiveFakeModel(respond)
        return role_fakes["analyst"]

    async def test_reviews_and_caches_then_serves_from_the_cache(self, make_data, findings, fake):
        ctx = JobContext(data=make_data(), findings=findings)
        spec = ReviewPull(pull_key="SYNTH01:1")
        progress = RecordingProgress()
        first = await run_review_pull(spec, ctx, progress)
        assert not first.cached and not first.partial
        assert [f.claim for f in first.findings.findings] == ["The pull was reviewed"]
        assert progress.events == [("review", "SYNTH01:1", 0.0), ("review", "SYNTH01:1", 1.0)]
        calls = len(fake.calls)
        assert calls > 0

        second = await run_review_pull(spec, ctx)
        assert second.cached
        assert second.findings == first.findings
        assert len(fake.calls) == calls  # no model call
        assert len(findings.for_pull("SYNTH01:1")) == 1  # nothing new written

    async def test_depth_is_part_of_the_cache_key(self, make_data, findings, fake):
        ctx = JobContext(data=make_data(), findings=findings)
        await run_review_pull(ReviewPull(pull_key="SYNTH01:1", depth="quick"), ctx)
        other = await run_review_pull(ReviewPull(pull_key="SYNTH01:1", depth="deep"), ctx)
        assert not other.cached

    async def test_model_mismatch_fails_before_any_model_call(self, make_data, findings, fake):
        ctx = JobContext(data=make_data(), findings=findings)
        with pytest.raises(ModelMismatchError, match="expected"):
            await run_review_pull(ReviewPull(pull_key="SYNTH01:1", model="some-other-model"), ctx)
        assert fake.calls == []

    async def test_unknown_pull_raises_key_error_before_any_model_call(self, make_data, findings, fake):
        with pytest.raises(KeyError, match="not in the event store"):
            await run_review_pull(ReviewPull(pull_key="SYNTH01:99"), JobContext(data=make_data(), findings=findings))
        assert fake.calls == []

    async def test_a_later_review_reuses_the_jobs_cached_findings(self, make_data, findings, fake):
        """A pull reviewed by the job costs no per-pull model calls in a multi-pull review: only the synthesis runs."""
        from khadbot.encounter.analyst import review
        from khadbot.encounter.models import AnalysisRequest

        data = make_data()
        await run_review_pull(ReviewPull(pull_key="SYNTH01:1"), JobContext(data=data, findings=findings))
        pull_calls = len(fake.calls)
        report = await review(
            AnalysisRequest(request_id="r1", question="What repeated?", pulls=["SYNTH01:1"]),
            store=data,
            findings=findings,
        )
        assert report.findings
        assert 0 < len(fake.calls) - pull_calls <= pull_calls


# ===========================================================================
# run_job
# ===========================================================================


async def test_run_job_dispatches_on_the_spec(ingest_ctx):
    result = await run_job(IngestReport(code="SYNTH01", offline=True), ingest_ctx)
    assert isinstance(result, IngestResult) and sorted(result.pulls) == PULLS
