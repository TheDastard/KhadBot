"""
tests/unit/encounter/analyst/test_analyst_scenarios.py

The analyst graph driven by a scripted model (FakeToolModel) on SYNTH01: a normal investigation,
budget exhaustion, a hallucinated ref rejected and retried once, a truncated result followed by a
narrower query, a plain-text answer, an unanswerable question, plus scope, tool-error, invalid
submission and tracing paths.

A scripted model raises StopIteration if it is called more often than scripted, so "the model was
not called again" is asserted by scripting exactly the expected turns.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from helpers.fake_llm import FakeToolModel, make_fake
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from khadbot.encounter.analyst import graph as analyst
from khadbot.encounter.analyst.graph import analyst_prompt_version, run_analysis
from khadbot.encounter.analyst.prompts import ACCEPTED, BUDGET_PROMPT, IGNORED, NO_PULLS, NO_STRUCTURED_REPORT
from khadbot.encounter.analyst.query_tools import build_analyst_tools
from khadbot.encounter.analyst.versioning import GRAPH_VERSION
from khadbot.encounter.models import AnalysisRequest

ALL_TOOLS = [t.name for t in build_analyst_tools()]
P1, P2 = "SYNTH01:1", "SYNTH01:2"
D1 = "ev:SYNTH01:1:death:1"  # Alphadps, 8000 ms, Synth Swirl, overkill 40,000

_ids = iter(range(10_000))


def call(name: str, **args: Any) -> dict:
    return {"name": name, "args": args, "id": f"call-{next(_ids)}", "type": "tool_call"}


def turn(*calls: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


def submit(*findings: tuple[str, list[str]], unknowns: list[str] | None = None) -> AIMessage:
    rows = [{"claim": claim, "refs": refs, "confidence": "high"} for claim, refs in findings]
    return turn(call("submit_report", findings=rows, unknowns=unknowns or []))


def request(*pulls: str, question: str = "Why did we wipe?", depth: str = "standard") -> AnalysisRequest:
    return AnalysisRequest(request_id="abcdef0123456789", question=question, pulls=list(pulls), depth=depth)


def tool_results(model: FakeToolModel) -> list[ToolMessage]:
    """Tool messages the model saw on its last call, in order."""
    return [m for m in model.seen[-1] if isinstance(m, ToolMessage)]


def results_after(model: FakeToolModel, call_index: int) -> list[dict]:
    """Parsed ToolResult JSON the model saw on call ``call_index`` (0-based) that it had not seen before."""
    before = len([m for m in model.seen[call_index - 1] if isinstance(m, ToolMessage)]) if call_index else 0
    new = [m for m in model.seen[call_index] if isinstance(m, ToolMessage)][before:]
    return [json.loads(m.content) for m in new]


@pytest.fixture
def script(role_fakes):
    def set_script(*turns: AIMessage | str) -> FakeToolModel:
        model = make_fake(turns)
        role_fakes["analyst"] = model
        return model

    return set_script


# ===========================================================================
# Plan scenarios
# ===========================================================================


async def test_normal_investigation(script, synth_data):
    model = script(
        turn(call("query_deaths", pull=P1)),
        turn(call("query_events", pull=P1, t_from_ms=4_000, t_to_ms=8_000, actor="Alphadps", types=["damage"])),
        submit(("Alphadps died to Synth Swirl at 8.0s with 40,000 overkill.", [D1])),
    )
    report = await run_analysis(request(P1), store=synth_data)

    assert [f.claim for f in report.findings] == ["Alphadps died to Synth Swirl at 8.0s with 40,000 overkill."]
    f = report.findings[0]
    assert (f.id, f.refs, f.confidence, f.scope) == ("abcdef01-1", [D1], "high", "pull")
    assert (report.partial, report.rejected, report.unknowns) == (False, [], [])
    assert report.request_id == "abcdef0123456789"

    # Tool order matters here: the event query narrows on what the death query showed.
    assert [m.name for m in tool_results(model)] == ["query_deaths", "query_events"]
    assert all(json.loads(m.content)["ok"] for m in tool_results(model))
    assert model.bind_history == [ALL_TOOLS] * 3

    system, human = model.seen[0][:2]
    assert isinstance(system, SystemMessage) and isinstance(human, HumanMessage)
    assert "PULL SYNTH01:1" in system.content and f"[{D1}]" in system.content
    assert "budget of 8 query rounds" in system.content
    assert human.content == "Why did we wipe?"

    pb = report.produced_by
    assert (pb.model_id, pb.prompt_version, pb.graph_version) == (
        "FakeToolModel",
        analyst_prompt_version(),
        GRAPH_VERSION,
    )
    assert pb.events_hashes == {P1: synth_data.store.events_hash(P1)}
    assert pb.encfile_hash == synth_data.records(P1).annotated.encfile_hash


async def test_budget_exhaustion_forces_submit_and_marks_partial(script, synth_data):
    queries = [turn(call("query_deaths", pull=P1)) for _ in range(4)]
    model = script(*queries, submit(("Alphadps died at 8.0s.", [D1])))
    report = await run_analysis(request(P1, depth="quick"), store=synth_data)

    assert report.partial is True
    assert [f.claim for f in report.findings] == ["Alphadps died at 8.0s."]
    assert model.bind_history == [ALL_TOOLS] * 4 + [["submit_report"]]
    final_input = model.seen[-1]
    assert isinstance(final_input[-1], HumanMessage) and final_input[-1].content == BUDGET_PROMPT
    assert sum(isinstance(m, HumanMessage) and m.content == BUDGET_PROMPT for m in final_input) == 1


async def test_budget_exhaustion_without_submit(script, synth_data):
    # The forced turn still asks for a query tool: it is not run, and the report says so.
    queries = [turn(call("query_deaths", pull=P1)) for _ in range(5)]
    model = script(*queries)
    report = await run_analysis(request(P1, depth="quick"), store=synth_data)

    assert (report.findings, report.unknowns, report.partial) == ([], [NO_STRUCTURED_REPORT], True)
    assert len(model.seen) == 5
    assert len(tool_results(model)) == 4  # the fifth query was never executed


async def test_hallucinated_ref_rejected_then_fixed_on_retry(script, synth_data):
    model = script(
        submit(("Alphadps died at 8.0s.", ["ev:SYNTH01:1:death:9"])),
        submit(("Alphadps died at 8.0s.", [D1])),
    )
    report = await run_analysis(request(P1), store=synth_data)

    assert [f.refs for f in report.findings] == [[D1]]
    assert report.rejected == []
    retry_note = tool_results(model)[-1].content
    assert "unresolvable ref ev:SYNTH01:1:death:9" in retry_note
    assert "Call submit_report again" in retry_note
    assert model.bind_history == [ALL_TOOLS] * 2


async def test_rejection_stands_after_single_retry(script, synth_data):
    model = script(
        submit(("Alphadps died at 9.0s.", [D1])),
        submit(("Alphadps died at 9.5s.", [D1]), ("Alphadps died at 8.0s.", [D1])),
    )
    report = await run_analysis(request(P1), store=synth_data)

    assert [f.claim for f in report.findings] == ["Alphadps died at 8.0s."]
    assert report.rejected == ['abcdef01-1 "Alphadps died at 9.5s.": unsupported number 9.5s']
    assert len(model.seen) == 2  # no third turn


async def test_truncated_result_then_narrower_query(script, synth_data):
    model = script(
        turn(call("query_events", pull=P2, t_from_ms=0, t_to_ms=30_000)),
        turn(call("query_events", pull=P2, t_from_ms=4_000, t_to_ms=6_000)),
        submit(unknowns=["nothing notable"]),
    )
    await run_analysis(request(P2), store=synth_data)

    (wide,) = results_after(model, 1)
    (narrow,) = results_after(model, 2)
    assert wide["truncated"] is True and "Narrow t_from_ms/t_to_ms" in wide["note"]
    assert narrow["truncated"] is False and narrow["total"] == len(narrow["data"])


async def test_plain_text_answer(script, synth_data):
    script("The raid wiped because Alphadps stood in Swirl.")
    report = await run_analysis(request(P1), store=synth_data)
    assert (report.findings, report.unknowns, report.partial, report.rejected) == (
        [],
        [NO_STRUCTURED_REPORT],
        False,
        [],
    )


async def test_unanswerable_question_returns_unknowns(script, synth_data):
    script(
        turn(call("query_mechanic", pull=P1, mechanic_id="tank_swap")),
        submit(unknowns=["Tank swaps are not tracked: the encounter file has no tank_swap mechanic."]),
    )
    report = await run_analysis(request(P1, question="Did the tanks swap on time?"), store=synth_data)
    assert report.findings == []
    assert report.unknowns == ["Tank swaps are not tracked: the encounter file has no tank_swap mechanic."]
    assert report.partial is False


# ===========================================================================
# Scope
# ===========================================================================


async def test_multi_pull_context_and_cross_pull_finding(script, synth_data):
    model = script(
        turn(call("compare_pulls", metric="mechanic_failures", mechanic_id="swirl")),
        submit(
            (
                "Swirl failures went from 8 on the wipe to 14 on the kill.",
                ["ev:SYNTH01:1:mech:swirl", "ev:SYNTH01:2:mech:swirl"],
            )
        ),
    )
    report = await run_analysis(request(P1, P2, question="Did Swirl get better?"), store=synth_data)

    system = model.seen[0][0].content
    assert "- SYNTH01:1: wipe, 60000 ms, fight 35.0%, boss HP 41.0%, reached p2, 4 deaths [ev:SYNTH01:1:pull]" in system
    assert "- SYNTH01:2: kill" in system and "PULL SYNTH01:1" not in system
    assert [f.scope for f in report.findings] == ["cross_pull"]
    assert set(report.produced_by.events_hashes) == {P1, P2}


async def test_missing_pulls_become_unknowns(script, synth_data):
    script(submit(("Alphadps died at 8.0s.", [D1])))
    report = await run_analysis(request(P1, "SYNTH01:9"), store=synth_data)
    assert report.unknowns == ["pull SYNTH01:9 is not ingested"]
    assert len(report.findings) == 1


async def test_no_ingested_pulls_skips_the_model(script, synth_data):
    model = script()
    report = await run_analysis(request("SYNTH01:9", "NOPE:1"), store=synth_data)
    assert report.findings == []
    assert report.unknowns == ["pull SYNTH01:9 is not ingested", "pull NOPE:1 is not ingested", NO_PULLS]
    assert model.seen == []


async def test_out_of_scope_queries_and_refs_are_refused(script, synth_data):
    model = script(
        turn(call("query_deaths", pull=P2)),
        submit(("Gammadps died at 55.0s.", ["ev:SYNTH01:2:death:1"])),
        submit(unknowns=["only pull 1 is in scope"]),
    )
    report = await run_analysis(request(P1), store=synth_data)

    (refused,) = results_after(model, 1)
    assert refused["ok"] is False and "not in this request's scope" in refused["note"]
    assert "pull is not part of this request" in tool_results(model)[-1].content
    assert report.findings == [] and report.unknowns == ["only pull 1 is in scope"]


# ===========================================================================
# Tool errors and malformed submissions
# ===========================================================================


async def test_tool_errors_are_returned_not_raised(script, synth_data):
    model = script(
        turn(
            call("query_everything", pull=P1),
            call("query_events", pull=P1, t_from_ms="soon", t_to_ms=1_000),
            call("query_events", pull=P1, t_from_ms=0, t_to_ms=60_000),
        ),
        submit(unknowns=["gave up"]),
    )
    report = await run_analysis(request(P1), store=synth_data)

    results = results_after(model, 1)
    assert [r["ok"] for r in results] == [False, False, False]
    assert "Unknown tool 'query_everything'" in results[0]["note"]
    assert "ValidationError" in results[1]["note"]
    assert "maximum is 30,000 ms" in results[2]["note"]
    assert report.unknowns == ["gave up"]


async def test_invalid_submission_gets_one_retry(script, synth_data):
    model = script(
        turn(call("submit_report", findings=[{"claim": "Alphadps died.", "refs": [], "confidence": "high"}])),
        submit(("Alphadps died at 8.0s.", [D1])),
    )
    report = await run_analysis(request(P1), store=synth_data)
    assert "Invalid submit_report arguments" in tool_results(model)[-1].content
    assert len(report.findings) == 1


async def test_invalid_submission_after_retry_ends_without_findings(script, synth_data):
    bad = {"claim": "Alphadps died.", "refs": [], "confidence": "certain"}
    script(turn(call("submit_report", findings=[bad])), turn(call("submit_report", findings=[bad])))
    report = await run_analysis(request(P1), store=synth_data)
    assert report.findings == []
    assert report.unknowns == [f"{NO_STRUCTURED_REPORT} (invalid submit_report arguments)"]


async def test_calls_alongside_submit_are_ignored(script, synth_data):
    model = script(
        turn(call("query_deaths", pull=P1), call("submit_report", findings=[], unknowns=["n/a"])),
        "unused",
    )
    report = await run_analysis(request(P1), store=synth_data)
    assert report.unknowns == ["n/a"]
    assert len(model.seen) == 1  # verify accepted, the graph ended


async def test_ignored_calls_get_replies(synth_data, role_fakes):
    # The ignored call still gets a ToolMessage, so the transcript stays valid for real providers.
    ignored = call("query_deaths", pull=P1)
    accepted = call("submit_report", findings=[], unknowns=["n/a"])
    role_fakes["analyst"] = make_fake([turn(ignored, accepted)])
    from khadbot.encounter.analyst.graph import AnalystRun, build_analyst_graph, model_id_of

    model = role_fakes["analyst"]
    run = AnalystRun(synth_data, model, model_id_of(model), analyst_prompt_version(), tuple(build_analyst_tools()))
    state = await build_analyst_graph().ainvoke(
        {
            "request": request(P1),
            "messages": [],
            "steps": 0,
            "report": None,
            "retried": False,
            "forced": False,
            "pulls": [],
            "unknowns": [],
        },
        {"configurable": {"analyst": run}},
    )
    replies = {m.tool_call_id: m.content for m in state["messages"] if isinstance(m, ToolMessage)}
    assert replies == {ignored["id"]: IGNORED, accepted["id"]: ACCEPTED}


# ===========================================================================
# Tracing
# ===========================================================================


class RunRecorder(BaseCallbackHandler):
    def __init__(self) -> None:
        self.runs: list[dict] = []

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kw):
        self.runs.append(
            {"name": kw.get("name"), "run_id": run_id, "parent": parent_run_id, "tags": tags, "metadata": metadata}
        )


async def test_trace_metadata(script, synth_data):
    script(submit(("Alphadps died at 8.0s.", [D1])))
    recorder = RunRecorder()
    run_id = uuid4()
    await run_analysis(request(P1), store=synth_data, callbacks=[recorder], run_id=run_id)

    root = next(r for r in recorder.runs if r["parent"] is None)
    assert (root["name"], root["run_id"]) == ("encounter_analyst", run_id)
    assert "encounter_analyst" in root["tags"]
    assert {k: root["metadata"][k] for k in ("request_id", "prompt_version", "graph_version", "model_id", "pulls")} == {
        "request_id": "abcdef0123456789",
        "prompt_version": analyst_prompt_version(),
        "graph_version": GRAPH_VERSION,
        "model_id": "FakeToolModel",
        "pulls": [P1],
    }


def test_prompt_version_tracks_prompts(monkeypatch):
    before = analyst_prompt_version()
    monkeypatch.setattr(analyst, "PROMPTS", (*analyst.PROMPTS, "extra"))
    assert analyst_prompt_version() != before


def test_model_id_of():
    class Named:
        model_name = "llama-3.3-70b-versatile"

    class Modeled:
        model_name = None
        model = "qwen3:8b"

    assert analyst.model_id_of(Named()) == "llama-3.3-70b-versatile"
    assert analyst.model_id_of(Modeled()) == "qwen3:8b"
    assert analyst.model_id_of(object()) == "object"
