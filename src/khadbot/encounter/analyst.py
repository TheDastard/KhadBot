"""
encounter/analyst.py

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
- ``verify`` applies the verify rules. If findings are rejected (or the submission is invalid) and
  budget is left, the model gets one retry with the reasons; after that, rejections stand. A plain-text
  answer becomes a finding-less report with ``unknowns=["no structured report"]``.

Verify rules (``VERIFY_RULES_VERSION`` in versioning.py):

1. Every ref in a finding resolves, to a pull inside the request. Otherwise the finding is rejected
   with ``unresolvable ref <ref>``.
2. Every number in the claim matches a value in the resolved records at the claim's precision (times
   may be written in seconds). The number of refs cited, overall and per kind, also counts as a value,
   so "3 players died" citing three death refs passes. Otherwise ``unsupported number <n>``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
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
from khadbot.encounter.digest import render_digest
from khadbot.encounter.evidence import (
    EncounterData,
    UnresolvableRef,
    claim_numbers,
    number_supported,
    numeric_values,
    parse_ref,
)
from khadbot.encounter.models import AnalysisReport, AnalysisRequest, Finding, ProducedBy, ToolResult
from khadbot.encounter.query_tools import AnalystScope, SubmitReportArgs, build_analyst_tools, list_pulls_result
from khadbot.encounter.store import EventStore
from khadbot.encounter.versioning import GRAPH_VERSION, prompt_version

STEP_BUDGETS: dict[str, int] = {"quick": 4, "standard": 8, "deep": 16}
SUBMIT_TOOL = "submit_report"

# ---------------------------------------------------------------------------
# Prompts (hashed into prompt_version; keep them free of braces other than the placeholders)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the encounter analyst for a World of Warcraft raid review. You answer one question about one or \
more pulls (attempts at a boss) using read-only query tools over the combat log, and you report findings \
that cite evidence.

How to work:
- Investigate with the query tools. Every tool result is JSON with "ok", "data", "refs", "truncated" and \
"note". If "ok" is false, read the note and correct the call. If "truncated" is true, narrow the query as \
the note says before drawing conclusions from partial data.
- You have a budget of {budget} query rounds. Use only as many as the question needs.
- Finish by calling submit_report exactly once. Do not answer in plain text.

Rules for findings:
- Each finding is one factual claim that helps answer the question, with the evidence refs (strings \
starting "ev:") of the records that support it, copied exactly from tool results or the context below.
- Every number in a claim must appear in a cited record. Copy numbers exactly; never add, subtract, \
average or estimate. Times in the data are fight-relative milliseconds; you may write them as seconds \
with one decimal (23000 becomes 23.0s).
- Never cite a ref you have not seen.
- Use confidence "high" only when the cited records directly show the claim.
- If the data cannot answer part of the question (for example a mechanic is not in the encounter file, \
or a data type was not recorded), say so in unknowns with the reason instead of guessing. No findings \
plus unknowns is a valid answer.

{context}"""

SINGLE_PULL_CONTEXT = """\
The question is about pull {pull}. Its digest (every record line starts with its ref):

{digest}"""

MULTI_PULL_CONTEXT = """\
The question is about these pulls (pull key, outcome, duration, boss health, phase reached, deaths):
{pulls}

Use compare_pulls for patterns across pulls and get_digest to look at one pull."""

BUDGET_PROMPT = """\
You have used your query budget. Call submit_report now with the findings the results so far support, \
and put anything unanswered in unknowns."""

RETRY_PROMPT = """\
Verification rejected part of your report:
{reasons}

Call submit_report again with the complete report. Fix each rejected finding using numbers and refs \
exactly as they appear in tool results, or drop it. Include the findings that passed again."""

ACCEPTED = "Report received."
IGNORED = "Ignored: submit_report ends the investigation, so this call was not run."

PROMPTS: tuple[str, ...] = (SYSTEM_PROMPT, SINGLE_PULL_CONTEXT, MULTI_PULL_CONTEXT, BUDGET_PROMPT, RETRY_PROMPT)

NO_STRUCTURED_REPORT = "no structured report"
NO_PULLS = "none of the requested pulls are ingested"


def analyst_prompt_version() -> str:
    return prompt_version("analyst", PROMPTS, build_analyst_tools())


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def check_finding(finding: Finding, data: EncounterData, pulls: Collection[str] | None = None) -> str | None:
    """The reason ``finding`` fails verification, or None if it passes."""
    values: set[float] = set()
    kinds: Counter[str] = Counter()
    for ref in finding.refs:
        try:
            parsed = parse_ref(ref)
            if pulls is not None and parsed.pull_key not in pulls:
                return f"unresolvable ref {ref} (pull is not part of this request)"
            values |= numeric_values(data.resolve(ref))
        except UnresolvableRef:
            return f"unresolvable ref {ref}"
        kinds[parsed.kind] += 1
    values |= {float(len(finding.refs)), *(float(n) for n in kinds.values())}
    for number in claim_numbers(finding.claim):
        if not number_supported(number, values):
            return f"unsupported number {number.text}"
    return None


def verify_report(
    report: AnalysisReport, data: EncounterData, pulls: Collection[str] | None = None
) -> tuple[AnalysisReport, list[str]]:
    """
    Apply the verify rules to every finding.

    Returns the report with failing findings moved to ``rejected`` (as ``<id> "<claim>": <reason>``)
    and the list of new rejection reasons. ``pulls`` limits refs to the request's pulls.
    """
    kept: list[Finding] = []
    reasons: list[str] = []
    for finding in report.findings:
        reason = check_finding(finding, data, pulls)
        if reason is None:
            kept.append(finding)
        else:
            reasons.append(f'{finding.id} "{finding.claim}": {reason}')
    return report.model_copy(update={"findings": kept, "rejected": [*report.rejected, *reasons]}), reasons


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

    if len(pulls) == 1:
        context = SINGLE_PULL_CONTEXT.format(pull=pulls[0], digest=render_digest(run.data.digest(pulls[0])))
    else:
        rows = list_pulls_result(AnalystScope(run.data, tuple(pulls))).data
        lines = [
            f"- {r['pull_key']}: {r['outcome']}, {r['duration_ms']} ms, boss {r['boss_pct']}%, "
            f"reached {r['phase_reached']}, {r['deaths']} deaths [{r['ref']}]"
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
    model = llm_factory.get_llm(role="analyst")
    run = AnalystRun(
        data=data,
        model=model,
        model_id=model_id_of(model),
        prompt_version=analyst_prompt_version(),
        tools=tuple(build_analyst_tools()),
    )
    budget = STEP_BUDGETS[req.depth]
    config: RunnableConfig = {
        "configurable": {"analyst": run},
        "callbacks": callbacks,
        "run_name": "encounter_analyst",
        "tags": ["encounter_analyst"],
        "metadata": {
            "request_id": req.request_id,
            "prompt_version": run.prompt_version,
            "graph_version": GRAPH_VERSION,
            "model_id": run.model_id,
            "pulls": list(req.pulls),
            "depth": req.depth,
        },
        # Each round is agent + tools; a retry adds agent + verify. Generous headroom over that.
        "recursion_limit": 4 * budget + 20,
    }
    if run_id is not None:
        config["run_id"] = run_id
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
