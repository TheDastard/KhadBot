"""
encounter/analyst/review_graph.py

Review: per-pull analysis in parallel, cached, then one synthesis.

    START -> plan_scope -(Send per pull without usable cached findings)-> analyze_pull -> collect
             plan_scope -(everything cached)-> collect
             collect -> synthesize -> END

Each pull is analysed with the fixed STANDARD_REVIEW_QUESTION so its findings do not depend on what
the user asked, and are cached by findings.cache_key. Synthesis answers the user's question from the
unflagged findings of all pulls and runs on every review, since it depends on the question.
Reports that are partial or have no structured report are used for this review but not cached.
Also the cost estimate printed before a review.
"""

from __future__ import annotations

import logging
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
from uuid import UUID, uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from khadbot import llm_factory
from khadbot.config import EncounterConfig
from khadbot.encounter.analyst.graph import COMPARE_TOOL, SUBMIT_TOOL, analysis_config, invoke, make_run, model_id_of
from khadbot.encounter.analyst.prompts import (
    BUDGET_PROMPT,
    NO_PULLS,
    NO_STRUCTURED_REPORT,
    PROMPTS,
    RETRY_PROMPT,
    STANDARD_REVIEW_QUESTION,
    SYNTHESIS_CONTEXT,
    SYSTEM_PROMPT,
)
from khadbot.encounter.analyst.query_tools import build_analyst_tools
from khadbot.encounter.analyst.versioning import GRAPH_VERSION, prompt_version
from khadbot.encounter.digest import render_digest
from khadbot.encounter.evidence import (
    EncounterData,
)
from khadbot.encounter.findings import FindingsStore, cache_key, now
from khadbot.encounter.models import (
    AnalysisReport,
    AnalysisRequest,
    ProducedBy,
    PullFindings,
)
from khadbot.encounter.store import EventStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt versions
# ---------------------------------------------------------------------------


def review_prompt_version() -> str:
    """Prompt version of the per-pull review: the analyst's prompts plus the fixed review question."""
    return prompt_version("analyst", (*PROMPTS, STANDARD_REVIEW_QUESTION), build_analyst_tools())


def synthesis_tools() -> list[BaseTool]:
    return [t for t in build_analyst_tools() if t.name in (COMPARE_TOOL, SUBMIT_TOOL)]


def synthesis_prompt_version() -> str:
    prompts = (SYSTEM_PROMPT, SYNTHESIS_CONTEXT, BUDGET_PROMPT, RETRY_PROMPT)
    return prompt_version("synthesis", prompts, synthesis_tools())


# ---------------------------------------------------------------------------
# Review graph
# ---------------------------------------------------------------------------


class PullTask(TypedDict):
    request: AnalysisRequest
    pull: str


class ReviewState(TypedDict):
    request: AnalysisRequest
    pulls: list[str]  # requested pulls that are ingested
    unknowns: list[str]  # found by code (missing pulls)
    cached: list[PullFindings]  # usable findings found in the store
    fresh: Annotated[list[PullFindings], operator.add]  # findings produced by this review
    partial_pulls: Annotated[list[str], operator.add]  # pulls whose analysis was partial or unstructured
    collected: list[PullFindings]  # cached + fresh, in request order
    report: AnalysisReport | None


@dataclass(frozen=True)
class ReviewRun:
    """Per-review context, passed in ``config["configurable"]["review"]``."""

    data: EncounterData
    findings: FindingsStore
    model: BaseChatModel
    model_id: str


def _review(config: RunnableConfig) -> ReviewRun:
    return (config.get("configurable") or {})["review"]


def pull_cache_key(data: EncounterData, pull_key: str, *, model_id: str, version: str, depth: str) -> str:
    """The findings cache key for one pull under the current data, files, prompts, graph and model."""
    produced = data.digest(pull_key).produced_from
    return cache_key(
        pull_key=pull_key,
        events_hash=produced["events_hash"],
        positions_hash=produced.get("positions_hash", ""),
        encfile_hash=produced["encfile_hash"],
        registry_hash=produced["registry_hash"],
        prompt_version=version,
        graph_version=GRAPH_VERSION,
        model_id=model_id,
        depth=depth,  # type: ignore[arg-type]
    )


