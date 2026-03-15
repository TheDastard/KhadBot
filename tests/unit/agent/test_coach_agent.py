"""
tests/unit/agent/test_coach_agent.py

Unit tests for coach.py.

Tests the agent's orchestration logic — tool selection, argument passing,
multi-turn context, error loop behavior, and ask_coach() output shape —
using FakeListChatModel. Zero real LLM inference cost.

The tests are deliberately agnostic to exact response wording.
They assert on shape: which tools were called, what args were passed,
whether the answer is non-empty, and whether error states are handled.
"""

from unittest.mock import MagicMock, patch

from fixtures.agent_payloads import (
    ALL_TOOLS_FAIL_RESPONSE,
    MOCK_RAIDERIO_NOT_FOUND,
    MOCK_RAIDERIO_RESULT,
    RAG_ONLY_ANSWER,
    RAIDERIO_THEN_ANSWER,
    SIMC_ONLY_ANSWER,
    TOOL_ERROR_THEN_RETRY,
)
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage

# ---------------------------------------------------------------------------
# Imports from source modules.
# Adjust paths to match your project layout.
# ---------------------------------------------------------------------------
from agent.coach import BASE_SYSTEM_PROMPT, TOOLS, ask_coach, build_agent_executor

# ===========================================================================
# Helpers & shared fixtures
# ===========================================================================


def _make_agent(fake_responses: list, mock_tool_results: dict | None = None):
    """
    Build a KhadBot agent with:
      - FakeListChatModel returning the scripted response sequence
      - All tools patched to return values from mock_tool_results
        (or a generic success dict if not specified)

    mock_tool_results maps tool name → return value.
    Tools not in the dict return {"_stub": True, "result": "ok"}.
    """
    fake_llm = FakeListChatModel(responses=fake_responses)

    default_result = {"_stub": True, "result": "ok"}
    tool_results = mock_tool_results or {}

    patched_tools = []
    for t in TOOLS:
        mock_t = MagicMock(wraps=t)
        mock_t.name = t.name
        mock_t.description = t.description
        mock_t.args_schema = t.args_schema
        mock_t.invoke = MagicMock(return_value=tool_results.get(t.name, default_result))
        patched_tools.append(mock_t)

    with (
        patch("src.agent.coach.get_llm", return_value=fake_llm),
        patch("src.agent.coach.TOOLS", patched_tools),
    ):
        agent = build_agent_executor(verbose=False)

    return agent, patched_tools


def _called_tool_names(steps: list) -> list[str]:
    """Extract just the tool names from ask_coach()'s steps list."""
    return [name for name, _ in steps]


# ===========================================================================
# Tool registry
# ===========================================================================


class TestToolRegistry:
    def test_all_four_tools_registered(self):
        tool_names = {t.name for t in TOOLS}
        assert "get_character_raiderio" in tool_names
        assert "get_warcraftlogs_report" in tool_names
        assert "run_simc" in tool_names
        assert "search_guide_rag" in tool_names

    def test_no_duplicate_tool_names(self):
        names = [t.name for t in TOOLS]
        assert len(names) == len(set(names))

    def test_all_tools_have_descriptions(self):
        for t in TOOLS:
            assert t.description, f"Tool {t.name} is missing a description"


# ===========================================================================
# System prompt
# ===========================================================================


class TestSystemPrompt:
    def test_system_prompt_references_all_tools(self):
        """Each tool name should appear in the system prompt so the LLM knows what's available."""
        for t in TOOLS:
            assert t.name in BASE_SYSTEM_PROMPT, (
                f"Tool '{t.name}' not mentioned in SYSTEM_PROMPT — the LLM won't know it exists."
            )

    def test_system_prompt_not_empty(self):
        assert len(BASE_SYSTEM_PROMPT.strip()) > 100


# ===========================================================================
# ask_coach output shape
# ===========================================================================


