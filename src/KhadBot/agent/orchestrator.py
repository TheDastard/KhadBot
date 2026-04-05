"""
agent/orchestrator.py

Orchestrator node — the only component that speaks to the user.

Responsibilities
----------------
1. Load AgentConfig (cached — no IO per turn).
2. Resolve the active persona via get_persona().
3. Render the base identity + persona voice via PromptAssembler.
4. Append dynamic synthesis instructions containing assembled skill results.
5. Call the LLM and return the final AIMessage.

Prompt assembly strategy
------------------------
The system prompt is built in two layers:

  Layer 1 (static per session):  PromptAssembler.render(agent_config, persona)
      → Renders config/templates/prompt.jinja2 with base_prompt + persona
        guardrail + voice_prompt.  The Jinja2 template is the single source
        of truth for identity + persona framing.

  Layer 2 (dynamic per turn):  Synthesis instructions
      → Appended after Layer 1.  Contains the assembled skill results and
        output-format instructions.  Never enters the router or subgraph
        context windows.

What the orchestrator does NOT do
----------------------------------
- It is not a tool-calling agent.  Tools are internal to subgraphs.
- It does not route, dispatch, or decide which skills ran.
- It does not see tool schemas.  Skill results arrive as plain text.
- It does not alter factual content produced by subgraphs.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage

from khadbot.agent.config import AgentConfig, PersonaConfig, get_agent_config, get_assembler, get_persona
from khadbot.agent.state import KhadbotState
from khadbot.llm_factory import get_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthesis prompt (Layer 2 — dynamic per turn)
# ---------------------------------------------------------------------------

_SYNTHESIS_INSTRUCTIONS = """
---
SYNTHESIS TASK

The following analysis results have been assembled by specialist skills.
Synthesise them into a single coaching response for the player.

Skill results:
{skill_results_block}

Output rules:
- Weave multiple skill results into one coherent response — do not present
  them as labelled sections unless the content genuinely requires it.
- Apply the persona voice (if active) throughout the full response.
- Lead with the highest-impact finding. Do not open with a summary of what
  you are about to say.
- Every claim must be traceable to a data point in the skill results above.
- End with one clear next step the player can take.
- Stay under 500 words unless the complexity of the findings demands more.
"""

_NO_SKILL_INSTRUCTIONS = """
---
No skill data is available for this question. Choose the appropriate response:
- If the question is answerable from general WoW knowledge: answer directly,
  noting this is general guidance rather than data-grounded analysis.
- If you need more information: ask one focused clarifying question.
- If the question is outside KhadBot's coaching scope: say so briefly.
"""


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _build_system_prompt(
    agent_config: AgentConfig,
    persona: PersonaConfig | None,
    skill_results: dict[str, str],
) -> str:
    """
    Assemble the full orchestrator system prompt for this turn.

    Layer 1: PromptAssembler renders the Jinja2 template (base_prompt +
             optional persona guardrail + voice_prompt).
    Layer 2: Dynamic synthesis instructions with embedded skill results.
    """
    # Layer 1 — PromptAssembler renders the Jinja2 template.
    base = get_assembler().render(agent_config, persona=persona)

    # Layer 2 — dynamic per turn.
    if skill_results:
        skill_results_block = "\n\n".join(
            f"[{name}]\n{result.strip()}" for name, result in skill_results.items() if result.strip()
        )
        dynamic = _SYNTHESIS_INSTRUCTIONS.format(skill_results_block=skill_results_block)
    else:
        dynamic = _NO_SKILL_INSTRUCTIONS

    return base.rstrip() + "\n" + dynamic


# ---------------------------------------------------------------------------
# Orchestrator node
# ---------------------------------------------------------------------------


async def orchestrator_node(state: KhadbotState) -> dict[str, Any]:
    """
    Synthesise skill results and produce the final user-facing response.

    Reads:  state["skill_results"], state["messages"], state["persona_id"]
    Writes: state["messages"] (appends AIMessage)
    """
    # AgentConfig is cached — no IO on repeated calls.
    agent_config: AgentConfig = get_agent_config()

    persona_id: str | None = state.get("persona_id")
    persona: PersonaConfig | None = get_persona(persona_id, agent_config)

    skill_results: dict[str, str] = state.get("skill_results", {})

    system_prompt = _build_system_prompt(agent_config, persona, skill_results)

    # Build message list for the LLM: system prompt + conversation history.
    # The current user turn is already in state["messages"] as the last
    # HumanMessage — no need to append it again.
    llm_messages = [SystemMessage(content=system_prompt)]
    llm_messages.extend(state.get("messages", []))

    llm = get_llm()
    try:
        response = await llm.ainvoke(llm_messages)
        answer = response.content
    except Exception as exc:
        logger.error("Orchestrator LLM call failed: %s", exc)
        answer = (
            "I encountered an error assembling the coaching response. "
            "Please try again — if the problem persists, check the LangSmith "
            "trace for details."
        )

    logger.info("Orchestrator produced response (%d chars).", len(answer))
    return {"messages": [AIMessage(content=answer)]}
