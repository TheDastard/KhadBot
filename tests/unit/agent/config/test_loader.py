"""
tests/unit/agent/config/test_loader.py

Unit tests for agent/config/loader.py.

Coverage: AgentConfig, PersonaConfig, load_agent_config, _validate_tool_names.
Fixtures config_root and agent_cfg come from tests/conftest.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import yaml

from khadbot.agent.config.loader import (
    ConfigurationError,
    load_agent_config,
)


class TestLoadAgentConfig:
    def test_happy_path_loads_all_fields(self, config_root):
        cfg = load_agent_config("coach", config_root=config_root, tools=None)
        assert cfg.name == "TestBot"
        assert cfg.version == "0.1"
        assert "test WoW coach" in cfg.base_prompt
        assert len(cfg.tools) > 0
        assert len(cfg.personas) == 3

    def test_agent_config_is_frozen(self, agent_config):
        with pytest.raises(ValueError):
            agent_config.name = "mutated"  # type: ignore

    def test_persona_config_is_frozen(self, agent_config):
        p = agent_config.personas[0]
        with pytest.raises(ValueError):
            p.id = "mutated"  # type: ignore

    def test_get_persona_config_valid_id(self, agent_config):
        p = agent_config.get_persona_config("thrall")
        assert p is not None
        assert p.id == "thrall"
        assert p.intro_message

    def test_get_persona_config_unknown_id_returns_none(self, agent_config):
        assert agent_config.get_persona_config("gandalf") is None

    def test_get_persona_config_none_returns_none(self, agent_config):
        assert agent_config.get_persona_config(None) is None

    def test_list_persona_ids(self, agent_config):
        ids = agent_config.list_persona_ids()
        assert ids == ["khadgar", "thrall", "xalatath"]

    def test_personas_loaded_in_declaration_order(self, agent_config):
        assert [p.id for p in agent_config.personas] == ["khadgar", "thrall", "xalatath"]

    def test_missing_agent_file_raises_file_not_found(self, tmp_path, write_persona_yaml):
        write_persona_yaml("thrall")
        with pytest.raises(FileNotFoundError):
            load_agent_config("nonexistent", config_root=tmp_path, tools=None)

    def test_missing_agent_block_raises_config_error(self, tmp_path):
        path = tmp_path / "agents" / "coach.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump({"tools": [], "personas": []}), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_agent_config("coach", config_root=tmp_path, tools=None)

    def test_empty_base_prompt_raises_config_error(
        self, tmp_path, write_agent_yaml, write_persona_yaml, write_template
    ):
        write_agent_yaml(base_prompt="   ")
        write_persona_yaml("thrall")
        write_template()
        with pytest.raises(ConfigurationError):
            load_agent_config("coach", config_root=tmp_path, tools=None)

    def test_missing_persona_file_raises(self, tmp_path, write_agent_yaml):
        write_agent_yaml(personas=["thrall"])
        # Deliberately omit persona file
        with pytest.raises((FileNotFoundError, ConfigurationError)):
            load_agent_config("coach", config_root=tmp_path, tools=None)

    def test_persona_id_mismatch_raises_config_error(self, tmp_path, write_agent_yaml):
        write_agent_yaml(personas=["thrall"])
        path = tmp_path / "personas" / "thrall.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(
                {
                    "id": "khadgar",
                    "display_name": "X",
                    "intro_message": "X",
                    "voice_prompt": "X",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="mismatch|expected"):
            load_agent_config("coach", config_root=tmp_path, tools=None)

    def test_duplicate_tool_names_raises_config_error(self, tmp_path, write_agent_yaml, write_persona_yaml):
        write_agent_yaml(tools=["run_simc", "run_simc"])
        write_persona_yaml("thrall")
        with pytest.raises(ConfigurationError):
            load_agent_config("coach", config_root=tmp_path, tools=None)

    def test_duplicate_persona_ids_raises_config_error(self, tmp_path, write_agent_yaml, write_persona_yaml):
        write_agent_yaml(personas=["thrall", "thrall"])
        write_persona_yaml("thrall")
        with pytest.raises(ConfigurationError):
            load_agent_config("coach", config_root=tmp_path, tools=None)

    def test_tools_none_skips_cross_reference(self, config_root):
        """tools=None must not raise even though no Python tools are provided."""
        cfg = load_agent_config("coach", config_root=config_root, tools=None)
        assert cfg is not None

    def test_orphaned_declared_tool_raises_config_error(self, config_root):
        fake = MagicMock()
        fake.name = "totally_unknown_tool"
        with pytest.raises(ConfigurationError, match="no Python implementation"):
            load_agent_config("coach", config_root=config_root, tools=[fake])

    def test_undeclared_python_tool_does_not_raise(self, config_root):
        """Python tools not in the YAML declaration → debug log only, no error."""
        from khadbot.agent.config.loader import _validate_tool_names

        # All declared names present, plus one extra Python tool
        declared = ["find_character_reports"]
        extra = MagicMock()
        extra.name = "undeclared_extra"
        matching = MagicMock()
        matching.name = "find_character_reports"
        _validate_tool_names(declared, [matching, extra])  # must not raise