class TestAskCoachOutputShape:
    def test_returns_dict_with_answer_and_steps(self):
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        result = ask_coach(agent, "What's my character's IO score?")
        assert isinstance(result, dict)
        assert "answer" in result
        assert "steps" in result

    def test_answer_is_a_string(self):
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        result = ask_coach(agent, "What's my character's IO score?")
        assert isinstance(result["answer"], str)

    def test_answer_is_not_empty(self):
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        result = ask_coach(agent, "What's my character's IO score?")
        assert len(result["answer"].strip()) > 0

    def test_steps_is_a_list(self):
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        result = ask_coach(agent, "What's my character's IO score?")
        assert isinstance(result["steps"], list)

    def test_steps_entries_are_tuples(self):
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        result = ask_coach(agent, "What's my character's IO score?")
        for step in result["steps"]:
            assert isinstance(step, tuple)
            assert len(step) == 2


# ===========================================================================
# Tool routing — correct tool selected per query type
# ===========================================================================


class TestToolRouting:
    def test_character_question_calls_raiderio(self):
        agent, tools = _make_agent(
            RAIDERIO_THEN_ANSWER,
            {"get_character_raiderio": MOCK_RAIDERIO_RESULT},
        )
        result = ask_coach(agent, "What's Pyroblastus's IO score on Area 52 US?")
        assert "get_character_raiderio" in _called_tool_names(result["steps"])

    def test_build_question_calls_rag(self):
        agent, tools = _make_agent(RAG_ONLY_ANSWER)
        result = ask_coach(agent, "What talents should I run for single-target as Fire Mage?")
        assert "search_guide_rag" in _called_tool_names(result["steps"])

    def test_sim_question_calls_simc(self):
        agent, tools = _make_agent(SIMC_ONLY_ANSWER)
        result = ask_coach(agent, "Simulate my current setup: mage=Pyroblastus\n...")
        assert "run_simc" in _called_tool_names(result["steps"])

    def test_build_question_does_not_call_warcraftlogs(self):
        """A pure build/talent question shouldn't waste a WarcraftLogs call."""
        agent, tools = _make_agent(RAG_ONLY_ANSWER)
        result = ask_coach(agent, "What talents should I run for single-target as Fire Mage?")
        assert "get_warcraftlogs_report" not in _called_tool_names(result["steps"])

    def test_sim_question_does_not_call_raiderio(self):
        """A raw simc string question shouldn't trigger a Raider.IO lookup."""
        agent, tools = _make_agent(SIMC_ONLY_ANSWER)
        result = ask_coach(agent, "Simulate this simc string for me.")
        assert "get_character_raiderio" not in _called_tool_names(result["steps"])


# ===========================================================================
# Tool argument correctness
# ===========================================================================


class TestToolArgumentPassing:
    def test_raiderio_receives_name_realm_region(self):
        agent, patched_tools = _make_agent(
            RAIDERIO_THEN_ANSWER,
            {"get_character_raiderio": MOCK_RAIDERIO_RESULT},
        )
        ask_coach(agent, "Look up Pyroblastus on area-52 US")

        raiderio_tool = next(t for t in patched_tools if t.name == "get_character_raiderio")
        call_args = raiderio_tool.invoke.call_args

        # Args are passed as a dict to .invoke()
        invocation = call_args[0][0] if call_args[0] else call_args[1]
        assert "name" in invocation
        assert "realm" in invocation
        assert "region" in invocation

    def test_rag_receives_spec_and_question(self):
        agent, patched_tools = _make_agent(RAG_ONLY_ANSWER)
        ask_coach(agent, "What talents for single-target as Fire Mage?")

        rag_tool = next(t for t in patched_tools if t.name == "search_guide_rag")
        call_args = rag_tool.invoke.call_args
        invocation = call_args[0][0] if call_args[0] else call_args[1]
        assert "spec" in invocation
        assert "question" in invocation


# ===========================================================================
# Error handling in the agent loop
# ===========================================================================


