"""
encounter/analyst/graph.py

The encounter analyst: a LangGraph subgraph that investigates one question about one or more
pulls through the read-only query tools and ends by calling ``submit_report``.

    START -> scope -> agent -(query tool calls)-> tools -> agent ...
                      agent -(submit_report, plain text, or budget hit)-> verify
                      verify -(rejections or invalid report, retry left)-> agent
                      verify -> END

- ``scope`` is deterministic: it drops pulls that are not ingested (they become unknowns) and seeds
  the system prompt with the rendered digest (one pull) or the pull list (several).
- ``agent`` binds the query tools to ``llm_factory.get_llm(role="analyst")``. Each tool round costs
  one step of the depth's budget. At the budget, the next turn binds only ``submit_report`` and the
  report is marked ``partial``.
- ``verify`` applies the verify rules (verify.py). If findings are rejected (or the submission is
  invalid) and budget is left, the model gets one retry with the reasons; after that, rejections stand.
  A plain-text answer becomes a finding-less report with ``unknowns=["no structured report"]``.

Prompt texts live in prompts.py. ``review`` (review_graph.py) fans this graph out over many pulls through
``make_run``, ``analysis_config`` and ``invoke``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from khadbot import llm_factory
from khadbot.encounter.analyst.prompts import (
    ACCEPTED,
    BUDGET_PROMPT,
    IGNORED,
    MULTI_PULL_CONTEXT,
    NO_PULLS,
    NO_STRUCTURED_REPORT,
    PROMPTS,
    RETRY_PROMPT,
    SINGLE_PULL_CONTEXT,
    SYNTHESIS_CONTEXT,
    SYSTEM_PROMPT,
)
from khadbot.encounter.analyst.query_tools import AnalystScope, SubmitReportArgs, build_analyst_tools, list_pulls_result
from khadbot.encounter.analyst.verify import verify_report
from khadbot.encounter.analyst.versioning import GRAPH_VERSION, prompt_version
from khadbot.encounter.digest import render_digest
from khadbot.encounter.evidence import (
    EncounterData,
    UnresolvableRef,
    parse_ref,
)
from khadbot.encounter.models import (
    AnalysisReport,
    AnalysisRequest,
    Finding,
    ProducedBy,
    ToolResult,
)
from khadbot.encounter.store import EventStore

STEP_BUDGETS: dict[str, int] = {"quick": 4, "standard": 8, "deep": 16}
SUBMIT_TOOL = "submit_report"
COMPARE_TOOL = "compare_pulls"

# ---------------------------------------------------------------------------
# Prompt version
# ---------------------------------------------------------------------------


def analyst_prompt_version() -> str:
    return prompt_version("analyst", PROMPTS, build_analyst_tools())


# ---------------------------------------------------------------------------
# Graph state and run context
# ---------------------------------------------------------------------------


class AnalystState(TypedDict):
    request: AnalysisRequest
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int  # tool rounds used, including submissions
    report: AnalysisReport | None  # set by verify (or scope) when the run is over
    retried: bool
    forced: bool  # the budget was hit and only submit_report was offered
    pulls: list[str]  # requested pulls that are ingested
    unknowns: list[str]  # unknowns found by code (missing pulls), prepended to the model's


@dataclass(frozen=True)
class AnalystRun:
    """Per-run context, passed in ``config["configurable"]["analyst"]`` (never in graph state)."""

    data: EncounterData
    model: BaseChatModel
    model_id: str
    prompt_version: str
    tools: tuple[BaseTool, ...]
    synthesis: str | None = None  # rendered per-pull findings; set when combining findings instead of investigating


def _run(config: RunnableConfig) -> AnalystRun:
    return (config.get("configurable") or {})["analyst"]


def _budget(state: AnalystState) -> int:
    return STEP_BUDGETS[state["request"].depth]


def model_id_of(model: Any) -> str:
    for attr in ("model_name", "model", "model_id"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(model).__name__


def _produced_by(run: AnalystRun, pulls: list[str]) -> ProducedBy:
    hashes = {p: run.data.store.events_hash(p) or "" for p in pulls}
    enc_hashes = {run.data.records(p).annotated.encfile_hash for p in pulls}
    return ProducedBy(
        model_id=run.model_id,
        prompt_version=run.prompt_version,
        graph_version=GRAPH_VERSION,
        encfile_hash=enc_hashes.pop() if len(enc_hashes) == 1 else None,
        events_hashes=hashes,
    )


def _report(
    run: AnalystRun, state: AnalystState, findings: list[Finding], unknowns: list[str], partial: bool
) -> AnalysisReport:
    return AnalysisReport(
        request_id=state["request"].request_id,
        findings=findings,
        unknowns=[*state["unknowns"], *unknowns],
        partial=partial,
        produced_by=_produced_by(run, state["pulls"]),
    )


def _finding_scope(refs: list[str]) -> Literal["pull", "cross_pull"]:
    pulls = set()
    for ref in refs:
        try:
            pulls.add(parse_ref(ref).pull_key)
        except UnresolvableRef:
            continue
    return "cross_pull" if len(pulls) > 1 else "pull"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def scope_node(state: AnalystState, config: RunnableConfig) -> dict[str, Any]:
    run = _run(config)
    req = state["request"]
    pulls = [p for p in dict.fromkeys(req.pulls) if run.data.pull(p) is not None]
    unknowns = [f"pull {p} is not ingested" for p in dict.fromkeys(req.pulls) if p not in pulls]
    if not pulls:
        state = {**state, "pulls": [], "unknowns": unknowns}
        return {"pulls": [], "unknowns": unknowns, "report": _report(run, state, [], [NO_PULLS], partial=False)}

    if run.synthesis is not None:
        context = SYNTHESIS_CONTEXT.format(findings=run.synthesis)
    elif len(pulls) == 1:
        context = SINGLE_PULL_CONTEXT.format(pull=pulls[0], digest=render_digest(run.data.digest(pulls[0])))
    else:
        rows = list_pulls_result(AnalystScope(run.data, tuple(pulls))).data
        lines = [
            f"- {r['pull_key']}: {r['outcome']}, {r['duration_ms']} ms, fight {r['boss_pct']}%, "
            f"boss HP {r['boss_hp_pct']}%, reached {r['phase_reached']}, {r['deaths']} deaths [{r['ref']}]"
            for r in rows
            if isinstance(r, dict)
        ]
        context = MULTI_PULL_CONTEXT.format(pulls="\n".join(lines))
    system = SystemMessage(SYSTEM_PROMPT.format(budget=_budget(state), context=context))
    return {"pulls": pulls, "unknowns": unknowns, "messages": [system, HumanMessage(req.question)]}


async def agent_node(state: AnalystState, config: RunnableConfig) -> dict[str, Any]:
    run = _run(config)
    forced = state["steps"] >= _budget(state)
    tools = [t for t in run.tools if t.name == SUBMIT_TOOL] if forced else list(run.tools)
    new: list[AnyMessage] = []
    if forced and not state["forced"]:
        new.append(HumanMessage(BUDGET_PROMPT))
    reply = await run.model.bind_tools(tools).ainvoke([*state["messages"], *new], config)
    return {"messages": [*new, reply], "forced": forced}


async def tools_node(state: AnalystState, config: RunnableConfig) -> dict[str, Any]:
    run = _run(config)
    scope = AnalystScope(run.data, tuple(state["pulls"]))
    tool_config: RunnableConfig = {**config, "configurable": {**(config.get("configurable") or {}), "scope": scope}}
    by_name = {t.name: t for t in run.tools if t.name != SUBMIT_TOOL}
    last = state["messages"][-1]
    out: list[AnyMessage] = []
    for call in last.tool_calls if isinstance(last, AIMessage) else []:
        tool = by_name.get(call["name"])
        if tool is None:
            note = f"Unknown tool {call['name']!r}. Tools: {', '.join(sorted(by_name))}, {SUBMIT_TOOL}."
            out.append(_error_message(call["id"], note))
            continue
        try:
            out.append(await tool.ainvoke({**call, "type": "tool_call"}, tool_config))
        except Exception as e:  # a tool never raises to the model
            out.append(_error_message(call["id"], f"{type(e).__name__}: {e}"))
    return {"messages": out, "steps": state["steps"] + 1}


def _error_message(call_id: str | None, note: str) -> ToolMessage:
    content = ToolResult(ok=False, data=[], refs=[], note=note).model_dump_json()
    return ToolMessage(content, tool_call_id=call_id or "", status="error")


async def verify_node(state: AnalystState, config: RunnableConfig) -> dict[str, Any]:
    run = _run(config)
    last = state["messages"][-1]
    calls = last.tool_calls if isinstance(last, AIMessage) else []
    submit = next((c for c in calls if c["name"] == SUBMIT_TOOL), None)
    replies: list[AnyMessage] = [ToolMessage(IGNORED, tool_call_id=c["id"] or "") for c in calls if c is not submit]

    if submit is None:  # plain text, or another tool called after the budget was spent
        report = _report(run, state, [], [NO_STRUCTURED_REPORT], partial=state["forced"])
        return {"messages": replies, "report": report}

    steps = state["steps"] + 1
    can_retry = not state["retried"] and steps < _budget(state)
    try:
        args = SubmitReportArgs.model_validate(submit["args"])
    except ValidationError as e:
        if can_retry:
            note = f"Invalid submit_report arguments: {e}. Call submit_report again with valid arguments."
            return {"messages": [*replies, _error_message(submit["id"], note)], "steps": steps, "retried": True}
        report = _report(run, state, [], [f"{NO_STRUCTURED_REPORT} (invalid submit_report arguments)"], state["forced"])
        return {"messages": replies, "report": report, "steps": steps}

    req = state["request"]
    findings = [
        Finding(
            id=f"{req.request_id[:8]}-{n}",
            claim=f.claim,
            refs=f.refs,
            confidence=f.confidence,
            scope=_finding_scope(f.refs),
        )
        for n, f in enumerate(args.findings, start=1)
    ]
    report = _report(run, state, findings, list(args.unknowns), partial=state["forced"])
    verified, reasons = verify_report(report, run.data, state["pulls"])
    if reasons and can_retry:
        note = RETRY_PROMPT.format(reasons="\n".join(f"- {r}" for r in reasons))
        return {
            "messages": [*replies, ToolMessage(note, tool_call_id=submit["id"] or "")],
            "steps": steps,
            "retried": True,
        }
    accepted = ToolMessage(ACCEPTED, tool_call_id=submit["id"] or "")
    return {"messages": [*replies, accepted], "report": verified, "steps": steps}


# ---------------------------------------------------------------------------
# Routing and graph
# ---------------------------------------------------------------------------


def _after_scope(state: AnalystState) -> str:
    return END if state.get("report") is not None else "agent"


def _after_agent(state: AnalystState) -> str:
    last = state["messages"][-1]
    calls = last.tool_calls if isinstance(last, AIMessage) else []
    if not calls or state["forced"] or any(c["name"] == SUBMIT_TOOL for c in calls):
        return "verify"
    return "tools"


def _after_verify(state: AnalystState) -> str:
    return END if state.get("report") is not None else "agent"


def build_analyst_graph() -> CompiledStateGraph:
    """The analyst graph. Data, model and tools come from ``config["configurable"]["analyst"]``."""
    g = StateGraph(AnalystState)
    g.add_node("scope", scope_node)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("verify", verify_node)
    g.add_edge(START, "scope")
    g.add_conditional_edges("scope", _after_scope, ["agent", END])
    g.add_conditional_edges("agent", _after_agent, ["tools", "verify"])
    g.add_edge("tools", "agent")
    g.add_conditional_edges("verify", _after_verify, ["agent", END])
    return g.compile()


# ---------------------------------------------------------------------------
# Running the graph
# ---------------------------------------------------------------------------


def analysis_config(req: AnalysisRequest, run: AnalystRun, parent: RunnableConfig | None) -> RunnableConfig:
    """Run config for one analyst graph run, nested under ``parent`` when there is one."""
    budget = STEP_BUDGETS[req.depth]
    config: RunnableConfig = {k: v for k, v in (parent or {}).items() if k not in ("run_id", "configurable")}  # type: ignore[assignment]
    config["configurable"] = {"analyst": run}
    config["run_name"] = "encounter_analyst"
    config["tags"] = ["encounter_analyst"]
    config["metadata"] = {
        "request_id": req.request_id,
        "prompt_version": run.prompt_version,
        "graph_version": GRAPH_VERSION,
        "model_id": run.model_id,
        "pulls": list(req.pulls),
        "depth": req.depth,
    }
    # Each round is agent + tools; a retry adds agent + verify. Generous headroom over that.
    config["recursion_limit"] = 4 * budget + 20
    return config


async def invoke(req: AnalysisRequest, config: RunnableConfig) -> AnalysisReport:
    initial: AnalystState = {
        "request": req,
        "messages": [],
        "steps": 0,
        "report": None,
        "retried": False,
        "forced": False,
        "pulls": [],
        "unknowns": [],
    }
    result = await build_analyst_graph().ainvoke(initial, config)
    report = result["report"]
    assert isinstance(report, AnalysisReport)
    return report


def make_run(
    data: EncounterData, model: BaseChatModel, version: str, tools: list[BaseTool], synthesis: str | None = None
) -> AnalystRun:
    return AnalystRun(
        data=data,
        model=model,
        model_id=model_id_of(model),
        prompt_version=version,
        tools=tuple(tools),
        synthesis=synthesis,
    )


async def run_analysis(
    req: AnalysisRequest,
    *,
    store: EncounterData | EventStore,
    callbacks: list | None = None,
    run_id: UUID | None = None,
) -> AnalysisReport:
    """
    Run the analyst on one request and return its verified report.

    ``store`` is an ``EncounterData`` or a bare event store (which then uses the authored files under
    ``config/``). The run is traced as ``encounter_analyst`` with the request ID, prompt and graph
    versions, model ID and pulls as metadata.
    """
    data = store if isinstance(store, EncounterData) else EncounterData(store)
    run = make_run(data, llm_factory.get_llm(role="analyst"), analyst_prompt_version(), build_analyst_tools())
    config = analysis_config(req, run, {"callbacks": callbacks} if callbacks else None)
    if run_id is not None:
        config["run_id"] = run_id
    return await invoke(req, config)
