"""
tests/unit/encounter/analyst/test_review.py

The review graph on SYNTH01 with a reactive fake model: per-pull analysis is cached, each cache-key
input invalidates it, the concurrency cap holds, flagged findings are excluded and regenerated, and
the synthesis only gets compare_pulls and submit_report. Also the no-LLM cost estimate.

The fake answers from the prompt it is given, because parallel pull analyses interleave and a scripted
reply list would hand replies to the wrong pull. It tells the three kinds of call apart by prompt:
per-pull analysis ("The question is about pull X"), synthesis ("combining findings") and anything else.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from helpers.fake_llm import ReactiveFakeModel
from helpers.findings import make
from helpers.review_fakes import P1, P2, kind_of, query, respond
from langchain_core.messages import AIMessage, SystemMessage

from khadbot.encounter.analyst import graph as analyst
from khadbot.encounter.analyst.graph import (
    NO_PULLS,
    NO_STRUCTURED_REPORT,
    OUTPUT_TOKENS_PER_PULL,
    collect_node,
    estimate_review_cost,
    render_findings,
    review,
)
from khadbot.encounter.analyst.query_tools import build_analyst_tools
from khadbot.encounter.findings import FindingsStore
from khadbot.encounter.models import AnalysisRequest


def kinds(model: ReactiveFakeModel) -> list[str]:
    return [kind_of(msgs) for _, msgs in model.calls]


def request(*pulls: str, depth: str = "standard", question: str = "What went wrong tonight?") -> AnalysisRequest:
    return AnalysisRequest(request_id="abcdef0123456789", question=question, pulls=list(pulls), depth=depth)


@pytest.fixture
def findings(tmp_path) -> FindingsStore:
    return FindingsStore(tmp_path / "khadbot.sqlite")


@pytest.fixture
def model(role_fakes) -> ReactiveFakeModel:
    fake = ReactiveFakeModel(respond, model_name="model-a")
    role_fakes["analyst"] = fake
    return fake


# ===========================================================================
# Fan-out, caching and synthesis
# ===========================================================================


async def test_first_review_analyses_each_pull_then_synthesizes(model, findings, synth_data):
    report = await review(request(P1, P2), store=synth_data, findings=findings)

    assert sorted(kinds(model)) == ["pull", "pull", "synthesis"]
    assert kinds(model)[-1] == "synthesis"
    assert {pf.pull_key for p in (P1, P2) for pf in findings.for_pull(p)} == {P1, P2}
    assert [f.claim for f in report.findings] == ["The pulls were reviewed"]
    assert [f.scope for f in report.findings] == ["cross_pull"]
    assert report.request_id == "abcdef0123456789"
    assert not report.rejected


async def test_second_identical_review_reuses_cached_pull_findings(model, findings, synth_data):
    await review(request(P1, P2), store=synth_data, findings=findings)
    model.calls.clear()

    report = await review(request(P1, P2), store=synth_data, findings=findings)

    assert kinds(model) == ["synthesis"]  # no per-pull analysis; synthesis depends on the question
    assert [f.claim for f in report.findings] == ["The pulls were reviewed"]


async def test_a_new_pull_is_analysed_alone(model, findings, synth_data):
    await review(request(P1), store=synth_data, findings=findings)
    model.calls.clear()

    await review(request(P1, P2), store=synth_data, findings=findings)

    pulls_seen = [m.content for _, msgs in model.calls for m in msgs if isinstance(m, SystemMessage)]
    assert kinds(model).count("pull") == 1
    assert any(f"pull {P2}." in s for s in pulls_seen)


async def test_synthesis_only_gets_compare_pulls_and_submit_report(model, findings, synth_data):
    await review(request(P1, P2), store=synth_data, findings=findings)

    synthesis_tools = next(tools for tools, msgs in model.calls if kind_of(msgs) == "synthesis")
    pull_tools = next(tools for tools, msgs in model.calls if kind_of(msgs) == "pull")
    assert sorted(synthesis_tools) == ["compare_pulls", "submit_report"]
    assert "query_events" in pull_tools


async def test_synthesis_prompt_carries_the_pull_findings(model, findings, synth_data):
    await review(request(P1, P2, question="What repeats?"), store=synth_data, findings=findings)

    _, msgs = next(c for c in model.calls if kind_of(c[1]) == "synthesis")
    system = next(m.content for m in msgs if isinstance(m, SystemMessage))
    assert f"Pull {P1}:" in system
    assert f"Pull {P2}:" in system
    assert "The pull was reviewed" in system
    assert msgs[-1].content == "What repeats?"


# ===========================================================================
# Invalidation
# ===========================================================================


async def reviewed_twice(model, findings, synth_data, *, change, req=None):
    """Run a review, apply ``change()``, review again, and return the second review's call kinds."""
    await review(req or request(P1, P2), store=synth_data, findings=findings)
    model.calls.clear()
    change()
    await review(req or request(P1, P2), store=synth_data, findings=findings)
    return kinds(model)


