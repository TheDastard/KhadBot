"""
tests/unit/agent/test_graph.py

Unit tests for agent/graph.py.

Coverage:
  extract_task_node      — message parsing
  dispatch_skills_node   — subgraph fan-out, error handling, context propagation
  build_graph wiring     — persona resolution, return shape, graph compiles
                           (adapted from TestBuildAgentExecutor in retired test_coach_agent.py)

No real LLM calls. No network. No file I/O beyond tmp_path fixtures.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from khadbot.agent.graph import (
    build_graph,
    dispatch_skills_node,
    extract_task_node,
)
from khadbot.agent.state import CharacterContext

# ---------------------------------------------------------------------------
# extract_task_node
# ---------------------------------------------------------------------------


class TestExtractTaskNode:
    def test_extracts_last_human_message(self, make_state):
        state = make_state(
            messages=[
                HumanMessage(content="first"),
                AIMessage(content="response"),
                HumanMessage(content="second"),
            ]
        )
        assert extract_task_node(state)["task"] == "second"

    def test_skips_ai_messages(self, make_state):
        state = make_state(messages=[AIMessage(content="only AI")])
        assert extract_task_node(state)["task"] == ""

    def test_empty_messages_returns_empty_task(self, make_state):
        state = make_state(messages=[])
        assert extract_task_node(state)["task"] == ""

    def test_non_string_content_coerced_to_str(self, make_state):
        msg = HumanMessage(content=["part1", "part2"])
        state = make_state(messages=[msg])
        result = extract_task_node(state)
        assert isinstance(result["task"], str)

    def test_reads_only_latest_human_message(self, make_state):
        state = make_state(
            messages=[
                HumanMessage(content="old question"),
                HumanMessage(content="new question"),
            ]
        )
        assert extract_task_node(state)["task"] == "new question"


# ---------------------------------------------------------------------------
# dispatch_skills_node
# ---------------------------------------------------------------------------


class TestDispatchSkillsNode:
    @pytest.mark.asyncio
    async def test_result_stored_under_skill_name(self, make_state, make_skill):
        skill = make_skill("personal_coaching", result="Great analysis!")
        state = make_state(
            task="why is my dps low?",
            active_skills={"personal_coaching": skill},
        )
        result = await dispatch_skills_node(state)
        assert result["skill_results"]["personal_coaching"] == "Great analysis!"

    @pytest.mark.asyncio
    async def test_subgraph_exception_stored_as_error_string(self, make_state, make_skill):
        skill = make_skill("personal_coaching")
        skill.subgraph.ainvoke.side_effect = RuntimeError("subgraph exploded")
        state = make_state(task="analyse me", active_skills={"personal_coaching": skill})

        result = await dispatch_skills_node(state)

        assert "personal_coaching" in result["skill_results"]
        assert "error" in result["skill_results"]["personal_coaching"].lower()

    @pytest.mark.asyncio
    async def test_character_context_propagated_from_subgraph(self, make_state, make_skill):
        ctx = CharacterContext(name="Thrall", realm="Stormrage", realm_slug="stormrage", region="us")
        skill = make_skill("personal_coaching")
        skill.subgraph.ainvoke = AsyncMock(
            return_value={
                "result": "analysis",
                "resolved_character_context": ctx,
            }
        )
        state = make_state(task="check me", active_skills={"personal_coaching": skill})

        result = await dispatch_skills_node(state)

        assert result.get("character_context") is ctx

    @pytest.mark.asyncio
    async def test_multiple_skills_all_dispatched(self, make_state, make_skill):
        skills = {
            "personal_coaching": make_skill("personal_coaching", "perf"),
            "build_review": make_skill("build_review", "build"),
        }
        state = make_state(task="help", active_skills=skills)
        result = await dispatch_skills_node(state)

        assert result["skill_results"]["personal_coaching"] == "perf"
        assert result["skill_results"]["build_review"] == "build"

    @pytest.mark.asyncio
    async def test_empty_active_skills_returns_empty_results(self, make_state):
        state = make_state(task="anything", active_skills={})
        result = await dispatch_skills_node(state)
        assert result["skill_results"] == {}

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_other_skills(self, make_state, make_skill):
        failing = make_skill("encounter_review")
        failing.subgraph.ainvoke.side_effect = RuntimeError("failed")
        passing = make_skill("build_review", "build ok")

        state = make_state(
            task="help",
            active_skills={"encounter_review": failing, "build_review": passing},
        )
        result = await dispatch_skills_node(state)

        assert "encounter_review" in result["skill_results"]
        assert result["skill_results"]["build_review"] == "build ok"

    @pytest.mark.asyncio
    async def test_context_from_first_skill_passed_to_second(self, make_state, make_skill):
        ctx = CharacterContext(name="Thrall", realm="Stormrage", realm_slug="stormrage", region="us")
        received_contexts: list = []

        async def first_subgraph(input_):
            return {"result": "first", "resolved_character_context": ctx}

        async def second_subgraph(input_):
            received_contexts.append(input_.get("character_context"))
            return {"result": "second"}

        skill1 = make_skill("personal_coaching")
        skill1.subgraph.ainvoke = first_subgraph
        skill2 = make_skill("build_review")
        skill2.subgraph.ainvoke = second_subgraph

        # Use ordered dict to guarantee dispatch order
        from collections import OrderedDict

        skills = OrderedDict([("personal_coaching", skill1), ("build_review", skill2)])
        state = make_state(task="help", active_skills=skills)
        await dispatch_skills_node(state)

        assert len(received_contexts) == 1
        assert received_contexts[0] is ctx


# ---------------------------------------------------------------------------
# build_graph wiring
# (adapted from TestBuildAgentExecutor / TestBuildAgentExecutorConfigFallback
#  in the retired test_coach_agent.py)
# ---------------------------------------------------------------------------


class TestBuildGraphWiring:
    """
    Verifies that build_graph() resolves persona correctly and returns a
    compilable graph. Does not test graph execution — that belongs in
    test_skill_routing.py.
    """

    def _patched_build(self, **kwargs):
        """Call build_graph() with all external dependencies patched."""
        mock_cfg = MagicMock()
        mock_cfg.base_prompt = "You are KhadBot."
        mock_cfg.personas = []
        mock_cfg.list_persona_ids.return_value = []

        with (
            patch("khadbot.agent.graph.get_agent_config", return_value=mock_cfg),
            patch("khadbot.agent.skills.initialise_registry"),
            patch("khadbot.agent.graph.resolve_session_persona", return_value=kwargs.get("persona", None)),
        ):
            return build_graph(
                persona_id=kwargs.get("persona_id"),
                checkpointer=MemorySaver(),
            )

    def test_returns_tuple_of_graph_and_persona_id(self):
        graph, pid = self._patched_build()
        assert graph is not None
        assert pid is None  # no persona set

    def test_resolved_persona_id_returned(self):
        mock_persona = MagicMock()
        mock_persona.id = "thrall"

        mock_cfg = MagicMock()
        mock_cfg.base_prompt = "You are KhadBot."
        mock_cfg.personas = []

        with (
            patch("khadbot.agent.graph.get_agent_config", return_value=mock_cfg),
            patch("khadbot.agent.skills.initialise_registry"),
            patch("khadbot.agent.graph.resolve_session_persona", return_value=mock_persona),
        ):
            _, pid = build_graph(persona_id="thrall", checkpointer=MemorySaver())

        assert pid == "thrall"

    def test_no_persona_returns_none_id(self):
        _, pid = self._patched_build(persona=None)
        assert pid is None

    def test_graph_has_expected_nodes(self):
        graph, _ = self._patched_build()
        node_names = set(graph.get_graph().nodes.keys())
        for expected in {"extract_task", "router", "skill_loader", "dispatch_skills", "orchestrator"}:
            assert expected in node_names, f"Missing node: {expected}"

    def test_explicit_persona_id_forwarded_to_resolve(self):
        mock_cfg = MagicMock()
        mock_cfg.base_prompt = "You are KhadBot."
        mock_cfg.personas = []

        captured = {}

        def fake_resolve(explicit_id=None, config=None):
            captured["explicit_id"] = explicit_id
            return None

        with (
            patch("khadbot.agent.graph.get_agent_config", return_value=mock_cfg),
            patch("khadbot.agent.skills.initialise_registry"),
            patch("khadbot.agent.graph.resolve_session_persona", side_effect=fake_resolve),
        ):
            build_graph(persona_id="khadgar", checkpointer=MemorySaver())

        assert captured["explicit_id"] == "khadgar"

    def test_env_var_persona_resolved_via_resolve_session_persona(self):
        """build_graph delegates env var lookup to resolve_session_persona — not itself."""
        mock_cfg = MagicMock()
        mock_cfg.base_prompt = "You are KhadBot."
        mock_cfg.personas = []
        mock_persona = MagicMock()
        mock_persona.id = "thrall"

        with (
            patch.dict(os.environ, {"KHADBOT_PERSONA": "thrall"}),
            patch("khadbot.agent.graph.get_agent_config", return_value=mock_cfg),
            patch("khadbot.agent.skills.initialise_registry"),
            patch("khadbot.agent.graph.resolve_session_persona", return_value=mock_persona),
        ):
            _, pid = build_graph(persona_id=None, checkpointer=MemorySaver())

        assert pid == "thrall"
