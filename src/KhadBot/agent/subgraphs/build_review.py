"""
agent/subgraphs/build_review.py

Build review skill subgraph.

Answers spec theory and build questions — talent choices, rotation priority,
stat weights, trinket comparisons — grounded in Icy Veins guide content
retrieved via RAG.  Does not require a WarcraftLogs report.

Subgraph flow
-------------
  [fetch_guide_data] ── search_guide_rag (+ optional get_character_raiderio)
          │              via create_react_agent
          ▼
  [synthesise]       ── direct answer grounded in retrieved guide chunks
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subgraph state
# ---------------------------------------------------------------------------


class BuildReviewState(TypedDict):
    task: str
    character_context: CharacterContext | None
    messages: Annotated[list, add_messages]
    result: str


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_TOOLS: list[BaseTool] = [
    search_guide_rag,
    get_character_raiderio,
]

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_FETCH_SYSTEM = """\
You are a World of Warcraft spec guide data gatherer. Your job is to retrieve
the guide content needed to answer the player's build or theory question.

Available tools:
- search_guide_rag: retrieve relevant sections of Icy Veins spec guides.
  Call this with the player's spec and their specific question.
- get_character_raiderio: fetch the player's progression tier if character
  context is provided — useful for calibrating advice (e.g. M+ vs raid build
  recommendations can differ).

Data collection rules:
1. Always call search_guide_rag first with the spec and question.
2. If the question involves a trade-off (e.g. single target vs AoE build),
   run a second search_guide_rag call focused on that trade-off.
3. Call get_character_raiderio only if character context is available AND
   the progression tier is relevant to the answer.
4. Stop after 2-3 tool calls. Build questions rarely require more.

Output the retrieved guide content as a structured summary.
"""

_SYNTHESISE_SYSTEM = """\
You are answering a World of Warcraft spec theory or build question using
retrieved Icy Veins guide content. This will be handed to a coaching persona
— write in clear, direct prose, no persona voice.

Structure:
1. Direct answer in 1-2 sentences.
2. Supporting detail from the guide — specific talent names, rotation
   priority steps, stat thresholds.
3. Any important caveats (multi-target vs single-target, hero talent
   assumptions, recent patch changes if noted in the guide).
4. If the guide data doesn't clearly answer the question, say so rather
   than speculating.

Rules:
- Only recommend what the retrieved guide content supports.
- Do not use training knowledge alone when guide content is available.
- Under 300 words.
"""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def fetch_guide_data_node(state: BuildReviewState) -> dict[str, Any]:
    task = state.get("task", "")
    char = state.get("character_context")

    context_note = ""
    if char:
        context_note = f"\n\nCharacter context: {char.name} on {char.realm} ({char.region.upper()})"
        if char.raiderio_profile:
            score = char.raiderio_profile.get("mythicPlusScores", {}).get("all", 0)
            context_note += f" — M+ score {score}"

    agent = create_react_agent(
        model=get_llm(),
        tools=_TOOLS,
        prompt=_FETCH_SYSTEM,
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content=f"{task}{context_note}")]})

    return {"messages": result["messages"]}


async def synthesise_node(state: BuildReviewState) -> dict[str, Any]:
    messages = state.get("messages", [])
    if not messages:
        return {"result": "No guide data was retrieved for this question."}

    llm = get_llm()
    synthesis_messages = [
        SystemMessage(content=_SYNTHESISE_SYSTEM),
        *messages,
        HumanMessage(content="Now produce the structured build/theory answer."),
    ]

    try:
        response = await llm.ainvoke(synthesis_messages)
        return {"result": response.content}
    except Exception as exc:
        logger.error("Build review synthesis failed: %s", exc)
        return {"result": f"Build analysis could not be assembled: {exc}"}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _build_build_review_graph() -> StateGraph:
    graph = StateGraph(BuildReviewState)

    graph.add_node("fetch_guide_data", fetch_guide_data_node)
    graph.add_node("synthesise", synthesise_node)

    graph.set_entry_point("fetch_guide_data")
    graph.add_edge("fetch_guide_data", "synthesise")
    graph.add_edge("synthesise", END)

    return graph


compiled_build_review = _build_build_review_graph().compile()