async def test_editing_the_encounter_file_invalidates(model, findings, synth_data, synth_enc_root):
    def edit():
        path = next(synth_enc_root.rglob("*.yaml"))
        path.write_text(
            path.read_text().replace("cluster_window_ms: 1000", "cluster_window_ms: 1500"), encoding="utf-8"
        )

    assert (await reviewed_twice(model, findings, synth_data, change=edit)).count("pull") == 2


async def test_changing_a_tool_docstring_invalidates(model, findings, synth_data, monkeypatch):
    def edit():
        tools = [
            t.model_copy(update={"description": t.description + " Reworded."}) if t.name == "query_deaths" else t
            for t in build_analyst_tools()
        ]
        monkeypatch.setattr(analyst, "build_analyst_tools", lambda: tools)

    assert (await reviewed_twice(model, findings, synth_data, change=edit)).count("pull") == 2


async def test_changing_a_prompt_invalidates(model, findings, synth_data, monkeypatch):
    def edit():
        monkeypatch.setattr(analyst, "PROMPTS", (*analyst.PROMPTS, "Be terse."))

    assert (await reviewed_twice(model, findings, synth_data, change=edit)).count("pull") == 2


async def test_changing_the_review_question_invalidates(model, findings, synth_data, monkeypatch):
    def edit():
        monkeypatch.setattr(analyst, "STANDARD_REVIEW_QUESTION", "Review this pull again.")

    assert (await reviewed_twice(model, findings, synth_data, change=edit)).count("pull") == 2


async def test_changing_the_graph_version_invalidates(model, findings, synth_data, monkeypatch):
    def edit():
        monkeypatch.setattr(analyst, "GRAPH_VERSION", "99")

    assert (await reviewed_twice(model, findings, synth_data, change=edit)).count("pull") == 2


async def test_changing_the_model_id_invalidates(model, findings, synth_data):
    def edit():
        model.model_name = "model-b"

    assert (await reviewed_twice(model, findings, synth_data, change=edit)).count("pull") == 2


async def test_changing_the_depth_invalidates(model, findings, synth_data):
    await review(request(P1, P2), store=synth_data, findings=findings)
    model.calls.clear()

    await review(request(P1, P2, depth="deep"), store=synth_data, findings=findings)

    assert kinds(model).count("pull") == 2


# ===========================================================================
# Concurrency
# ===========================================================================


@pytest.mark.parametrize(("cap", "expected_peak"), [(1, 1), (2, 2)])
async def test_concurrency_cap_is_respected(role_fakes, findings, synth_data, monkeypatch, cap, expected_peak):
    monkeypatch.setattr(analyst, "EncounterConfig", lambda: SimpleNamespace(analyst_max_concurrency=cap))
    fake = ReactiveFakeModel(respond, delay=0.05)
    role_fakes["analyst"] = fake

    await review(request(P1, P2), store=synth_data, findings=findings)

    assert fake.peak == expected_peak


# ===========================================================================
# Flagging
# ===========================================================================