async def plan_scope_node(state: ReviewState, config: RunnableConfig) -> dict[str, Any]:
    run = _review(config)
    req = state["request"]
    requested = list(dict.fromkeys(req.pulls))
    pulls = [p for p in requested if run.data.pull(p) is not None]
    unknowns = [f"pull {p} is not ingested" for p in requested if p not in pulls]
    version = review_prompt_version()
    cached: list[PullFindings] = []
    for pull in pulls:
        key = pull_cache_key(run.data, pull, model_id=run.model_id, version=version, depth=req.depth)
        hit = run.findings.get(key)
        if hit is not None and not hit.flagged:
            cached.append(hit)
    return {"pulls": pulls, "unknowns": unknowns, "cached": cached}


def _route_plan(state: ReviewState) -> list[Send] | str:
    if not state["pulls"]:
        return "synthesize"
    have = {pf.pull_key for pf in state["cached"]}
    sends = [Send("analyze_pull", {"request": state["request"], "pull": p}) for p in state["pulls"] if p not in have]
    return sends or "collect"


async def analyze_pull_node(state: PullTask, config: RunnableConfig) -> dict[str, Any]:
    run = _review(config)
    req = state["request"]
    pull = state["pull"]
    version = review_prompt_version()
    pull_req = AnalysisRequest(
        request_id=uuid4().hex, question=STANDARD_REVIEW_QUESTION, pulls=[pull], depth=req.depth, plan_id=req.plan_id
    )
    analyst_run = make_run(run.data, run.model, version, build_analyst_tools())
    report = await invoke(pull_req, analysis_config(pull_req, analyst_run, config))
    pf = PullFindings(
        pull_key=pull,
        findings=report.findings,
        unknowns=report.unknowns,
        produced_by=report.produced_by,
        cache_key=pull_cache_key(run.data, pull, model_id=run.model_id, version=version, depth=req.depth),
        created_at=now(),
    )
    usable = not report.partial and not any(u.startswith(NO_STRUCTURED_REPORT) for u in report.unknowns)
    if usable:
        run.findings.put(pf)
    else:
        logger.warning("not caching findings for %s: partial or no structured report", pull)
    return {"fresh": [pf], "partial_pulls": [] if usable else [pull]}


async def collect_node(state: ReviewState) -> dict[str, Any]:
    by_pull = {pf.pull_key: pf for pf in [*state["cached"], *state["fresh"]]}
    return {"collected": [by_pull[p] for p in state["pulls"] if p in by_pull and not by_pull[p].flagged]}


