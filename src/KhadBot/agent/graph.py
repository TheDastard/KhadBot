"""
agent/graph.py

KhadBot parent LangGraph graph.

Entry point: build_graph() → (compiled_graph, persona_id)
Turn runner:  run_turn()

Changes from the initial implementation
----------------------------------------
- Removed initialise_personas() — personas are loaded as part of AgentConfig
  via get_agent_config().  No separate persona registry exists.
- Removed initialise_registry() from graph.py — skills.py still exposes it
  but graph.py calls it once at build time, not as a global side effect.
- persona_id is resolved against AgentConfig using get_persona() from
  personas.py, so only personas declared in the agent YAML are reachable.
- AgentConfig is not stored in KhadbotState — nodes load it via get_agent_config()
  which is cached (@cache), so there is no per-turn IO.

Graph topology
--------------

  [extract_task]        ── pure Python
        │
        ▼
  [router]              ── lightweight LLM → needed_skills
        │
        ▼
  [skill_loader]        ── pure Python → active_skills
        │
        ▼
  [dispatch_skills]     ── pure Python fan-out → skill_results
        │
        ▼
  [orchestrator]        ── full LLM → final response with persona voice
        │
        ▼
      (END)
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from khadbot.agent.config import get_agent_config, resolve_session_persona
from khadbot.agent.orchestrator import orchestrator_node
from khadbot.agent.router import router_node
from khadbot.agent.skills import initialise_registry, skill_loader_node
from khadbot.agent.state import CharacterContext, KhadbotState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper nodes
# ---------------------------------------------------------------------------


def extract_task_node(state: KhadbotState) -> dict[str, Any]:
    """
    Extract the current user message as a plain string into state["task"].

    Reads the last HumanMessage from state["messages"].  Both the router and
    subgraphs read state["task"] directly — they don't re-parse messages.
    """
    messages = state.get("messages", [])
    task = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            task = content if isinstance(content, str) else str(content)
            break

    if not task:
        logger.warning("extract_task_node: no HumanMessage found in messages.")

    return {"task": task}


async def dispatch_skills_node(state: KhadbotState) -> dict[str, Any]:
    """
    Invoke each active skill subgraph and collect results into skill_results.

    Runs subgraphs sequentially in the prototype.  The upgrade path to
    parallel execution is asyncio.gather() over the active_skills items —
    subgraph inputs are independent so there are no ordering constraints.

    Character context resolved by any subgraph is propagated back to parent
    state so subsequent turns skip re-discovery.
    """
    active_skills = state.get("active_skills", {})
    if not active_skills:
        logger.info("dispatch_skills: no active skills — orchestrator runs standalone.")
        return {"skill_results": {}}

    skill_results: dict[str, str] = {}
    resolved_context: CharacterContext | None = state.get("character_context")

    for skill_name, skill_def in active_skills.items():
        logger.info("Dispatching skill: %s", skill_name)

        subgraph_input = {
            "task": state.get("task", ""),
            "character_context": resolved_context,
            "messages": [],
            "result": "",
        }

        try:
            subgraph_result = await skill_def.subgraph.ainvoke(subgraph_input)
            skill_results[skill_name] = subgraph_result.get("result", "")

            # Propagate any character context the subgraph resolved.
            new_ctx = subgraph_result.get("resolved_character_context")
            if new_ctx is not None:
                resolved_context = new_ctx
                logger.info(
                    "Character context updated by skill '%s': %s",
                    skill_name,
                    new_ctx.name,
                )

        except Exception as exc:
            logger.error("Skill '%s' subgraph failed: %s", skill_name, exc)
            skill_results[skill_name] = (
                f"The {skill_name.replace('_', ' ')} skill encountered an error and could not complete: {exc}"
            )

    updates: dict[str, Any] = {"skill_results": skill_results}
    if resolved_context is not None:
        updates["character_context"] = resolved_context
    return updates


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph(
    persona_id: str | None = None,
    agent_name: str = "coach",
    checkpointer: Any = None,
) -> tuple[Any, str | None]:
    """
    Build and compile the KhadBot LangGraph application graph.

    Parameters
    ----------
    persona_id:
        Persona slug to use for this session.  If None, falls back to
        KHADBOT_PERSONA env var, then no persona (base identity only).
        Must be declared in config/agents/{agent_name}.yaml — personas not
        in the agent's declaration list are silently ignored.
    agent_name:
        Which agent YAML to load.  Default: "coach".
    checkpointer:
        LangGraph checkpointer for state persistence across turns.  Defaults
        to MemorySaver (in-memory, suitable for prototype / single-process).
        Pass a PostgresSaver for production multi-process deployment.

    Returns
    -------
    (compiled_graph, resolved_persona_id)
        resolved_persona_id is the ID of the active persona, or None.
        Callers (CLI, Discord) use it to surface the intro_message.
    """
    # Initialise skill registry (idempotent — safe to call multiple times).
    initialise_registry()

    # Load agent config — validates YAML, cross-references tool names.
    # Cached after first call so repeated build_graph() calls in tests
    # don't re-parse files.
    agent_config = get_agent_config(agent_name)

    # Resolve persona — scoped to agent config so only declared personas
    # are reachable.
    persona = resolve_session_persona(
        explicit_id=persona_id,
        config=agent_config,
    )
    resolved_persona_id = persona.id if persona else None

    if persona:
        logger.info(
            "Building graph: agent=%s, persona=%s (%s)",
            agent_name,
            persona.id,
            persona.display_name,
        )
    else:
        logger.info("Building graph: agent=%s, no persona.", agent_name)

    if checkpointer is None:
        checkpointer = MemorySaver()

    # ── Graph wiring ──────────────────────────────────────────────────────
    graph = StateGraph(KhadbotState)

    graph.add_node("extract_task", extract_task_node)
    graph.add_node("router", router_node)
    graph.add_node("skill_loader", skill_loader_node)
    graph.add_node("dispatch_skills", dispatch_skills_node)
    graph.add_node("orchestrator", orchestrator_node)

    graph.set_entry_point("extract_task")
    graph.add_edge("extract_task", "router")
    graph.add_edge("router", "skill_loader")
    graph.add_edge("skill_loader", "dispatch_skills")
    graph.add_edge("dispatch_skills", "orchestrator")
    graph.add_edge("orchestrator", END)

    compiled = graph.compile(checkpointer=checkpointer)
    return compiled, resolved_persona_id


# ---------------------------------------------------------------------------
# Session turn runner
# ---------------------------------------------------------------------------


async def run_turn(
    graph: Any,
    user_message: str,
    thread_id: str,
    persona_id: str | None = None,
    callbacks: list | None = None,
) -> dict[str, Any]:
    """
    Run one turn of the coaching conversation.

    Parameters
    ----------
    graph:
        Compiled graph from build_graph().
    user_message:
        The player's raw message text.
    thread_id:
        LangGraph thread ID.  Use a stable identifier per user/channel
        (e.g. Discord user ID) so checkpointed state persists across turns.
        Character context and conversation history are restored automatically.
    persona_id:
        Written into state on the first turn for a new thread.  Subsequent
        turns for the same thread_id inherit it from checkpoint — no need
        to pass it again.
    callbacks:
        Optional LangChain callbacks (LangSmith tracing, CLI tool panel, etc.).

    Returns
    -------
    dict with keys:
        "answer"            — final response string from the orchestrator
        "skill_results"     — raw analysis texts keyed by skill name
        "character_context" — resolved CharacterContext or None
    """
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "callbacks": callbacks or [],
    }

    input_state: dict[str, Any] = {
        "messages": [HumanMessage(content=user_message)],
        "persona_id": persona_id,
        "task": "",
        "needed_skills": [],
        "active_skills": {},
        "skill_results": {},
        "character_context": None,
    }

    result = await graph.ainvoke(input_state, config=config)

    output_messages = result.get("messages", [])
    answer = output_messages[-1].content if output_messages else ""

    return {
        "answer": answer,
        "skill_results": result.get("skill_results", {}),
        "character_context": result.get("character_context"),
    }
