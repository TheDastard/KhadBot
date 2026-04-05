"""
tests/unit/agent/subgraphs/test_build_review.py

Unit tests for agent/subgraphs/build_review.py.

Both nodes (fetch_guide_data, synthesise) are async and LLM-dependent —
they are exercised in integration tests via test_skill_routing.py.

What IS testable at unit level:
  BuildReviewState  — TypedDict shape and field defaults
  Graph topology    — compiled graph has the expected nodes and edges

Notable difference from personal_coaching and encounter_review:
  BuildReviewState does NOT have resolved_character_context — this subgraph
  is spec-theory focused and does not perform character discovery.
"""

from __future__ import annotations

from khadbot.agent.state import CharacterContext
from khadbot.agent.subgraphs.build_review import (
    BuildReviewState,
    compiled_build_review,
)


def _br_state(**kwargs) -> BuildReviewState:
    base = {
        "task": "",
        "character_context": None,
        "messages": [],
        "result": "",
    }
    base.update(kwargs)
    return base  # type: ignore[return-value]


class TestBuildReviewState:
    def test_task_defaults_to_empty_string(self):
        state = _br_state()
        assert state["task"] == ""

    def test_result_defaults_to_empty_string(self):
        state = _br_state()
        assert state["result"] == ""

    def test_character_context_defaults_to_none(self):
        state = _br_state()
        assert state["character_context"] is None

    def test_accepts_character_context(self):
        ctx = CharacterContext(name="Jaina", realm="Area 52", realm_slug="area-52", region="us")
        state = _br_state(character_context=ctx)
        assert state["character_context"].name == "Jaina"

    def test_no_resolved_character_context_field(self):
        """
        build_review does not perform character discovery so it has no
        resolved_character_context field — unlike personal_coaching and
        encounter_review.
        """
        state = _br_state()
        assert "resolved_character_context" not in state


class TestBuildReviewGraphTopology:
    def test_compiled_graph_exists(self):
        assert compiled_build_review is not None

    def test_graph_has_fetch_and_synthesise_nodes(self):
        nodes = set(compiled_build_review.get_graph().nodes.keys())
        assert "fetch_guide_data" in nodes
        assert "synthesise" in nodes

    def test_graph_has_exactly_two_skill_nodes(self):
        nodes = {n for n in compiled_build_review.get_graph().nodes.keys() if not n.startswith("__")}
        assert nodes == {"fetch_guide_data", "synthesise"}

    def test_fetch_leads_to_synthesise(self):
        edges = compiled_build_review.get_graph().edges
        edge_pairs = {(e.source, e.target) for e in edges}
        assert ("fetch_guide_data", "synthesise") in edge_pairs
