"""
tests/unit/agent/test_orchestrator.py

Unit tests for agent/orchestrator.py.

Coverage: orchestrator_node — prompt assembly, skill result embedding,
no-skill fallback, persona injection, LLM failure handling.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from khadbot.agent.config.loader import AgentConfig, PersonaConfig
from khadbot.agent.orchestrator import orchestrator_node


def _mock_cfg(base_prompt: str = "You are KhadBot.") -> AgentConfig:
    return AgentConfig(
        name="test",
        version="0.1",
        base_prompt=base_prompt,
        tools=[],
        personas=[],
    )


def _mock_persona(voice: str = "Speak as Thrall.") -> PersonaConfig:
    return PersonaConfig(
        id="thrall",
        display_name="Thrall",
        intro_message="Greetings.",
        voice_prompt=voice,
    )


class TestOrchestratorNode:
    @pytest.mark.asyncio
    async def test_appends_ai_message_to_messages(self, make_llm, make_state):
        llm = make_llm(response="Great advice!")
        state = make_state(
            messages=[HumanMessage(content="help me")],
            skill_results={"personal_coaching": "72nd percentile"},
        )

        with (
            patch("khadbot.agent.orchestrator.get_agent_config", return_value=_mock_cfg()),
            patch("khadbot.agent.orchestrator.get_persona", return_value=None),
            patch("khadbot.agent.orchestrator.get_llm", return_value=llm),
        ):
            result = await orchestrator_node(state)

        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)
        assert result["messages"][0].content == "Great advice!"

    @pytest.mark.asyncio
    async def test_skill_results_embedded_in_system_prompt(self, make_llm, make_state):
        captured = []

        llm = make_llm(capture=captured)
        state = make_state(
            messages=[HumanMessage(content="analyse me")],
            skill_results={"personal_coaching": "UNIQUE_SENTINEL_TEXT"},
        )

        with (
            patch("khadbot.agent.orchestrator.get_agent_config", return_value=_mock_cfg()),
            patch("khadbot.agent.orchestrator.get_persona", return_value=None),
            patch("khadbot.agent.orchestrator.get_llm", return_value=llm),
        ):
            await orchestrator_node(state)

        assert "UNIQUE_SENTINEL_TEXT" in captured[0].content

    @pytest.mark.asyncio
    async def test_empty_skill_results_uses_fallback_prompt(self, make_llm, make_state):
        captured = []

        llm = make_llm(capture=captured)
        state = make_state(
            messages=[HumanMessage(content="general question")],
            skill_results={},
        )

        with (
            patch("khadbot.agent.orchestrator.get_agent_config", return_value=_mock_cfg()),
            patch("khadbot.agent.orchestrator.get_persona", return_value=None),
            patch("khadbot.agent.orchestrator.get_llm", return_value=llm),
        ):
            await orchestrator_node(state)

        assert "SYNTHESIS TASK" not in captured[0].content

    @pytest.mark.asyncio
    async def test_llm_failure_returns_friendly_error(self, make_llm, make_state):
        llm = make_llm(side_effect=RuntimeError("LLM unreachable"))
        state = make_state(messages=[HumanMessage(content="help")])

        with (
            patch("khadbot.agent.orchestrator.get_agent_config", return_value=_mock_cfg()),
            patch("khadbot.agent.orchestrator.get_persona", return_value=None),
            patch("khadbot.agent.orchestrator.get_llm", return_value=llm),
        ):
            result = await orchestrator_node(state)

        assert "error" in result["messages"][0].content.lower()

    @pytest.mark.asyncio
    async def test_persona_voice_present_in_system_prompt(self, make_llm, make_state):
        captured = []

        llm = make_llm(capture=captured)
        persona = _mock_persona(voice="UNIQUE_VOICE_SENTINEL")
        state = make_state(
            messages=[HumanMessage(content="help")],
            persona_id="thrall",
        )

        with (
            patch("khadbot.agent.orchestrator.get_agent_config", return_value=_mock_cfg()),
            patch("khadbot.agent.orchestrator.get_persona", return_value=persona),
            patch("khadbot.agent.orchestrator.get_llm", return_value=llm),
        ):
            await orchestrator_node(state)

        assert "UNIQUE_VOICE_SENTINEL" in captured[0].content

    @pytest.mark.asyncio
    async def test_base_prompt_always_present(self, make_llm, make_state):
        captured = []

        llm = make_llm(capture=captured)
        state = make_state(messages=[HumanMessage(content="help")])

        with (
            patch("khadbot.agent.orchestrator.get_agent_config", return_value=_mock_cfg("UNIQUE_BASE_PROMPT")),
            patch("khadbot.agent.orchestrator.get_persona", return_value=None),
            patch("khadbot.agent.orchestrator.get_llm", return_value=llm),
        ):
            await orchestrator_node(state)

        assert "UNIQUE_BASE_PROMPT" in captured[0].content

    @pytest.mark.asyncio
    async def test_multiple_skill_results_all_embedded(self, make_llm, make_state):
        captured = []

        llm = make_llm(capture=captured)
        state = make_state(
            messages=[HumanMessage(content="help")],
            skill_results={
                "personal_coaching": "PERF_SENTINEL",
                "build_review": "BUILD_SENTINEL",
            },
        )

        with (
            patch("khadbot.agent.orchestrator.get_agent_config", return_value=_mock_cfg()),
            patch("khadbot.agent.orchestrator.get_persona", return_value=None),
            patch("khadbot.agent.orchestrator.get_llm", return_value=llm),
        ):
            await orchestrator_node(state)

        system = captured[0].content
        assert "PERF_SENTINEL" in system
        assert "BUILD_SENTINEL" in system
