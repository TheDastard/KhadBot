"""
agent/skills.py

Skill registry, SkillDefinition dataclass, and the skill_loader_node.

The registry is built at module import time by loading all YAML files from
config/skills/ and pairing each with its compiled subgraph.

Relationship to config/loader.py
---------------------------------
config/loader.py manages the agent-level YAML (which tool names the agent
declares, which persona IDs it supports).  skills.py manages the skill-level
YAML (which tools each skill subgraph uses, routing hints, behavioural flags).
These are parallel concerns — loader.py does not know about skills.

Personas are NOT loaded here.  Persona loading is handled exclusively through
AgentConfig via get_agent_config() in config/loader.py.  Skills have no
persona awareness — that belongs to the orchestrator only.

Context window discipline
--------------------------
The orchestrator sees only SkillDefinition.name and SkillDefinition.description.
SkillDefinition.tools and SkillDefinition.subgraph are internal to subgraphs
and never appear in any LLM context window.

Adding a new skill
-------------------
1. Create config/skills/<name>.yaml.
2. Create agent/subgraphs/<name>.py, compile the subgraph.
3. Import the compiled graph in agent/subgraphs/__init__.py and add it
   to SKILL_SUBGRAPH_MAP there.
No changes needed in skills.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import BaseTool

from khadbot.agent.state import KhadbotState
from khadbot.agent.subgraphs import SKILL_SUBGRAPH_MAP

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def _skills_dir() -> Path:
    import os

    config_root = os.environ.get("KHADBOT_CONFIG_DIR", "config")
    return Path(config_root) / "skills"


# ---------------------------------------------------------------------------
# SkillDefinition
# ---------------------------------------------------------------------------


@dataclass
class SkillDefinition:
    """
    Runtime representation of a skill.

    Loaded from YAML (metadata) and paired with a compiled subgraph
    (implementation).  The YAML half is what the system knows about the skill;
    the subgraph half is what it does.  They are kept separate so the
    orchestrator can see skill descriptions without seeing tool schemas.
    """

    # Visible to the orchestrator's LLM.
    name: str
    display_name: str
    description: str  # one sentence — the only thing orchestrator sees

    # Visible to the router's LLM (longer, more nuanced than description).
    routing_description: str

    # Internal — never visible to orchestrator or router.
    tools: list[BaseTool]
    subgraph: Callable  # compiled LangGraph subgraph

    # Behavioural flags from YAML.
    requires_character_context: bool = False
    fetch_raiderio: bool = False
    requires_confirmation: bool = False
    confirmation_prompt: str = ""

    # Full YAML data for inspection and debugging.
    raw: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Tool map — maps YAML tool name strings to live @tool objects
# ---------------------------------------------------------------------------


def _build_tool_map() -> dict[str, BaseTool]:
    from khadbot.tools.rag_search import search_guide_rag
    from khadbot.tools.raiderio import get_character_raiderio
    from khadbot.tools.simc import run_simc
    from khadbot.tools.warcraftlogs.character import find_character_reports
    from khadbot.tools.warcraftlogs.encounter import get_encounter_analysis
    from khadbot.tools.warcraftlogs.performance import get_warcraftlogs_report

    return {
        "find_character_reports": find_character_reports,
        "get_warcraftlogs_report": get_warcraftlogs_report,
        "get_encounter_analysis": get_encounter_analysis,
        "get_character_raiderio": get_character_raiderio,
        "run_simc": run_simc,
        "search_guide_rag": search_guide_rag,
    }


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------


def _load_skill(
    yaml_path: Path,
    subgraph_map: dict[str, Callable],
    tool_map: dict[str, BaseTool],
) -> SkillDefinition | None:
    """
    Load one skill YAML file and pair it with its compiled subgraph.

    Returns None (with a warning) when the YAML references a skill name
    with no registered subgraph — allows partial loading during development.
    """
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to load skill YAML %s: %s", yaml_path, exc)
        return None

    name = data.get("name", yaml_path.stem)

    subgraph = subgraph_map.get(name)
    if subgraph is None:
        logger.warning(
            "Skill '%s' defined in YAML but has no registered subgraph — skipping.",
            name,
        )
        return None

    tool_names: list[str] = data.get("tools", [])
    tools: list[BaseTool] = []
    for tool_name in tool_names:
        t = tool_map.get(tool_name)
        if t is None:
            logger.warning("Skill '%s' references unknown tool '%s' — skipping tool.", name, tool_name)
        else:
            tools.append(t)

    return SkillDefinition(
        name=name,
        display_name=data.get("display_name", name),
        description=data.get("description", "").strip(),
        routing_description=data.get("routing_description", data.get("description", "")).strip(),
        tools=tools,
        subgraph=subgraph,
        requires_character_context=data.get("requires_character_context", False),
        fetch_raiderio=data.get("fetch_raiderio", False),
        requires_confirmation=data.get("requires_confirmation", False),
        confirmation_prompt=data.get("confirmation_prompt", "").strip(),
        raw=data,
    )


def build_skill_registry() -> dict[str, SkillDefinition]:
    """
    Build the full skill registry from all YAML files in config/skills/.

    Skills whose subgraph is not yet registered are skipped with a warning,
    allowing the application to start in a partial state during development.
    """
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        logger.warning("Skills config directory not found: %s", skills_dir)
        return {}

    subgraph_map = SKILL_SUBGRAPH_MAP
    tool_map = _build_tool_map()

    registry: dict[str, SkillDefinition] = {}
    for yaml_path in sorted(skills_dir.glob("*.yaml")):
        skill = _load_skill(yaml_path, subgraph_map, tool_map)
        if skill is not None:
            registry[skill.name] = skill
            logger.debug("Registered skill: %s", skill.name)

    logger.info("Loaded %d skills: %s", len(registry), list(registry.keys()))
    return registry


# Module-level registry — populated by initialise_registry() at startup.
# Tests can replace this by patching khadbot.agent.skills.SKILL_REGISTRY.
SKILL_REGISTRY: dict[str, SkillDefinition] = {}


def initialise_registry() -> None:
    """
    Populate SKILL_REGISTRY from config/skills/*.yaml.

    Called once at application startup by graph.build_graph().
    Idempotent — safe to call multiple times (no-op after first call).
    """
    global SKILL_REGISTRY
    if not SKILL_REGISTRY:
        SKILL_REGISTRY = build_skill_registry()


# ---------------------------------------------------------------------------
# skill_loader_node — pure Python, no LLM
# ---------------------------------------------------------------------------


def skill_loader_node(state: KhadbotState) -> dict:
    """
    Translate the router's skill name list into live SkillDefinition objects.

    Validates each name against SKILL_REGISTRY.  Unknown names (router
    hallucinations) are dropped with a warning.  The orchestrator handles
    the empty-skills case by responding from base knowledge.

    Reads:  state["needed_skills"]
    Writes: state["active_skills"]
    """
    active: dict[str, SkillDefinition] = {}

    for name in state.get("needed_skills", []):
        skill = SKILL_REGISTRY.get(name)
        if skill is None:
            logger.warning("Router requested unknown skill '%s' — not in registry, dropping.", name)
            continue
        active[name] = skill

    if not active:
        logger.info("No skills loaded — orchestrator will respond from base knowledge.")

    return {"active_skills": active}
