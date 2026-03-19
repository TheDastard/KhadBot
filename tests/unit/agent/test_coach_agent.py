"""
tests/unit/agent/test_coach_agent.py

Unit tests for coach.py.

Scope: what coach.py is directly responsible for —
  - The real TOOLS list (registry completeness, no duplicates, descriptions present)
  - resolve_tools() (filtering, ordering, unknown name handling)
  - System prompt assembly via PromptAssembler (base content, guardrail placement)
  - build_agent_executor() wiring (correct tools, prompt, and LLM passed to
    create_agent; persona selection from arg vs env fallback)

Not in scope here:
  - Tool routing, argument passing, error handling, multi-turn context —
    these require a running agent loop and belong in integration tests.
  - Tool implementations — each tool has its own unit test file.

No LLM inference. No network calls. No agent invocation.
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from khadbot.agent.coach import build_agent_executor, resolve_tools
from khadbot.tools import TOOLS

# config_root, agent_config, assembler fixtures come from tests/fixtures/conftest.py


# ===========================================================================
# Tool registry
# ===========================================================================


class TestToolRegistry:
    """The global TOOLS list is the source of truth for available tools."""

    def test_all_tools_registered(self):
        tool_names = {t.name for t in TOOLS}
        assert "get_character_raiderio" in tool_names
        assert "get_warcraftlogs_report" in tool_names
        assert "get_wipefest_insights" in tool_names
        assert "run_simc" in tool_names
        assert "search_guide_rag" in tool_names

    def test_no_duplicate_tool_names(self):
        names = [t.name for t in TOOLS]
        assert len(names) == len(set(names))

    def test_all_tools_have_descriptions(self):
        for t in TOOLS:
            assert t.description, f"Tool '{t.name}' is missing a description."


# ===========================================================================
# resolve_tools()
# ===========================================================================


class TestResolveTools:
    """resolve_tools() filters the global TOOLS list to the declared subset."""

    def test_filters_to_declared_subset(self):
        result = resolve_tools(["get_character_raiderio", "run_simc"], TOOLS)
        assert {t.name for t in result} == {"get_character_raiderio", "run_simc"}

    def test_excludes_undeclared_tools(self):
        result = resolve_tools(["run_simc"], TOOLS)
        names = {t.name for t in result}
        assert "get_character_raiderio" not in names
        assert "search_guide_rag" not in names

    def test_preserves_declaration_order(self):
        result = resolve_tools(["run_simc", "get_character_raiderio"], TOOLS)
        assert [t.name for t in result] == ["run_simc", "get_character_raiderio"]

    def test_unknown_name_warns_and_skips(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            result = resolve_tools(["get_character_raiderio", "nonexistent_tool"], TOOLS)
        assert len(result) == 1
        assert result[0].name == "get_character_raiderio"
        assert "nonexistent_tool" in caplog.text

    def test_empty_declared_list_returns_empty(self):
        assert resolve_tools([], TOOLS) == []

    def test_all_tools_declared_returns_all(self):
        all_names = [t.name for t in TOOLS]
        result = resolve_tools(all_names, TOOLS)
        assert len(result) == len(TOOLS)


# ===========================================================================
# System prompt assembly
# ===========================================================================


class TestSystemPromptAssembly:
    """
    PromptAssembler.render() is tested here in coach context — verifying that
    the base prompt content and persona injection behave correctly for the
    fixture config. Structural template tests live in test_agent_config.py.
    """

    def test_no_persona_renders_base_prompt(self, agent_config, assembler):
        prompt = assembler.render(agent_config, persona=None)
        assert "test WoW coach" in prompt

    def test_no_persona_excludes_guardrail(self, agent_config, assembler):
        prompt = assembler.render(agent_config, persona=None)
        assert "PERSONA SCOPE" not in prompt.upper()

    def test_persona_injects_guardrail(self, agent_config, assembler):
        persona = agent_config.get_persona_config("khadgar")
        prompt = assembler.render(agent_config, persona=persona)
        assert "PERSONA SCOPE" in prompt.upper()

    def test_persona_voice_prompt_appended(self, agent_config, assembler):
        persona = agent_config.get_persona_config("khadgar")
        prompt = assembler.render(agent_config, persona=persona)
        assert persona.voice_prompt.strip() in prompt

    def test_guardrail_before_voice_prompt(self, agent_config, assembler):
        persona = agent_config.get_persona_config("khadgar")
        prompt = assembler.render(agent_config, persona=persona)
        assert prompt.upper().index("PERSONA SCOPE") < prompt.index(persona.voice_prompt.strip())

    def test_each_persona_produces_distinct_prompt(self, agent_config, assembler):
        prompts = [
            assembler.render(agent_config, persona=agent_config.get_persona_config(pid))
            for pid in ["khadgar", "thrall", "xalatath"]
        ]
        assert len(prompts) == len(set(prompts))


# ===========================================================================
# build_agent_executor() wiring
# ===========================================================================


class TestBuildAgentExecutor:
    """
    Verifies that build_agent_executor() assembles the agent correctly.
    create_agent() is captured via side_effect so we can assert on what it
    receives without actually building a LangGraph graph.
    """

    @pytest.fixture(autouse=True)
    def _patch_llm(self, monkeypatch):
        """Provide a no-op LLM for all tests in this class."""
        self.fake_llm = GenericFakeChatModel(messages=iter([AIMessage(content="Hello")]))
        monkeypatch.setattr("khadbot.llm_factory.get_llm", lambda: self.fake_llm)

    def _capture_create_agent(self) -> tuple[dict, MagicMock]:
        """
        Return (captured, mock_agent) where captured is populated with the
        kwargs passed to create_agent() when it is called.
        """
        captured = {}
        mock_agent = MagicMock()

        def _side_effect(model, tools, system_prompt):
            captured["model"] = model
            captured["tools"] = tools
            captured["system_prompt"] = system_prompt
            return mock_agent

        return captured, mock_agent, _side_effect

    def test_correct_llm_passed(self, agent_config, assembler):
        captured, _, side_effect = self._capture_create_agent()
        with patch("khadbot.agent.coach.create_agent", side_effect=side_effect):
            build_agent_executor(config=agent_config, assembler=assembler)
        assert captured["model"] is self.fake_llm

    def test_declared_tool_subset_passed(self, agent_config, assembler):
        captured, _, side_effect = self._capture_create_agent()
        with patch("khadbot.agent.coach.create_agent", side_effect=side_effect):
            build_agent_executor(config=agent_config, assembler=assembler)
        passed_names = {t.name for t in captured["tools"]}
        assert passed_names == set(agent_config.tools)

    def test_full_tools_list_not_passed(self, agent_config, assembler):
        """The agent should receive only its declared subset, not all of TOOLS."""
        captured, _, side_effect = self._capture_create_agent()
        # Add a tool to TOOLS that is NOT in the agent YAML
        extra_tool = MagicMock()
        extra_tool.name = "undeclared_extra_tool"
        with (
            patch("khadbot.agent.coach.create_agent", side_effect=side_effect),
            patch("khadbot.agent.coach.TOOLS", [*TOOLS, extra_tool]),
        ):
            build_agent_executor(config=agent_config, assembler=assembler)
        passed_names = {t.name for t in captured["tools"]}
        assert "undeclared_extra_tool" not in passed_names

    def test_no_persona_prompt_excludes_guardrail(self, agent_config, assembler):
        captured, _, side_effect = self._capture_create_agent()
        with patch("khadbot.agent.coach.create_agent", side_effect=side_effect):
            build_agent_executor(persona=None, config=agent_config, assembler=assembler)
        assert "PERSONA SCOPE" not in captured["system_prompt"].upper()

    def test_persona_prompt_includes_guardrail(self, agent_config, assembler):
        captured, _, side_effect = self._capture_create_agent()
        persona = agent_config.get_persona_config("khadgar")
        with patch("khadbot.agent.coach.create_agent", side_effect=side_effect):
            build_agent_executor(persona=persona, config=agent_config, assembler=assembler)
        assert "PERSONA SCOPE" in captured["system_prompt"].upper()

    def test_persona_voice_prompt_in_system_prompt(self, agent_config, assembler):
        captured, _, side_effect = self._capture_create_agent()
        persona = agent_config.get_persona_config("thrall")
        with patch("khadbot.agent.coach.create_agent", side_effect=side_effect):
            build_agent_executor(persona=persona, config=agent_config, assembler=assembler)
        assert persona.voice_prompt.strip() in captured["system_prompt"]

    def test_base_prompt_before_voice_prompt(self, agent_config, assembler):
        captured, _, side_effect = self._capture_create_agent()
        persona = agent_config.get_persona_config("khadgar")
        with patch("khadbot.agent.coach.create_agent", side_effect=side_effect):
            build_agent_executor(persona=persona, config=agent_config, assembler=assembler)
        prompt = captured["system_prompt"]
        assert prompt.index(agent_config.base_prompt.strip()) < prompt.index(persona.voice_prompt.strip())

    def test_returns_agent_from_create_agent(self, agent_config, assembler):
        _, mock_agent, side_effect = self._capture_create_agent()
        with patch("khadbot.agent.coach.create_agent", side_effect=side_effect):
            result = build_agent_executor(config=agent_config, assembler=assembler)
        assert result is mock_agent

    def test_different_personas_produce_different_prompts(self, agent_config, assembler):
        prompts = []

        def _side_effect(model, tools, system_prompt):
            prompts.append(system_prompt)
            return MagicMock()

        thrall = agent_config.get_persona_config("thrall")
        khadgar = agent_config.get_persona_config("khadgar")

        with patch("khadbot.agent.coach.create_agent", side_effect=_side_effect):
            build_agent_executor(persona=thrall, config=agent_config, assembler=assembler)
            build_agent_executor(persona=khadgar, config=agent_config, assembler=assembler)

        assert prompts[0] != prompts[1]


# ===========================================================================
# build_agent_executor() — env var / config fallback
# ===========================================================================


class TestBuildAgentExecutorConfigFallback:
    """
    When no persona arg is passed, build_agent_executor() falls back to the
    KHADBOT_PERSONA env var via get_config(). Explicit arg always wins.
    """

    @pytest.fixture(autouse=True)
    def _patch_llm_and_create(self, monkeypatch):
        fake_llm = GenericFakeChatModel(messages=iter([AIMessage(content="Hi")]))
        monkeypatch.setattr("khadbot.llm_factory.get_llm", lambda: fake_llm)

        self.captured = {}

        def _side_effect(model, tools, system_prompt):
            self.captured["system_prompt"] = system_prompt
            return MagicMock()

        monkeypatch.setattr("khadbot.agent.coach.create_agent", _side_effect)

        import khadbot.config as config

        config.reset_config()
        yield
        config.reset_config()

    def test_no_arg_no_env_var_uses_base_prompt_only(self, monkeypatch, agent_config, assembler):
        monkeypatch.delenv("KHADBOT_PERSONA", raising=False)
        build_agent_executor(config=agent_config, assembler=assembler)
        assert "PERSONA SCOPE" not in self.captured["system_prompt"].upper()

    def test_env_var_selects_persona(self, monkeypatch, agent_config, assembler):
        import khadbot.config as config

        monkeypatch.setenv("KHADBOT_PERSONA", "thrall")
        config.reset_config()
        thrall = agent_config.get_persona_config("thrall")
        build_agent_executor(config=agent_config, assembler=assembler)
        assert thrall.voice_prompt.strip() in self.captured["system_prompt"]

    def test_unknown_env_var_falls_back_to_base_prompt(self, monkeypatch, agent_config, assembler):
        import khadbot.config as config

        monkeypatch.setenv("KHADBOT_PERSONA", "gandalf_the_grey")
        config.reset_config()
        build_agent_executor(config=agent_config, assembler=assembler)
        assert "PERSONA SCOPE" not in self.captured["system_prompt"].upper()

    def test_explicit_persona_arg_overrides_env_var(self, monkeypatch, agent_config, assembler):
        import khadbot.config as config

        monkeypatch.setenv("KHADBOT_PERSONA", "thrall")
        config.reset_config()
        xalatath = agent_config.get_persona_config("xalatath")
        build_agent_executor(persona=xalatath, config=agent_config, assembler=assembler)
        assert xalatath.voice_prompt.strip() in self.captured["system_prompt"]
