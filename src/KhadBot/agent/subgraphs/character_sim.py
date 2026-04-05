"""
agent/subgraphs/character_sim.py

Character simulation skill subgraph.

Runs SimulationCraft to compare gear or talent configurations.  Has a
confirmation gate before executing the subprocess — SimC runs take 30-90
seconds and the user should be informed before the wait begins.

Subgraph flow
-------------
  [check_simc_input] ── validates SimC string is present in the task
          │
    ┌─────┴──────┐
    │ missing    │ present
    ▼            ▼
  [request]   [confirm_gate] ── surfaces confirmation prompt to orchestrator
  [simc_str]       │             (parent graph pauses here for user input
                   │              in a human-in-the-loop deployment)
                   ▼
              [run_sim]    ── executes run_simc subprocess
                   │
                   ▼
              [synthesise] ── interprets sim output

Note on confirmation
--------------------
In the current prototype the confirmation_gate simply proceeds automatically
— there is no actual interrupt.  The YAML flag `requires_confirmation: true`
is read by the parent graph builder which will wire a LangGraph interrupt
here in a future iteration.  The node exists now so the wiring point is
explicit and the subgraph structure is correct.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from khadbot.agent.state import CharacterContext
from khadbot.llm_factory import get_llm
from khadbot.tools.rag_search import search_guide_rag
from khadbot.tools.simc import run_simc

logger = logging.getLogger(__name__)

# Rough heuristic for detecting a SimC export string.
# A valid /simc export begins with a character header line like:
#   ClassName="Name", level=70, race=Orc, ...
_SIMC_PATTERN = re.compile(
    r'\w+="[^"]+",\s*level=\d+',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Subgraph state
# ---------------------------------------------------------------------------


class CharacterSimState(TypedDict):
    task: str
    character_context: CharacterContext | None
    messages: Annotated[list, add_messages]
    result: str
    simc_string: str | None  # extracted SimC export
    sim_output: str | None  # raw output from run_simc
    simc_status: Literal[  # drives conditional routing
        "missing",
        "pending_confirmation",
        "running",
        "done",
    ]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_SIM_TOOLS: list[BaseTool] = [run_simc]
_GUIDE_TOOLS: list[BaseTool] = [search_guide_rag]

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYNTHESISE_SYSTEM = """\
You are interpreting SimulationCraft output for a World of Warcraft player.
This will be handed to a coaching persona — write in clear prose, no persona
voice.

Structure:
1. Headline result: what the sim shows (e.g. "Trinket B is +340 DPS over
   Trinket A — approximately 1.8% throughput gain").
2. Key numbers: DPS values, upgrade delta, margin of difference.
3. Important caveats: patchwerk assumption, fight length, target count.
4. Recommendation: yes/no on the swap, one sentence of reasoning.

Rules:
- Never fabricate sim numbers. Report only what run_simc returned.
- Always note sim results assume optimal play and patchwerk conditions
  unless the user specified otherwise.
- Under 250 words.
"""

_REQUEST_SIMC_RESPONSE = """\
To run a simulation I need your current character data as a SimC export string.

How to get it:
1. Install the SimulationCraft addon (CurseForge or Wago).
2. In-game, open the addon and click "Export to SimC".
3. Copy the full text block and paste it here.

Once you share the export string I can run the sim immediately.
"""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def check_simc_input_node(state: CharacterSimState) -> dict[str, Any]:
    """
    Check whether the task contains a SimC export string.
    Sets simc_status to "missing" or "pending_confirmation".
    """
    task = state.get("task", "")

    match = _SIMC_PATTERN.search(task)
    if match:
        # Extract the full SimC block — from the first match line to end
        # of the pasted content.
        simc_string = task[match.start() :]
        logger.info("SimC string detected (%d chars).", len(simc_string))
        return {
            "simc_string": simc_string,
            "simc_status": "pending_confirmation",
        }
    else:
        logger.info("No SimC string found in task.")
        return {
            "simc_string": None,
            "simc_status": "missing",
        }


def request_simc_string_node(state: CharacterSimState) -> dict[str, Any]:
    """
    The SimC string is missing — produce a response asking the user for it.
    """
    return {"result": _REQUEST_SIMC_RESPONSE}


def confirmation_gate_node(state: CharacterSimState) -> dict[str, Any]:
    """
    Confirmation gate — placeholder for future LangGraph interrupt wiring.
    In the prototype, proceeds automatically.
    TODO: wire interrupt(here) when the parent graph builder sets
          requires_confirmation=True for this skill.
    """
    logger.debug("Confirmation gate reached — proceeding automatically (prototype).")
    return {"simc_status": "running"}


async def run_sim_node(state: CharacterSimState) -> dict[str, Any]:
    """
    Execute run_simc and store raw output.
    """
    simc_string = state.get("simc_string", "")
    if not simc_string:
        return {
            "sim_output": None,
            "simc_status": "done",
            "result": "SimC string was lost before execution — please paste it again.",
        }

    logger.info("Running SimulationCraft...")
    try:
        output = await run_simc.ainvoke({"simc_string": simc_string})
        return {"sim_output": output, "simc_status": "done"}
    except Exception as exc:
        logger.error("run_simc failed: %s", exc)
        return {
            "sim_output": None,
            "simc_status": "done",
            "result": f"SimulationCraft encountered an error: {exc}",
        }


async def synthesise_node(state: CharacterSimState) -> dict[str, Any]:
    """
    Interpret the sim output and produce a structured result.
    """
    sim_output = state.get("sim_output")
    if not sim_output:
        # run_sim_node already set result on error.
        return {}

    task = state.get("task", "")
    llm = get_llm()

    synthesis_messages = [
        SystemMessage(content=_SYNTHESISE_SYSTEM),
        HumanMessage(content=(f"Player question: {task}\n\nSimulationCraft output:\n{sim_output}")),
    ]

    try:
        response = await llm.ainvoke(synthesis_messages)
        return {"result": response.content}
    except Exception as exc:
        logger.error("Sim synthesis failed: %s", exc)
        return {"result": f"Sim interpretation failed: {exc}"}


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def route_after_check(state: CharacterSimState) -> str:
    status = state.get("simc_status", "missing")
    if status == "missing":
        return "request_simc_string"
    return "confirmation_gate"


def route_after_confirmation(state: CharacterSimState) -> str:
    # Always run after confirmation in prototype.
    return "run_sim"


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _build_character_sim_graph() -> StateGraph:
    graph = StateGraph(CharacterSimState)

    graph.add_node("check_simc_input", check_simc_input_node)
    graph.add_node("request_simc_string", request_simc_string_node)
    graph.add_node("confirmation_gate", confirmation_gate_node)
    graph.add_node("run_sim", run_sim_node)
    graph.add_node("synthesise", synthesise_node)

    graph.set_entry_point("check_simc_input")

    graph.add_conditional_edges(
        "check_simc_input",
        route_after_check,
        {
            "request_simc_string": "request_simc_string",
            "confirmation_gate": "confirmation_gate",
        },
    )
    graph.add_edge("request_simc_string", END)

    graph.add_conditional_edges(
        "confirmation_gate",
        route_after_confirmation,
        {"run_sim": "run_sim"},
    )
    graph.add_edge("run_sim", "synthesise")
    graph.add_edge("synthesise", END)

    return graph


compiled_character_sim = _build_character_sim_graph().compile()
