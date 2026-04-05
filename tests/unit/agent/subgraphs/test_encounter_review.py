"""
tests/unit/agent/subgraphs/test_encounter_review.py

Unit tests for agent/subgraphs/encounter_review.py.

Both nodes (fetch_encounter, synthesise) are async and LLM-dependent —
they are exercised in integration tests via test_skill_routing.py.

What IS testable at unit level:
  EncounterReviewState  — TypedDict shape and field defaults
  Graph topology        — compiled graph has the expected nodes and edges
"""

from __future__ import annotations

from khadbot.agent.state import CharacterContext
from khadbot.agent.subgraphs.encounter_review import (
    EncounterReviewState,
    compiled_encounter_review,
)


def _er_state(**kwargs) -> EncounterReviewState:
    base = {
        "task": "",
        "character_context": None,
        "messages": [],
        "result": "",
        "resolved_character_context": None,
    }
    base.update(kwargs)
    return base  # type: ignore[return-value]


class TestEncounterReviewState:
    def test_accepts_character_context(self):
        ctx = CharacterContext(name="Thrall", realm="Stormrage", realm_slug="stormrage", region="us")
        state = _er_state(character_context=ctx)
        assert state["character_context"].name == "Thrall"

    def test_accepts_none_character_context(self):
        state = _er_state(character_context=None)
        assert state["character_context"] is None

    def test_result_defaults_to_empty_string(self):
        state = _er_state()
        assert state["result"] == ""

    def test_resolved_character_context_defaults_to_none(self):
        state = _er_state()
        assert state["resolved_character_context"] is None

    def test_task_defaults_to_empty_string(self):
        state = _er_state()
        assert state["task"] == ""


class TestEncounterReviewGraphTopology:
    def test_compiled_graph_exists(self):
        assert compiled_encounter_review is not None

    def test_graph_has_fetch_and_synthesise_nodes(self):
        nodes = set(compiled_encounter_review.get_graph().nodes.keys())
        assert "fetch_encounter" in nodes
        assert "synthesise" in nodes

    def test_graph_has_exactly_two_skill_nodes(self):
        nodes = {n for n in compiled_encounter_review.get_graph().nodes.keys() if not n.startswith("__")}
        assert nodes == {"fetch_encounter", "synthesise"}

    def test_fetch_leads_to_synthesise(self):
        edges = compiled_encounter_review.get_graph().edges
        edge_pairs = {(e.source, e.target) for e in edges}
        assert ("fetch_encounter", "synthesise") in edge_pairs