def render_findings(collected: list[PullFindings]) -> str:
    """Per-pull findings as text for the synthesis prompt."""
    blocks = []
    for pf in collected:
        lines = [f"Pull {pf.pull_key}:"]
        lines += [f"- {f.id} ({f.confidence}) {f.claim} [{', '.join(f.refs)}]" for f in pf.findings]
        lines += [f"- unknown: {u}" for u in pf.unknowns]
        if len(lines) == 1:
            lines.append("- (no findings)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def synthesize_node(state: ReviewState, config: RunnableConfig) -> dict[str, Any]:
    run = _review(config)
    req = state["request"]
    collected = state["collected"]
    if not any(pf.findings for pf in collected):
        unknowns = [*state["unknowns"], *(u for pf in collected for u in pf.unknowns)]
        unknowns.append("no findings were produced for the requested pulls" if state["pulls"] else NO_PULLS)
        produced = ProducedBy(
            model_id=run.model_id,
            prompt_version=synthesis_prompt_version(),
            graph_version=GRAPH_VERSION,
            encfile_hash=None,
            events_hashes={p: run.data.store.events_hash(p) or "" for p in state["pulls"]},
        )
        empty = AnalysisReport(
            request_id=req.request_id, findings=[], unknowns=unknowns, partial=False, produced_by=produced
        )
        return {"report": empty}

    pulls = [pf.pull_key for pf in collected]
    synth_req = req.model_copy(update={"pulls": pulls})
    analyst_run = make_run(
        run.data, run.model, synthesis_prompt_version(), synthesis_tools(), synthesis=render_findings(collected)
    )
    report = await invoke(synth_req, analysis_config(synth_req, analyst_run, config))
    report = report.model_copy(
        update={
            "findings": [f.model_copy(update={"scope": "cross_pull"}) for f in report.findings],
            "unknowns": [*state["unknowns"], *report.unknowns],
            "partial": report.partial or bool(state["partial_pulls"]),
        }
    )
    return {"report": report}


def build_review_graph() -> CompiledStateGraph:
    """The review graph. Data, findings store and model come from ``config["configurable"]["review"]``."""
    g = StateGraph(ReviewState)
    g.add_node("plan_scope", plan_scope_node)
    g.add_node("analyze_pull", analyze_pull_node, input_schema=PullTask)
    g.add_node("collect", collect_node)
    g.add_node("synthesize", synthesize_node)
    g.add_edge(START, "plan_scope")
    g.add_conditional_edges("plan_scope", _route_plan, ["analyze_pull", "collect", "synthesize"])
    g.add_edge("analyze_pull", "collect")
    g.add_edge("collect", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


async def review(
    req: AnalysisRequest,
    *,
    store: EncounterData | EventStore,
    findings: FindingsStore,
    callbacks: list | None = None,
    run_id: UUID | None = None,
) -> AnalysisReport:
    """
    Review several pulls and answer ``req.question`` from their findings.

    Each pull is analysed once per (data, files, prompts, graph, model, depth) and cached in
    ``findings``; pulls with usable cached findings cost no model calls. Findings the user flagged are
    left out of the synthesis and their pulls are analysed again. At most
    ``EncounterConfig.analyst_max_concurrency`` pulls are analysed at once.
    """
    data = store if isinstance(store, EncounterData) else EncounterData(store)
    model = llm_factory.get_llm(role="analyst")
    run = ReviewRun(data=data, findings=findings, model=model, model_id=model_id_of(model))
    config: RunnableConfig = {
        "configurable": {"review": run},
        "callbacks": callbacks,
        "run_name": "encounter_review",
        "tags": ["encounter_review"],
        "metadata": {
            "request_id": req.request_id,
            "graph_version": GRAPH_VERSION,
            "model_id": run.model_id,
            "pulls": list(req.pulls),
            "depth": req.depth,
        },
        "max_concurrency": EncounterConfig().analyst_max_concurrency,
        "recursion_limit": 50 + 4 * len(req.pulls),
    }
    if run_id is not None:
        config["run_id"] = run_id
    initial: ReviewState = {
        "request": req,
        "pulls": [],
        "unknowns": [],
        "cached": [],
        "fresh": [],
        "partial_pulls": [],
        "collected": [],
        "report": None,
    }
    result = await build_review_graph().ainvoke(initial, config)
    report = result["report"]
    assert isinstance(report, AnalysisReport)
    return report


# ---------------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------------

OUTPUT_TOKENS_PER_PULL = 1_500  # allowance for one pull's investigation output; not enforced


@dataclass(frozen=True)
class ReviewEstimate:
    pulls: int
    digest_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.digest_tokens + self.output_tokens


def estimate_review_cost(pulls: list[str], store: EncounterData | EventStore) -> ReviewEstimate:
    """
    Rough input + output tokens for analysing ``pulls`` from scratch, with no model call.

    Digest tokens are ``len(rendered digest) / 4``; output is ``OUTPUT_TOKENS_PER_PULL`` per pull. Cached
    pulls would cost nothing, so this is an upper bound. Pulls that are not ingested are skipped.
    """
    data = store if isinstance(store, EncounterData) else EncounterData(store)
    keys = [p for p in dict.fromkeys(pulls) if data.pull(p) is not None]
    digest_tokens = sum(len(render_digest(data.digest(p))) // 4 for p in keys)
    return ReviewEstimate(len(keys), digest_tokens, len(keys) * OUTPUT_TOKENS_PER_PULL)
