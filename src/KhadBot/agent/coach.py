"""
agent/coach.py

Core coaching agent for KhadBot.

build_agent_executor() is responsible for:
  1. Resolving the active persona (from arg or env config)
  2. Filtering the global TOOLS list to the subset declared in the agent YAML
  3. Rendering the system prompt via PromptAssembler
  4. Constructing the agent with create_agent()

The system prompt is assembled once at construction time. Swapping personas
requires rebuilding the agent — persona is session-scoped, not turn-scoped.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from khadbot.agent.agent_config import AgentConfig, PersonaConfig, get_agent_config
from khadbot.agent.personas import get_persona
from khadbot.agent.prompt_assembler import PromptAssembler, get_assembler
from khadbot.tools import TOOLS

load_dotenv()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool filtering
# ---------------------------------------------------------------------------


def resolve_tools(declared_names: list[str], available_tools: list) -> list:
    """
    Filter the available tool objects to those declared in the agent YAML.

    Preserves the declaration order from the YAML. Logs a warning for any
    declared name not found in available_tools (the loader cross-references
    at startup, so this should never happen in production).

    Args:
        declared_names: Tool names from AgentConfig.tools.
        available_tools: Full TOOLS list from src/tools/__init__.py.

    Returns:
        Ordered list of tool objects matching the declared names.
    """
    tool_map = {getattr(t, "name", None): t for t in available_tools}
    resolved = []
    for name in declared_names:
        tool = tool_map.get(name)
        if tool is None:
            logger.warning(f"Declared tool '{name}' not found in available tools — skipping.")
        else:
            resolved.append(tool)
    return resolved


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def build_agent_executor(
    persona: PersonaConfig | None = None,
    agent_name: str = "coach",
    config: AgentConfig | None = None,
    assembler: PromptAssembler | None = None,
    verbose: bool = True,
):
    """
    Build and return a KhadBot agent.

    Args:
        persona:      Active PersonaConfig. If None, checks the env config
                      default (KHADBOT_PERSONA). If that is also unset, no
                      persona is applied.
        agent_name:   Which agent YAML to load. Default: "coach".
        config:       AgentConfig to use. Defaults to get_agent_config().
                      Pass explicitly in tests to avoid the global cache.
        assembler:    PromptAssembler to use. Defaults to get_assembler().
                      Pass explicitly in tests to use a fixture template.
        verbose:      Kept for CLI compatibility.
    """
    from khadbot.config import get_config
    from khadbot.llm_factory import get_llm

    cfg = config or get_agent_config(agent_name)

    # Resolve persona: explicit arg → env default → None
    if persona is None:
        app_cfg = get_config()
        persona = get_persona(app_cfg.persona.default_persona_id, cfg)

    tools = resolve_tools(cfg.tools, TOOLS)
    system_prompt = (assembler or get_assembler()).render(cfg, persona=persona)

    return create_agent(
        model=get_llm(),
        tools=tools,
        system_prompt=system_prompt,
    )


# ---------------------------------------------------------------------------
# Single-turn helper (CLI and Discord bot)
# ---------------------------------------------------------------------------


async def ask_coach(
    agent,
    user_message: str,
    chat_history: list | None = None,
    callbacks: list | None = None,
) -> dict:
    """
    Run one turn of the coaching conversation.

    Args:
        agent:        Agent built by build_agent_executor()
        user_message: The player's question
        chat_history: List of prior (HumanMessage, AIMessage) pairs
        callbacks:    Optional LangChain callbacks (e.g. LangSmith tracing)

    Returns:
        dict with keys:
          "answer" (str)  — the agent's final response
          "steps"  (list) — (tool_name, tool_output) pairs from this turn
    """
    messages = list(chat_history or [])
    messages.append(HumanMessage(content=user_message))

    result = await agent.ainvoke(
        {"messages": messages},
        config={"callbacks": callbacks or []},
    )

    output_messages = result.get("messages", [])
    answer = output_messages[-1].content if output_messages else ""
    steps = [(m.name, m.content) for m in output_messages if hasattr(m, "type") and m.type == "tool"]

    return {"answer": answer, "steps": steps}
