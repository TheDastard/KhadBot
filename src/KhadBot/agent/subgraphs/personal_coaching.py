"""
agent/subgraphs/personal_coaching.py

Personal coaching skill subgraph.

Answers questions about a single player's performance — parse percentile,
cast efficiency, cooldown usage, rotation gaps — cross-referenced against
Icy Veins guide content.

Subgraph flow
-------------
  [resolve_character] ── ensures character context + Raider.IO snapshot
          │
          ▼
  [fetch_performance] ── calls get_warcraftlogs_report (+ search_guide_rag)
          │                via create_react_agent ReAct loop
          ▼
  [synthesise]        ── assembles structured analysis text for orchestrator

State
-----
PersonalCoachingState is local to this subgraph.  The parent graph state
(KhadbotState) is not visible here.  The subgraph receives its inputs via
the task string and character_context injected by the parent before
invocation, and returns skill_results["personal_coaching"] on completion.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from typing_extensions import TypedDict

from khadbot.agent.state import CharacterContext
from khadbot.llm_factory import get_llm
from khadbot.tools.rag_search import search_guide_rag
from khadbot.tools.raiderio import get_character_raiderio
from khadbot.tools.warcraftlogs.character import find_character_reports
from khadbot.tools.warcraftlogs.performance import get_warcraftlogs_report

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Subgraph state
# ---------------------------------------------------------------------------


class PersonalCoachingState(TypedDict):
    # Inputs from parent graph (injected before subgraph invocation).
    task: str
    character_context: CharacterContext | None

    # Internal working state.
    messages: Annotated[list, add_messages]

    # Output — written by synthesise node, read by parent graph.
    result: str
    resolved_character_context: CharacterContext | None


# ---------------------------------------------------------------------------
# Tools for this subgraph
# ---------------------------------------------------------------------------

_TOOLS: list[BaseTool] = [
    find_character_reports,
    get_warcraftlogs_report,
    get_character_raiderio,
    search_guide_rag,
]

# ---------------------------------------------------------------------------
# System prompt for the data-fetching ReAct agent
# ---------------------------------------------------------------------------

_FETCH_SYSTEM = """\
You are a World of Warcraft performance data gatherer. Your job is to collect
all data needed to answer the player's coaching question. You are NOT writing
the coaching response — only gathering data.

Available tools:
- find_character_reports: given a character name/realm/region, find their
  recent WarcraftLogs reports and fight IDs.
- get_warcraftlogs_report: fetch parse percentile, damage breakdown, cast
  efficiency, and ability usage for a specific report and player.
- get_character_raiderio: fetch Raider.IO progression and M+ score for
  context on the player's tier.
- search_guide_rag: retrieve Icy Veins spec guide content relevant to the
  player's question.

Data collection rules:
1. If character context is provided (name/realm/region already known),
   use find_character_reports to get their most recent report — do not ask
   the user for a URL.
2. Always scope get_warcraftlogs_report with the player's character name
   to get ability-level breakdown.
3. Fetch Raider.IO context once to establish the player's progression tier.
4. Call search_guide_rag with the player's spec and the specific coaching
   question — retrieve guide recommendations to compare against log data.
5. Stop when you have: parse data, ability breakdown, cast summary,
   Raider.IO context, and relevant guide content. Do not over-fetch.

Output your collected data as a structured summary. Do not write coaching
advice yet — that happens in a separate step.
"""

_SYNTHESISE_SYSTEM = """\
You are assembling a structured performance analysis from collected WoW data.
This analysis will be handed to a coaching persona who will reframe it in
their voice — write in clear, direct prose. No persona flavour.

Structure:
1. Performance summary: parse percentile in context of the player's
   progression tier (from Raider.IO).
2. Top 2-3 highest-impact issues. Be specific — cite ability names,
   cast counts, percentages from the data.
3. For each issue: one concrete fix grounded in the spec guide data.
4. One positive to acknowledge.

Rules:
- Every claim must be traceable to a specific data point.
- If data is missing or incomplete, state what is missing rather than
  inferring.
- Under 400 words. The persona will expand this.
"""


# ---------------------------------------------------------------------------
# Subgraph nodes
# ---------------------------------------------------------------------------


async def fetch_performance_node(state: PersonalCoachingState) -> dict[str, Any]:
    """
    ReAct agent loop: calls tools to collect all performance data.
    Runs until it has enough data or exhausts reasonable tool call attempts.
    """
    task = state.get("task", "")
    char = state.get("character_context")

    # Build the initial message — inject character context if available
    # so the agent doesn't waste a tool call re-discovering it.
    context_note = ""
    if char:
        context_note = (
            f"\n\nCharacter context (already resolved):\n"
            f"  Name: {char.name}\n"
            f"  Realm: {char.realm} ({char.realm_slug})\n"
            f"  Region: {char.region.upper()}\n"
        )
        if char.last_report_code:
            context_note += f"  Last report: {char.last_report_code}"
            if char.last_fight_id:
                context_note += f" (fight {char.last_fight_id})"
            context_note += "\n"

    user_content = f"{task}{context_note}"

    agent = create_react_agent(
        model=get_llm(),
        tools=_TOOLS,
        prompt=_FETCH_SYSTEM,
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content=user_content)]})

    return {"messages": result["messages"]}


async def synthesise_node(state: PersonalCoachingState) -> dict[str, Any]:
    """
    Read the collected data from messages and produce structured analysis text.
    """
    # Extract the final message from the ReAct agent — this is the data summary.
    messages = state.get("messages", [])
    if not messages:
        return {"result": "No performance data was collected for this question."}

    # Feed the collected data into a synthesis LLM call.
    llm = get_llm()
    synthesis_messages = [
        SystemMessage(content=_SYNTHESISE_SYSTEM),
        # Include the full ReAct message history so the synthesiser can
        # see all tool outputs, not just the final summary.
        *messages,
        HumanMessage(content="Now produce the structured performance analysis."),
    ]

    try:
        response = await llm.ainvoke(synthesis_messages)
        return {"result": response.content}
    except Exception as exc:
        logger.error("Personal coaching synthesis failed: %s", exc)
        return {"result": f"Analysis could not be assembled: {exc}"}


# ---------------------------------------------------------------------------
# Subgraph wiring
# ---------------------------------------------------------------------------


def _build_personal_coaching_graph() -> StateGraph:
    graph = StateGraph(PersonalCoachingState)

    graph.add_node("fetch_performance", fetch_performance_node)
    graph.add_node("synthesise", synthesise_node)

    graph.set_entry_point("fetch_performance")
    graph.add_edge("fetch_performance", "synthesise")
    graph.add_edge("synthesise", END)

    return graph


compiled_personal_coaching = _build_personal_coaching_graph().compile()