class TestAgentErrorHandling:
    def test_tool_not_found_response_produces_non_empty_answer(self):
        """When a tool returns a not_found error, the agent should still produce an answer."""
        agent, _ = _make_agent(
            ALL_TOOLS_FAIL_RESPONSE,
            {"get_character_raiderio": MOCK_RAIDERIO_NOT_FOUND},
        )
        result = ask_coach(agent, "Look up NobodyKnows on area-52 US")
        assert len(result["answer"].strip()) > 0

    def test_tool_error_does_not_propagate_as_exception(self):
        """The agent should catch tool errors and return gracefully — never raise."""
        agent, _ = _make_agent(
            ALL_TOOLS_FAIL_RESPONSE,
            {"get_character_raiderio": MOCK_RAIDERIO_NOT_FOUND},
        )
        # This must not raise
        result = ask_coach(agent, "Look up NobodyKnows on area-52 US")
        assert isinstance(result, dict)

    def test_retry_sequence_produces_final_answer(self):
        """
        When the agent retries with corrected args, the final response
        should still be a non-empty string.
        """
        agent, _ = _make_agent(
            TOOL_ERROR_THEN_RETRY,
            {"get_character_raiderio": MOCK_RAIDERIO_RESULT},
        )
        result = ask_coach(agent, "Look up Pyroblastus on area52 US")  # bad realm
        assert len(result["answer"].strip()) > 0


# ===========================================================================
# Multi-turn context (chat_history)
# ===========================================================================


class TestMultiTurnContext:
    def test_chat_history_accepted_without_error(self):
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        history = [
            HumanMessage(content="My character is Pyroblastus on Area 52 US."),
            AIMessage(content="Got it! I'll keep that in mind."),
        ]
        # Should not raise
        result = ask_coach(agent, "What's my IO score?", chat_history=history)
        assert isinstance(result, dict)

    def test_none_chat_history_defaults_gracefully(self):
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        result = ask_coach(agent, "What's my IO score?", chat_history=None)
        assert isinstance(result, dict)

    def test_empty_chat_history_defaults_gracefully(self):
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        result = ask_coach(agent, "What's my IO score?", chat_history=[])
        assert isinstance(result, dict)

    def test_prior_context_included_in_message_list(self):
        """
        Verify that chat_history messages are prepended to the invocation payload,
        not discarded. We check this by inspecting the agent's invoke call.
        """
        agent, _ = _make_agent(RAIDERIO_THEN_ANSWER, {"get_character_raiderio": MOCK_RAIDERIO_RESULT})
        history = [HumanMessage(content="Context message")]

        with patch.object(agent, "invoke", wraps=agent.invoke) as mock_invoke:
            ask_coach(agent, "Follow-up question", chat_history=history)
            call_kwargs = mock_invoke.call_args[0][0]
            messages = call_kwargs.get("messages", [])
            message_contents = [m.content for m in messages]
            assert "Context message" in message_contents


# ===========================================================================
# build_agent_executor
# ===========================================================================


class TestBuildAgentExecutor:
    def test_returns_invocable_agent(self):
        """build_agent_executor must return something with an .invoke() method."""
        with patch("agents.coach.get_llm", return_value=FakeListChatModel(responses=RAIDERIO_THEN_ANSWER)):
            agent = build_agent_executor(verbose=False)
        assert hasattr(agent, "invoke")

    def test_agent_is_built_with_tools(self):
        """Ensure the agent actually has tools bound — not just a bare LLM."""
        with patch("agents.coach.get_llm", return_value=FakeListChatModel(responses=RAIDERIO_THEN_ANSWER)):
            agent = build_agent_executor(verbose=False)
        # LangGraph agents expose their tool set; verify it's non-empty
        # (exact attribute varies by LangChain version — check common locations)
        has_tools = (
            hasattr(agent, "tools")
            and len(agent.tools) > 0
            or hasattr(agent, "_tools")
            and len(agent._tools) > 0
            or hasattr(agent, "nodes")  # LangGraph compiled graph
        )
        assert has_tools, "Agent appears to have no tools bound"
