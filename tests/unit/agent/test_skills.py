"""
tests/unit/agent/test_skills.py

Unit tests for agent/skills.py.

Coverage: skill_loader_node, build_skill_registry, _load_skill.
SkillDefinition dataclass fields are implicitly tested through _load_skill.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from khadbot.agent.skills import (
    _load_skill,
    build_skill_registry,
    skill_loader_node,
)


class TestSkillLoaderNode:
    def test_known_skill_loaded_into_active_skills(self, make_state, make_skill):
        skill = make_skill("personal_coaching")
        registry = {"personal_coaching": skill}
        state = make_state(needed_skills=["personal_coaching"])

        with patch("khadbot.agent.skills.SKILL_REGISTRY", registry):
            result = skill_loader_node(state)

        assert "personal_coaching" in result["active_skills"]
        assert result["active_skills"]["personal_coaching"] is skill

    def test_unknown_skill_name_dropped(self, make_state, make_skill):
        registry = {"personal_coaching": make_skill("personal_coaching")}
        state = make_state(needed_skills=["personal_coaching", "ghost_skill"])

        with patch("khadbot.agent.skills.SKILL_REGISTRY", registry):
            result = skill_loader_node(state)

        assert "personal_coaching" in result["active_skills"]
        assert "ghost_skill" not in result["active_skills"]

    def test_empty_needed_skills_returns_empty_active(self, make_state):
        state = make_state(needed_skills=[])
        with patch("khadbot.agent.skills.SKILL_REGISTRY", {}):
            result = skill_loader_node(state)
        assert result["active_skills"] == {}

    def test_all_unknown_returns_empty_active(self, make_state):
        state = make_state(needed_skills=["ghost1", "ghost2"])
        with patch("khadbot.agent.skills.SKILL_REGISTRY", {}):
            result = skill_loader_node(state)
        assert result["active_skills"] == {}

    def test_multiple_known_skills_all_loaded(self, make_state, make_skill):
        registry = {
            "personal_coaching": make_skill("personal_coaching"),
            "encounter_review": make_skill("encounter_review"),
        }
        state = make_state(needed_skills=["personal_coaching", "encounter_review"])
        with patch("khadbot.agent.skills.SKILL_REGISTRY", registry):
            result = skill_loader_node(state)
        assert len(result["active_skills"]) == 2

    def test_result_identity_preserved(self, make_state, make_skill):
        """Active skills dict must hold the same object, not a copy."""
        skill = make_skill("build_review")
        registry = {"build_review": skill}
        state = make_state(needed_skills=["build_review"])
        with patch("khadbot.agent.skills.SKILL_REGISTRY", registry):
            result = skill_loader_node(state)
        assert result["active_skills"]["build_review"] is skill


class TestLoadSkill:
    def _write_skill_yaml(self, path: Path, **overrides) -> None:
        data = {
            "name": overrides.get("name", "test_skill"),
            "display_name": overrides.get("display_name", "Test Skill"),
            "description": overrides.get("description", "A test skill."),
            "routing_description": overrides.get("routing_description", "Use for testing."),
            "tools": overrides.get("tools", []),
            "requires_character_context": overrides.get("requires_character_context", False),
            "fetch_raiderio": overrides.get("fetch_raiderio", False),
            "requires_confirmation": overrides.get("requires_confirmation", False),
        }
        path.write_text(yaml.dump(data), encoding="utf-8")

    def test_happy_path_skill_loaded(self, tmp_path):
        yaml_path = tmp_path / "test_skill.yaml"
        self._write_skill_yaml(yaml_path)
        fake_subgraph = MagicMock()
        subgraph_map = {"test_skill": fake_subgraph}

        result = _load_skill(yaml_path, subgraph_map, tool_map={})

        assert result is not None
        assert result.name == "test_skill"
        assert result.subgraph is fake_subgraph
        assert result.requires_character_context is False
        assert result.fetch_raiderio is False

    def test_missing_subgraph_returns_none(self, tmp_path):
        yaml_path = tmp_path / "test_skill.yaml"
        self._write_skill_yaml(yaml_path)

        result = _load_skill(yaml_path, subgraph_map={}, tool_map={})

        assert result is None

    def test_unknown_tool_name_skipped(self, tmp_path):
        yaml_path = tmp_path / "test_skill.yaml"
        self._write_skill_yaml(yaml_path, tools=["known_tool", "unknown_tool"])
        fake_tool = MagicMock()
        fake_tool.name = "known_tool"
        fake_subgraph = MagicMock()

        result = _load_skill(
            yaml_path,
            subgraph_map={"test_skill": fake_subgraph},
            tool_map={"known_tool": fake_tool},
        )

        assert result is not None
        assert len(result.tools) == 1
        assert result.tools[0] is fake_tool

    def test_behavioural_flags_loaded(self, tmp_path):
        yaml_path = tmp_path / "test_skill.yaml"
        self._write_skill_yaml(
            yaml_path,
            requires_character_context=True,
            fetch_raiderio=True,
            requires_confirmation=True,
        )
        result = _load_skill(
            yaml_path,
            subgraph_map={"test_skill": MagicMock()},
            tool_map={},
        )
        assert result.requires_character_context is True
        assert result.fetch_raiderio is True
        assert result.requires_confirmation is True

    def test_malformed_yaml_returns_none(self, tmp_path):
        yaml_path = tmp_path / "test_skill.yaml"
        yaml_path.write_text(":: invalid yaml ::", encoding="utf-8")
        result = _load_skill(yaml_path, subgraph_map={}, tool_map={})
        assert result is None


class TestBuildSkillRegistry:
    def test_missing_skills_dir_returns_empty(self, tmp_path):
        with patch("khadbot.agent.skills._skills_dir", return_value=tmp_path / "nonexistent"):
            result = build_skill_registry()
        assert result == {}

    def test_empty_skills_dir_returns_empty(self, tmp_path):
        (tmp_path / "skills").mkdir()
        with patch("khadbot.agent.skills._skills_dir", return_value=tmp_path / "skills"):
            result = build_skill_registry()
        assert result == {}

    def test_skill_without_subgraph_skipped(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        yaml_path = skills_dir / "orphan.yaml"
        yaml_path.write_text(
            yaml.dump(
                {
                    "name": "orphan",
                    "description": "No subgraph registered.",
                    "tools": [],
                }
            ),
            encoding="utf-8",
        )

        with (
            patch("khadbot.agent.skills._skills_dir", return_value=skills_dir),
            patch("khadbot.agent.subgraphs.SKILL_SUBGRAPH_MAP", {}),
        ):
            result = build_skill_registry()

        assert "orphan" not in result
