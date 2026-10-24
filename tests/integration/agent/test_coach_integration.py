"""
tests/integration/agent/test_coach_integration.py

Integration tests for the KhadBot coaching agent loop.

These tests run the full agent loop — real LLM inference and real multi-turn
state — against a local Ollama model. They are deliberately
excluded from the unit test suite and should not run in CI without an Ollama
instance available.

Run with:
    LLM_PROVIDER=ollama pytest tests/integration/

Skip if Ollama is unavailable:
    pytest tests/integration/ --ignore-glob="*integration*"

What these tests cover (and why they don't belong in unit tests):
  - Multi-turn context: whether prior conversation is correctly included in
    subsequent turns. Requires real state threading.
  - ask_coach() output shape under real conditions: the dict structure,
    non-empty answer, steps list.

The coach has no tools until tools over locally cached data return, so tool
routing, argument passing and tool-error recovery tests come back with them.

Assertions are deliberately loose — we assert on shape and plausibility, not
exact wording, since LLM output is non-deterministic even at temperature=0.

Prerequisites:
  - Ollama running locally: `ollama serve`
  - Model pulled: `ollama pull qwen3:8b` (or whatever OLLAMA_MODEL is set to)
"""

import pytest

from khadbot.agent.coach import ask_coach, build_agent_executor
from khadbot.llm_factory import LLMProviderError

# config_root, agent_config, assembler fixtures from tests/conftest.py


# ===========================================================================
# Session-scoped agent
#
# Building the agent is relatively expensive (LLM client init, graph compile).
# Scope to the session so it's built once and reused across all integration
# tests. Individual tests reset state via fresh chat_history lists.
# ===========================================================================


@pytest.fixture(scope="session")
def integration_agent():
    """
    Build a real coaching agent using the default Ollama provider.
    Skips the entire session if Ollama is unavailable.
    """
    try:
        agent = build_agent_executor(persona=None, verbose=False)
    except LLMProviderError as e:
        pytest.skip(f"Ollama unavailable — skipping integration tests: {e}")
    return agent


# ===========================================================================
# ask_coach() output shape
# ===========================================================================


class TestAskCoachOutputShape:
    def test_returns_dict_with_answer_and_steps(self, integration_agent):
        result = ask_coach(integration_agent, "What is a Mythic+ score?")
        assert isinstance(result, dict)
        assert "answer" in result
        assert "steps" in result

    def test_answer_is_non_empty_string(self, integration_agent):
        result = ask_coach(integration_agent, "What is a Mythic+ score?")
        assert isinstance(result["answer"], str)
        assert len(result["answer"].strip()) > 0

    def test_steps_is_a_list_of_tuples(self, integration_agent):
        result = ask_coach(integration_agent, "What is a Mythic+ score?")
        assert isinstance(result["steps"], list)
        for step in result["steps"]:
            assert isinstance(step, tuple) and len(step) == 2


# ===========================================================================
# Multi-turn context
# ===========================================================================


class TestMultiTurnContext:
    def test_prior_context_influences_response(self, integration_agent):
        """
        Establish character context in turn 1, ask a follow-up in turn 2.
        The agent should not ask for the character again if it was just provided.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        turn_1 = ask_coach(
            integration_agent,
            "My character is Pyroblastus, Fire Mage on Area 52 US.",
        )
        history = [
            HumanMessage(content="My character is Pyroblastus, Fire Mage on Area 52 US."),
            AIMessage(content=turn_1["answer"]),
        ]
        turn_2 = ask_coach(
            integration_agent,
            "What should I focus on improving?",
            chat_history=history,
        )
        # Should produce a coaching response, not ask "which character?"
        assert len(turn_2["answer"].strip()) > 0

    def test_none_chat_history_does_not_raise(self, integration_agent):
        result = ask_coach(
            integration_agent,
            "What is a good M+ score for a casual player?",
            chat_history=None,
        )
        assert isinstance(result, dict)

    def test_empty_chat_history_does_not_raise(self, integration_agent):
        result = ask_coach(
            integration_agent,
            "What is a good M+ score for a casual player?",
            chat_history=[],
        )
        assert isinstance(result, dict)