async def test_flagged_finding_regenerates_only_its_pull(model, findings, synth_data):
    await review(request(P1, P2), store=synth_data, findings=findings)
    flagged_id = findings.for_pull(P1)[0].findings[0].id
    assert findings.flag(flagged_id)
    model.calls.clear()

    await review(request(P1, P2), store=synth_data, findings=findings)

    assert kinds(model).count("pull") == 1
    regenerated = findings.for_pull(P1)  # the flagged row was replaced under the same key
    assert len(regenerated) == 1
    assert regenerated[0].findings[0].id != flagged_id


async def test_collect_excludes_flagged_findings(findings, synth_data):
    ok = make(pull=P1, key="a")
    bad = make(pull=P2, key="b").model_copy(update={"flagged": True})
    state = {"pulls": [P1, P2], "cached": [ok], "fresh": [bad]}

    out = await collect_node(state)  # type: ignore[arg-type]

    assert [pf.pull_key for pf in out["collected"]] == [P1]


# ===========================================================================
# Edge cases
# ===========================================================================


async def test_no_ingested_pulls_makes_no_model_calls(model, findings, synth_data):
    report = await review(request("NOPE:9"), store=synth_data, findings=findings)

    assert model.calls == []
    assert report.findings == []
    assert "pull NOPE:9 is not ingested" in report.unknowns
    assert NO_PULLS in report.unknowns or "no findings were produced for the requested pulls" in report.unknowns


async def test_missing_pull_is_reported_and_the_rest_reviewed(model, findings, synth_data):
    report = await review(request(P1, "NOPE:9"), store=synth_data, findings=findings)

    assert kinds(model) == ["pull", "synthesis"]
    assert "pull NOPE:9 is not ingested" in report.unknowns
    assert report.findings


async def test_unstructured_pull_reports_are_not_cached(role_fakes, findings, synth_data):
    fake = ReactiveFakeModel(lambda msgs, tools: AIMessage(content="I think they died."))
    role_fakes["analyst"] = fake

    report = await review(request(P1), store=synth_data, findings=findings)

    assert findings.for_pull(P1, include_flagged=True) == []
    assert report.findings == []
    assert any(u.startswith(NO_STRUCTURED_REPORT) for u in report.unknowns)
    assert len(fake.calls) == 1  # synthesis is skipped when no pull produced findings


async def test_partial_pull_analysis_marks_the_report_partial_and_is_not_cached(role_fakes, findings, synth_data):
    def respond_with_queries_first(messages, tools):
        if tools == ["submit_report"] and kind_of(messages) == "pull":  # budget forced the final turn
            return respond(messages, tools)
        if kind_of(messages) == "pull":
            return query("list_pulls")
        return respond(messages, tools)

    role_fakes["analyst"] = ReactiveFakeModel(respond_with_queries_first)

    report = await review(request(P1, P2, depth="quick"), store=synth_data, findings=findings)

    assert report.partial is True
    assert findings.for_pull(P1) == [] and findings.for_pull(P2) == []


# ===========================================================================
# Rendering and estimate
# ===========================================================================


def test_render_findings_lists_findings_unknowns_and_empty_pulls():
    empty = make(pull=P2, key="e").model_copy(update={"findings": [], "unknowns": []})
    text = render_findings([make(pull=P1), empty])

    assert f"Pull {P1}:" in text
    assert "- abcd0123-1 (high) Someone died [ev:SYNTH01:1:pull]" in text
    assert "- unknown: why" in text
    assert f"Pull {P2}:\n- (no findings)" in text


def test_estimate_review_cost_needs_no_model_and_skips_missing_pulls(synth_data):
    one = estimate_review_cost([P1], synth_data)
    both = estimate_review_cost([P1, P2, P1, "NOPE:9"], synth_data)

    assert one.pulls == 1 and both.pulls == 2
    assert one.digest_tokens > 0
    assert both.output_tokens == 2 * OUTPUT_TOKENS_PER_PULL
    assert both.total == both.digest_tokens + both.output_tokens
    assert both.digest_tokens > one.digest_tokens
