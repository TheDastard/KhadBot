"""
agent/subgraphs/encounter_review.py

Encounter review skill subgraph.

Answers group-level wipe/mechanic questions — deaths, avoidable damage,
cooldown coordination, healing pressure.  The output is framed around the
group's collective execution, not individual player performance.

Subgraph flow
-------------
  [fetch_encounter] ── find_character_reports → get_encounter_analysis
          │              via create_react_agent ReAct loop
          ▼
  [synthesise]      ── assembles structured encounter analysis for orchestrator
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
from khadbot.tools.warcraftlogs.character import find_character_reports
from khadbot.tools.warcraftlogs.encounter import get_encounter_analysis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subgraph state
# ---------------------------------------------------------------------------


class EncounterReviewState(TypedDict):
    task: str
    character_context: CharacterContext | None
    messages: Annotated[list, add_messages]
    result: str
    resolved_character_context: CharacterContext | None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_TOOLS: list[BaseTool] = [
    find_character_reports,
    get_encounter_analysis,
]

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_FETCH_SYSTEM = """\
You are a World of Warcraft encounter data gatherer. Your job is to collect
the raw encounter analysis data needed to answer the player's wipe or
mechanics question. Do NOT write coaching advice — only collect data.

Available tools:
- find_character_reports: given a character name/realm/region, find their
  recent WarcraftLogs reports and the fight IDs within each report.
- get_encounter_analysis: given a report code and specific fight ID, fetch
  the encounter analysis — deaths, avoidable damage, cooldown timeline,
  and healing pressure.

Data collection rules:
1. Use find_character_reports first if no report code is available.
   Select the most recent report that matches the user's question (e.g.
   if they said "last night's raid", pick the most recent report; if they
   said "our Gnarlroot wipe", pick the most recent Gnarlroot wipe fight).
2. Call get_encounter_analysis with the specific fight ID — not the full
   report. One fight at a time unless the user explicitly asked to compare
   multiple pulls.
3. If the user mentioned a specific boss name, select the most recent wipe
   on that boss (kill: false). If they mentioned their best attempt, select
   the longest wipe (closest to a kill) or the actual kill if it exists.
4. Stop after collecting data for the target fight. Do not fetch multiple
   fights unless directly asked.

Output a structured summary of all collected data. Do not write coaching
advice yet.
"""

_SYNTHESISE_SYSTEM = """\
You are assembling a structured encounter analysis from collected WoW
encounter data. This will be handed to a coaching persona — write in clear
analytical prose, no persona voice.

Structure:
1. Pull summary: duration, outcome, phase reached.
2. Deaths: what killed each player and what the pre-death data shows.
   Distinguish avoidable-mechanic deaths from overwhelmed-healer deaths.
3. Highest-cost mechanical failures: which avoidable mechanics dealt the
   most raid damage and who was most affected.
4. Cooldown assessment: were major externals and raidwides well-timed or
   were there gaps during high-damage windows.
5. Healing pressure: were healers overwhelmed or comfortable.
6. Single highest-leverage fix for the next attempt.

Rules:
- Attribute failures to patterns, not individuals, unless the data clearly
  shows repeated single-player failure.
- Distinguish correlation from causation.
- Under 500 words.
"""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def fetch_encounter_node(state: EncounterReviewState) -> dict[str, Any]:
    task = state.get("task", "")
    char = state.get("character_context")

    context_note = ""
    if char:
        context_note = (
            f"\n\nCharacter context:\n  Name: {char.name}  Realm: {char.realm}  Region: {char.region.upper()}\n"
        )
        if char.last_report_code:
            context_note += f"  Last known report: {char.last_report_code}\n"

    agent = create_react_agent(
        model=get_llm(),
        tools=_TOOLS,
        prompt=_FETCH_SYSTEM,
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content=f"{task}{context_note}")]})

    return {"messages": result["messages"]}


async def synthesise_node(state: EncounterReviewState) -> dict[str, Any]:
    messages = state.get("messages", [])
    if not messages:
        return {"result": "No encounter data was collected for this question."}

    llm = get_llm()
    synthesis_messages = [
        SystemMessage(content=_SYNTHESISE_SYSTEM),
        *messages,
        HumanMessage(content="Now produce the structured encounter analysis."),
    ]

    try:
        response = await llm.ainvoke(synthesis_messages)
        return {"result": response.content}
    except Exception as exc:
        logger.error("Encounter review synthesis failed: %s", exc)
        return {"result": f"Encounter analysis could not be assembled: {exc}"}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _build_encounter_review_graph() -> StateGraph:
    graph = StateGraph(EncounterReviewState)

    graph.add_node("fetch_encounter", fetch_encounter_node)
    graph.add_node("synthesise", synthesise_node)

    graph.set_entry_point("fetch_encounter")
    graph.add_edge("fetch_encounter", "synthesise")
    graph.add_edge("synthesise", END)

    return graph


compiled_encounter_review = _build_encounter_review_graph().compile()
