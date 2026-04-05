"""
tests/unit/agent/config/test_assembler.py

Unit tests for agent/config/assembler.py.

Coverage: PromptAssembler.render() — base prompt rendering, persona injection,
guardrail placement, caching, missing template handling.

Absorbs TestSystemPromptAssembly from the retired test_coach_agent.py.
Those tests used agent_cfg + assembler fixtures from conftest.py and verified
the assembler in the context of the coach config — that same coverage lives
here now that coach.py is gone.

Fixtures agent_cfg and assembler come from tests/conftest.py.
"""

from __future__ import annotations

import pytest

from khadbot.agent.config.assembler import PromptAssembler
from khadbot.agent.config.loader import AgentConfig, PersonaConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(base_prompt: str = "You are KhadBot.") -> AgentConfig:
    return AgentConfig(
        name="test",
        version="0.1",
        base_prompt=base_prompt,
        tools=[],
        personas=[],
    )


def _persona(id_: str = "thrall", voice: str = "Speak as Thrall.") -> PersonaConfig:
    return PersonaConfig(
        id=id_,
        display_name=id_.capitalize(),
        intro_message=f"Intro from {id_}.",
        voice_prompt=voice,
    )


# ---------------------------------------------------------------------------
# Core rendering — PromptAssembler unit tests
# (assembler fixture uses the test template from conftest config_root)
# ---------------------------------------------------------------------------


class TestPromptAssembler:
    def test_no_persona_contains_base_prompt(self, assembler):
        result = assembler.render(_config("You are KhadBot."), persona=None)
        assert "You are KhadBot." in result

    def test_no_persona_excludes_voice_prompt(self, assembler):
        result = assembler.render(_config(), persona=None)
        assert "Speak as" not in result

    def test_no_persona_excludes_guardrail(self, assembler):
        result = assembler.render(_config(), persona=None)
        assert "PERSONA SCOPE" not in result.upper()

    def test_with_persona_contains_base_prompt(self, assembler):
        result = assembler.render(_config("You are KhadBot."), persona=_persona())
        assert "You are KhadBot." in result

    def test_with_persona_includes_voice_prompt(self, assembler):
        result = assembler.render(_config(), persona=_persona(voice="Speak as Thrall."))
        assert "Speak as Thrall." in result

    def test_with_persona_includes_guardrail(self, assembler):
        result = assembler.render(_config(), persona=_persona())
        assert "PERSONA SCOPE" in result.upper()

    def test_guardrail_precedes_voice_prompt(self, assembler):
        persona = _persona(voice="Speak as Thrall.")
        result = assembler.render(_config(), persona=persona)
        guardrail_pos = result.upper().index("PERSONA SCOPE")
        voice_pos = result.index("Speak as Thrall.")
        assert guardrail_pos < voice_pos

    def test_missing_template_raises_file_not_found(self, tmp_path):
        bad_assembler = PromptAssembler(tmp_path / "nonexistent.jinja2")
        with pytest.raises(FileNotFoundError):
            bad_assembler.render(_config(), persona=None)

    def test_render_is_idempotent(self, assembler):
        cfg = _config()
        r1 = assembler.render(cfg, persona=None)
        r2 = assembler.render(cfg, persona=None)
        assert r1 == r2

    def test_template_cached_after_first_render(self, assembler):
        """Verify cached_property behaviour — second access returns same object."""
        cfg = _config()
        assembler.render(cfg, persona=None)
        t1 = assembler._template
        t2 = assembler._template
        assert t1 is t2


# ---------------------------------------------------------------------------
# Coach-context assembler tests
# (formerly TestSystemPromptAssembly in test_coach_agent.py)
# Uses agent_cfg + assembler fixtures built from the full config_root.
# ---------------------------------------------------------------------------


class TestAssemblerWithCoachConfig:
    """
    Verifies assembler behaviour using the full coach AgentConfig fixture —
    all three personas declared, real base_prompt text.
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

    def test_persona_voice_prompt_present(self, agent_config, assembler):
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
            for pid in ["thrall", "khadgar", "xalatath"]
        ]
        assert len(set(prompts)) == 3

    def test_base_prompt_appears_before_voice_prompt(self, agent_config, assembler):
        persona = agent_config.get_persona_config("thrall")
        prompt = assembler.render(agent_config, persona=persona)
        base_pos = prompt.index(agent_config.base_prompt.strip())
        voice_pos = prompt.index(persona.voice_prompt.strip())
        assert base_pos < voice_pos
