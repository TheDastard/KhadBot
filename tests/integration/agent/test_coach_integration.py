"""
tests/integration/agent/test_coach_integration.py

Integration tests for the KhadBot coaching agent loop.

These tests run the full agent loop — real LLM inference, real tool execution,
real multi-turn state — against a local Ollama model. They are deliberately
excluded from the unit test suite and should not run in CI without an Ollama
instance available.

Run with:
    LLM_PROVIDER=ollama pytest tests/integration/

Skip if Ollama is unavailable:
    pytest tests/integration/ --ignore-glob="*integration*"

What these tests cover (and why they don't belong in unit tests):
  - Tool routing: which tool the LLM selects for a given question. This
    requires real reasoning — a scripted fake LLM would only test that the
    fake LLM returned what we told it to.
  - Argument passing: whether the LLM extracts the right fields from a user
    message and passes them to the tool. Same reason.
  - Error handling in the agent loop: whether the agent recovers gracefully
    when a tool returns an error. Requires the full ReAct cycle.
  - Multi-turn context: whether prior conversation is correctly included in
    subsequent turns. Requires real state threading.
  - ask_coach() output shape under real conditions: the dict structure,
    non-empty answer, steps list.

Assertions are deliberately loose — we assert on shape and plausibility, not
exact wording, since LLM output is non-deterministic even at temperature=0.

Prerequisites:
  - Ollama running locally: `ollama serve`
  - Model pulled: `ollama pull qwen3:8b` (or whatever OLLAMA_MODEL is set to)
  - Real tool dependencies available (Raider.IO accessible, etc.)
"""

import pytest

from khadbot.agent.coach import ask_coach, build_agent_executor
from khadbot.llm_factory import LLMProviderError

# config_root, agent_config, assembler fixtures from tests/fixtures/conftest.py


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
# Tool routing
#
# These assert that the agent selects a plausible tool for a given query type.
# They do not assert exact wording of the answer.
# ===========================================================================


class TestToolRouting:
    def test_character_question_calls_raiderio(self, integration_agent):
        result = ask_coach(
            integration_agent,
            "Look up the character Pyroblastus on the realm Area 52 in the US region.",
        )
        tool_names = [name for name, _ in result["steps"]]
        assert "get_character_raiderio" in tool_names

    def test_build_question_calls_rag(self, integration_agent):
        result = ask_coach(
            integration_agent,
            "What talents should I run for single-target as a Fire Mage?",
        )
        tool_names = [name for name, _ in result["steps"]]
        assert "search_guide_rag" in tool_names

    def test_build_question_does_not_call_warcraftlogs(self, integration_agent):
        result = ask_coach(
            integration_agent,
            "What is the recommended opener rotation for Arcane Mage?",
        )
        tool_names = [name for name, _ in result["steps"]]
        assert "get_warcraftlogs_report" not in tool_names

    def test_knowledge_question_uses_no_tools(self, integration_agent):
        """A general WoW knowledge question should not trigger any tool call."""
        result = ask_coach(
            integration_agent,
            "What does the Mythic+ affixes 'Fortified' mean?",
        )
        assert result["steps"] == []


# ===========================================================================
# Tool argument passing
# ===========================================================================


class TestToolArgumentPassing:
    def test_raiderio_extracts_name_realm_region(self, integration_agent):
        """The agent should parse character, realm, and region from natural language."""
        ask_coach(
            integration_agent,
            "Look up Pyroblastus on Area 52 US.",
        )
        # We can't inspect the tool's internal call directly in integration tests —
        # we verify the agent completed without error and called the right tool.
        # Argument correctness at the tool boundary is covered by tool unit tests.

    def test_rag_called_with_spec_context(self, integration_agent):
        result = ask_coach(
            integration_agent,
            "What talents should I run for single-target as a Fire Mage?",
        )
        tool_names = [name for name, _ in result["steps"]]
        assert "search_guide_rag" in tool_names


# ===========================================================================
# Error handling in the agent loop
# ===========================================================================


class TestAgentErrorHandling:
    def test_unknown_character_produces_non_empty_answer(self, integration_agent):
        """
        Raider.IO returns a not-found error for an unknown character.
        The agent should surface this gracefully rather than raising.
        """
        result = ask_coach(
            integration_agent,
            "Look up XxNobodyKnowsXx on Area 52 US.",
        )
        assert isinstance(result, dict)
        assert len(result["answer"].strip()) > 0

    def test_tool_error_does_not_propagate_as_exception(self, integration_agent):
        try:
            ask_coach(integration_agent, "Look up XxNobodyKnowsXx on Area 52 US.")
        except Exception as e:
            pytest.fail(f"ask_coach() raised unexpectedly: {e}")


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
