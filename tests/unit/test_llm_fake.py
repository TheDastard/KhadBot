"""
tests/unit/test_llm_fake.py

The fake model provider: resolution through get_llm, the fixed usage and model name every reply carries, the scripts
(findings pass verify, the failing script's finding is rejected as verify rejects it), the script registry and the
price table's zero price for ``fake``. A real provider is unaffected.
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from khadbot import llm_factory, llm_fake
from khadbot.config import reset_config
from khadbot.encounter.analyst import query_tools as qt
from khadbot.encounter.analyst.graph import run_analysis
from khadbot.encounter.analyst.query_tools import AnalystScope
from khadbot.encounter.analyst.review_graph import review, review_pull
from khadbot.encounter.analyst.verify import verify_report
from khadbot.encounter.findings import SqliteFindingsStore
from khadbot.encounter.models import AnalysisReport, AnalysisRequest, Finding, ProducedBy
from khadbot.errors import ConfigurationError
from khadbot.jobs.model_prices import get_price_table
from khadbot.jobs.usage import LlmUsage, llm_entry

P1, P2 = "SYNTH01:1", "SYNTH01:2"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in ("LLM_PROVIDER_ANALYST", "LLM_PROVIDER_CONVERSATION", "FAKE_LLM_SCRIPT", "FAKE_LLM_DELAY_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    reset_config()
    yield
    reset_config()


@pytest.fixture
def scripts(monkeypatch):
    """A private copy of the script registry."""
    copy = {role: dict(by_name) for role, by_name in llm_fake.SCRIPTS.items()}
    monkeypatch.setattr(llm_fake, "SCRIPTS", copy)
    return copy


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


class TestGetLlm:
    def test_fake_for_every_role_with_no_key_or_network(self):
        for role in ("analyst", "conversation", None):
            model = llm_factory.get_llm(role=role)
            assert isinstance(model, llm_fake.FakeChatModel)
            assert model.model_name == "fake"

    def test_role_override_selects_fake_while_the_rest_stay_real(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "g-key")
        monkeypatch.setenv("LLM_PROVIDER_ANALYST", "fake")
        reset_config()
        assert isinstance(llm_factory.get_llm(role="analyst"), llm_fake.FakeChatModel)
        with patch("langchain_groq.ChatGroq") as groq:
            llm_factory.get_llm(role="conversation")
        groq.assert_called_once()
        assert llm_factory.fake_provider_roles() == ["analyst"]

    def test_a_real_provider_never_builds_the_fake(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "g-key")
        reset_config()
        with patch("langchain_groq.ChatGroq") as groq, patch.object(llm_fake, "build_fake_model") as build:
            llm_factory.get_llm(role="analyst")
        groq.assert_called_once()
        build.assert_not_called()
        assert llm_factory.fake_provider_roles() == []

    def test_global_fake_is_reported_as_default(self):
        assert llm_factory.fake_provider_roles() == ["default"]

    def test_script_and_delay_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("FAKE_LLM_SCRIPT", "failing")
        monkeypatch.setenv("FAKE_LLM_DELAY_SECONDS", "0.25")
        reset_config()
        model = llm_factory.get_llm(role="analyst")
        assert model.script is llm_fake.failing_analyst_script
        assert model.delay_seconds == 0.25

    def test_unknown_script_is_a_configuration_error(self, monkeypatch):
        monkeypatch.setenv("FAKE_LLM_SCRIPT", "nope")
        reset_config()
        with pytest.raises(ConfigurationError, match="Valid scripts: default, failing"):
            llm_factory.get_llm(role="analyst")


# ---------------------------------------------------------------------------
# Usage, name, delay
# ---------------------------------------------------------------------------


class TestReplies:
    def test_every_reply_carries_fixed_usage_and_the_model_name(self):
        reply = llm_fake.build_fake_model("conversation").invoke([HumanMessage("hi")])
        assert reply.usage_metadata == {
            "input_tokens": 1000,
            "output_tokens": 200,
            "total_tokens": 1200,
            "input_token_details": {"cache_read": 0, "cache_creation": 0},
        }
        assert reply.response_metadata["model_name"] == "fake"

    async def test_async_reply_reaches_the_usage_callback(self):
        handler = UsageMetadataCallbackHandler()
        await llm_fake.build_fake_model("conversation").ainvoke([HumanMessage("hi")], {"callbacks": [handler]})
        assert handler.usage_metadata["fake"]["input_tokens"] == 1000
        assert handler.usage_metadata["fake"]["output_tokens"] == 200

    def test_usage_reads_through_the_ledger_with_a_zero_cost(self):
        reply = llm_fake.build_fake_model("analyst").invoke([HumanMessage("hi")])
        usage = LlmUsage.from_usage_metadata(reply.usage_metadata or {})
        assert (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens) == (1000, 200, 0)
        entry = llm_entry(reply.response_metadata["model_name"], usage)
        assert entry.model == "fake" and entry.cost_usd == 0

    def test_the_price_table_prices_fake_at_zero(self):
        assert get_price_table().estimate_cost("fake", input_tokens=5_000, output_tokens=5_000) == 0

    def test_a_role_without_a_script_gets_the_placeholder(self):
        reply = llm_fake.build_fake_model("conversation").invoke([HumanMessage("hi")])
        assert "No fake script" in str(reply.content)
        assert not reply.tool_calls

    def test_bind_tools_returns_a_copy_that_remembers_the_names(self):
        @tool
        def ping(x: int) -> int:
            """Return x."""
            return x

        model = llm_fake.build_fake_model("analyst")
        bound = model.bind_tools([ping])
        assert bound.bound_tools == ["ping"] and model.bound_tools == []

    async def test_delay_waits_before_each_reply(self):
        model = llm_fake.build_fake_model("conversation", delay_seconds=0.05)
        start = time.perf_counter()
        await model.ainvoke([HumanMessage("hi")])
        assert time.perf_counter() - start >= 0.05
        start = time.perf_counter()
        model.invoke([HumanMessage("hi")])
        assert time.perf_counter() - start >= 0.05


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_script_adds_a_role_script(self, scripts):
        llm_fake.register_script("conversation", "default", lambda m, t: AIMessage(content="scripted"))
        assert llm_factory.get_llm(role="conversation").invoke([HumanMessage("hi")]).content == "scripted"

    def test_a_name_only_another_role_has_falls_back_to_the_roles_default(self, scripts):
        llm_fake.register_script("conversation", "default", lambda m, t: AIMessage(content="chat"))
        assert llm_fake.resolve_script("conversation", "failing")([], []).content == "chat"


# ---------------------------------------------------------------------------
# Scripts against the real query tools
# ---------------------------------------------------------------------------


def _report(rows: list[dict], *, scope: str) -> AnalysisReport:
    findings = [
        Finding(id=f"f-{i}", claim=r["claim"], refs=r["refs"], confidence=r["confidence"], scope=scope)  # type: ignore[arg-type]
        for i, r in enumerate(rows)
    ]
    produced = ProducedBy(model_id="fake", prompt_version="v", graph_version="g", encfile_hash=None, events_hashes={})
    return AnalysisReport(request_id="r", findings=findings, unknowns=[], partial=False, produced_by=produced)


class TestAnalystScripts:
    @pytest.fixture
    def run_tool(self, synth_data):
        scope = AnalystScope(synth_data, (P1, P2))

        def run(call: dict) -> str:
            args = call["args"]
            match call["name"]:
                case "list_pulls":
                    return qt.list_pulls_result(scope).model_dump_json()
                case "query_deaths":
                    return qt.query_deaths_result(scope, args["pull"]).model_dump_json()
                case "compare_pulls":
                    return qt.compare_pulls_result(scope, args["metric"]).model_dump_json()
            raise AssertionError(call)

        return run

    async def _drive(self, script: str, tool_names: list[str], run_tool) -> list[dict]:
        """Play the script against the real tools until it submits; returns the submitted findings."""
        bound = llm_fake.build_fake_model("analyst", script=script).bind_tools([{"name": n} for n in tool_names])
        messages: list = [HumanMessage("review")]
        for _ in range(8):
            reply = await bound.ainvoke(messages)
            messages.append(reply)
            call = reply.tool_calls[0]
            if call["name"] == "submit_report":
                return call["args"]["findings"]
            messages.append(ToolMessage(run_tool(call), tool_call_id=call["id"]))
        raise AssertionError("the script never submitted")

    async def test_pull_script_reads_then_submits_findings_verify_accepts(self, synth_data, run_tool):
        rows = await self._drive("default", ["list_pulls", "query_deaths", "submit_report"], run_tool)
        assert len(rows) >= 2
        verified, reasons = verify_report(_report(rows, scope="pull"), synth_data, [P1, P2])
        assert reasons == [] and len(verified.findings) == len(rows)

    async def test_synthesis_script_compares_pulls_and_submits_findings_verify_accepts(self, synth_data, run_tool):
        rows = await self._drive("default", ["compare_pulls", "submit_report"], run_tool)
        assert rows[0]["claim"] == "The review compared 2 pulls."
        _, reasons = verify_report(_report(rows, scope="cross_pull"), synth_data, [P1, P2])
        assert reasons == []

    async def test_failing_script_adds_a_finding_verify_rejects(self, synth_data, run_tool):
        rows = await self._drive("failing", ["list_pulls", "query_deaths", "submit_report"], run_tool)
        verified, reasons = verify_report(_report(rows, scope="pull"), synth_data, [P1, P2])
        assert len(reasons) == 1 and "unsupported number 987654" in reasons[0]
        assert len(verified.findings) == len(rows) - 1 and len(verified.rejected) == 1

    def test_forced_submission_with_no_reads_reports_an_unknown(self):
        bound = llm_fake.build_fake_model("analyst").bind_tools([{"name": "submit_report"}])
        args = bound.invoke([HumanMessage("x")]).tool_calls[0]["args"]
        assert args["findings"] == [] and args["unknowns"] == [llm_fake.NO_DATA]

    def test_no_pull_rows_submit_an_unknown(self):
        bound = llm_fake.build_fake_model("analyst").bind_tools([{"name": "list_pulls"}, {"name": "submit_report"}])
        first = bound.invoke([HumanMessage("x")])
        empty = ToolMessage(json.dumps({"ok": True, "data": [], "refs": []}), tool_call_id=first.tool_calls[0]["id"])
        second = bound.invoke([HumanMessage("x"), first, empty])
        assert second.tool_calls[0]["name"] == "submit_report"
        assert second.tool_calls[0]["args"]["unknowns"] == [llm_fake.NO_DATA]


# ---------------------------------------------------------------------------
# End to end through the analyst and review graphs
# ---------------------------------------------------------------------------


def _request(*pulls: str) -> AnalysisRequest:
    return AnalysisRequest(
        request_id="abcdef0123456789", question="What repeated?", pulls=list(pulls), depth="standard"
    )


class TestReview:
    async def test_review_writes_findings_that_pass_verify_and_reports_usage(self, synth_data, tmp_path):
        findings = SqliteFindingsStore(tmp_path / "f.sqlite")
        handler = UsageMetadataCallbackHandler()
        report = await review(_request(P1, P2), store=synth_data, findings=findings, callbacks=[handler])
        assert report.findings and report.rejected == [] and not report.partial
        assert report.produced_by.model_id == "fake"
        assert set(handler.usage_metadata) == {"fake"}
        # Two pull analyses and the synthesis, each reading at least once before submitting.
        assert handler.usage_metadata["fake"]["input_tokens"] >= 6 * 1000

    async def test_pull_findings_are_cached_under_the_fake_model_key(self, synth_data, tmp_path):
        findings = SqliteFindingsStore(tmp_path / "f.sqlite")
        first = await review_pull(P1, store=synth_data, findings=findings)
        assert not first.cached and first.findings.findings and first.findings.produced_by.model_id == "fake"
        assert (await review_pull(P1, store=synth_data, findings=findings)).cached

    async def test_the_failing_script_s_finding_is_rejected_by_the_analyst_graph(self, synth_data, monkeypatch):
        monkeypatch.setenv("FAKE_LLM_SCRIPT", "failing")
        reset_config()
        report = await run_analysis(_request(P1), store=synth_data)
        assert len(report.rejected) == 1 and "unsupported number 987654" in report.rejected[0]
        assert report.findings  # the passing findings stand
        assert report.produced_by.model_id == "fake"
