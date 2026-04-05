"""
tests/unit/agent/config/test_personas.py

Unit tests for agent/config/personas.py.

Coverage: CoachPersona, get_persona, list_personas, resolve_session_persona.
Fixtures agent_cfg comes from tests/conftest.py.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from khadbot.agent.config.loader import PersonaConfig, load_agent_config
from khadbot.agent.config.personas import (
    CoachPersona,
    get_persona,
    list_personas,
    resolve_session_persona,
)


class TestGetPersona:
    def test_returns_persona_config_for_valid_id(self, agent_config):
        p = get_persona("thrall", agent_config)
        assert isinstance(p, PersonaConfig)
        assert p.id == "thrall"

    def test_returns_persona_for_each_declared_id(self, agent_config):
        for pid in ["thrall", "khadgar", "xalatath"]:
            assert get_persona(pid, agent_config) is not None

    def test_returns_none_for_unknown_id(self, agent_config):
        assert get_persona("gandalf", agent_config) is None

    def test_returns_none_for_empty_string(self, agent_config):
        assert get_persona("", agent_config) is None

    def test_returns_none_for_none(self, agent_config):
        assert get_persona(None, agent_config) is None

    def test_id_not_in_agent_declaration_returns_none(
        self, tmp_path, write_agent_yaml, write_persona_yaml, write_template
    ):
        """A persona file that exists but is not declared in the agent YAML
        must not be reachable via get_persona."""
        write_agent_yaml(personas=["thrall"])
        write_persona_yaml("thrall")
        write_persona_yaml("khadgar")  # exists on disk but not declared
        write_template()
        cfg = load_agent_config("coach", config_root=tmp_path, tools=None)
        assert get_persona("khadgar", cfg) is None


class TestListPersonas:
    def test_returns_coach_persona_wrappers(self, agent_config):
        personas = list_personas(agent_config)
        assert all(isinstance(p, CoachPersona) for p in personas)

    def test_returns_correct_count(self, agent_config):
        assert len(list_personas(agent_config)) == 3

    def test_declaration_order_preserved(self, agent_config):
        ids = [p.id for p in list_personas(agent_config)]
        assert ids == ["khadgar", "thrall", "xalatath"]

    def test_coach_persona_fields_match_persona_config(self, agent_config):
        coach_personas = list_personas(agent_config)
        for coach, raw in zip(coach_personas, agent_config.personas, strict=False):
            assert coach.id == raw.id
            assert coach.display_name == raw.display_name
            assert coach.voice_prompt == raw.voice_prompt
            assert coach.intro_message == raw.intro_message

    def test_intro_message_populated(self, agent_config):
        for p in list_personas(agent_config):
            assert p.intro_message, f"intro_message missing for {p.id}"


class TestResolveSessionPersona:
    def test_explicit_id_takes_priority_over_env_var(self, agent_config):
        with patch.dict(os.environ, {"KHADBOT_PERSONA": "khadgar"}):
            result = resolve_session_persona(explicit_id="thrall", config=agent_config)
        assert result is not None
        assert result.id == "thrall"

    def test_falls_back_to_env_var_when_no_explicit_id(self, agent_config):
        with patch.dict(os.environ, {"KHADBOT_PERSONA": "thrall"}):
            result = resolve_session_persona(explicit_id=None, config=agent_config)
        assert result is not None
        assert result.id == "thrall"

    def test_returns_none_when_neither_set(self, agent_config):
        clean_env = {k: v for k, v in os.environ.items() if k != "KHADBOT_PERSONA"}
        with patch.dict(os.environ, clean_env, clear=True):
            result = resolve_session_persona(explicit_id=None, config=agent_config)
        assert result is None

    def test_unknown_explicit_id_returns_none(self, agent_config):
        result = resolve_session_persona(explicit_id="gandalf", config=agent_config)
        assert result is None

    def test_unknown_env_var_returns_none(self, agent_config):
        with patch.dict(os.environ, {"KHADBOT_PERSONA": "gandalf"}):
            result = resolve_session_persona(explicit_id=None, config=agent_config)
        assert result is None

    def test_empty_env_var_returns_none(self, agent_config):
        with patch.dict(os.environ, {"KHADBOT_PERSONA": ""}):
            result = resolve_session_persona(explicit_id=None, config=agent_config)
        assert result is None
