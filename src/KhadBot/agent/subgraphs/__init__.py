"""
agent/subgraphs/__init__.py

Compiled LangGraph subgraphs for each KhadBot skill.

Each subgraph is a self-contained agent loop — it receives a structured task,
calls its own tools, and returns a result string.  Subgraphs have no access
to the parent graph's persona, conversation history, or other subgraphs'
tools.

Exports
-------
compiled_personal_coaching  — parse analysis, cast efficiency, spec guidance
compiled_encounter_review   — wipe analysis, deaths, avoidable damage, CDs
compiled_build_review       — talent/rotation/stat questions from Icy Veins RAG
compiled_character_sim      — SimulationCraft gear and talent comparisons

Usage in skills.py
-------------------
    from khadbot.agent.subgraphs import SKILL_SUBGRAPH_MAP

Adding a new skill
------------------
1. Create agent/subgraphs/<n>.py, define and compile the subgraph.
2. Import the compiled graph below and add it to SKILL_SUBGRAPH_MAP.
3. Create config/skills/<n>.yaml.
No changes needed in skills.py or graph.py.
"""

from khadbot.agent.subgraphs.build_review import compiled_build_review
from khadbot.agent.subgraphs.character_sim import compiled_character_sim
from khadbot.agent.subgraphs.encounter_review import compiled_encounter_review
from khadbot.agent.subgraphs.personal_coaching import compiled_personal_coaching

# Canonical map from skill name → compiled subgraph.
# Consumed directly by skills.py — no lazy _build_subgraph_map() needed.
SKILL_SUBGRAPH_MAP: dict[str, object] = {
    "personal_coaching": compiled_personal_coaching,
    "encounter_review": compiled_encounter_review,
    "build_review": compiled_build_review,
    "character_sim": compiled_character_sim,
}

__all__ = [
    "compiled_personal_coaching",
    "compiled_encounter_review",
    "compiled_build_review",
    "compiled_character_sim",
    "SKILL_SUBGRAPH_MAP",
]
