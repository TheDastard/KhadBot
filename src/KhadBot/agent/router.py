"""
agent/router.py

Router node for the KhadBot LangGraph graph.

The router's only job is to read the current user message and output a list
of skill names to invoke.  It is deliberately lightweight:

  - Uses a fast/cheap model (configurable, defaults to the configured LLM
    but can be overridden to a smaller model for cost efficiency).
  - Has no persona, no conversation history, no tool awareness.
  - Outputs structured JSON only — no free-form reasoning in the response.
  - Validates output against SKILL_REGISTRY so hallucinated skill names
    never reach the skill loader.

Router prompt design
--------------------
The router sees:
  - The user's current message (task).
  - A newline-separated list of skill name + routing_description pairs.
    routing_description is longer and more nuanced than description, which
    is what the orchestrator sees — this asymmetry is intentional.

The router does NOT see:
  - Conversation history (stateless classifier).
  - Persona (irrelevant to routing).
  - Tool schemas (tools are internal to subgraphs).
  - Character context (routing is message-content-only).

Failure handling
----------------
JSON parse failures and empty lists are both valid outputs — the skill
loader handles them gracefully.  The orchestrator responds from base
knowledge when no skills are loaded.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from khadbot.agent.skills import SKILL_REGISTRY, SkillDefinition
from khadbot.agent.state import KhadbotState
from khadbot.llm_factory import get_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router prompt
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM = """\
You are a routing classifier for a World of Warcraft coaching assistant.

Your task: read the player's message and identify which skills are needed
to answer it. Be conservative — only include skills that are clearly
necessary. Most questions require exactly one skill.

Available skills:
{skill_descriptions}

Respond ONLY with a valid JSON array of skill names, e.g.:
["personal_coaching"]
["encounter_review"]
["build_review"]
["character_sim"]
[]

Rules:
- Output ONLY the JSON array. No explanation, no markdown, no preamble.
- Use [] if no skill is needed (general question answerable from knowledge).
- Never include a skill name not in the list above.
- If the message is ambiguous between personal_coaching and encounter_review,
  prefer encounter_review when group mechanics are mentioned, personal_coaching
  otherwise.
"""


def _build_skill_descriptions(registry: dict[str, SkillDefinition]) -> str:
    """
    Format skill routing descriptions for the router prompt.
    Uses routing_description (longer) not description (one-liner).
    """
    lines = []
    for name, skill in registry.items():
        lines.append(f"- {name}: {skill.routing_description}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Router node
# ---------------------------------------------------------------------------


async def router_node(state: KhadbotState) -> dict[str, Any]:
    """
    Classify the current task and write needed_skills to state.

    Reads:  state["task"]
    Writes: state["needed_skills"]
    """
    task = state.get("task", "")
    if not task:
        # No task extracted — pass through with empty skill list.
        logger.warning("Router received empty task — skipping classification.")
        return {"needed_skills": []}

    if not SKILL_REGISTRY:
        logger.warning("SKILL_REGISTRY is empty — router returning no skills.")
        return {"needed_skills": []}

    skill_descriptions = _build_skill_descriptions(SKILL_REGISTRY)
    system_prompt = _ROUTER_SYSTEM.format(skill_descriptions=skill_descriptions)

    llm = get_llm()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=task),
    ]

    try:
        response = await llm.ainvoke(messages)
        raw = response.content.strip()

        # Strip markdown fences if the model wrapped the JSON.
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(line for line in lines if not line.startswith("```")).strip()

        needed: list[str] = json.loads(raw)

        # Validate — drop any hallucinated skill names.
        validated = [s for s in needed if s in SKILL_REGISTRY]
        dropped = set(needed) - set(validated)
        if dropped:
            logger.warning("Router returned unknown skill names, dropping: %s", dropped)

        logger.info("Router selected skills: %s", validated)
        return {"needed_skills": validated}

    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.warning("Router failed to parse skill list: %s — defaulting to []", exc)
        return {"needed_skills": []}
