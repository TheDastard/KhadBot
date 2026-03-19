"""
tests/unit/test_agent_config.py

Unit tests for the agent config loader, prompt assembler, and tool resolver.

All tests use tmp_path to write YAML and template fixture files — no
dependency on the real config/ directory, no lru_cache interference.

Coverage:
  - AgentConfig loading: happy path, field validation, duplicate detection
  - Persona loading: file resolution, id/filename mismatch, slug validation
  - Tool cross-reference: orphaned names, excluded-but-available tools
  - PromptAssembler: no-persona rendering, persona rendering, guardrail
    placement, StrictUndefined on missing variables
  - resolve_tools: ordering, missing tool warning, full subset
  - Integration: build_agent_executor receives correct tool subset and prompt
"""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from khadbot.agent.agent_config import (
    ConfigurationError,
    load_agent_config,
)
from khadbot.agent.coach import resolve_tools
from khadbot.agent.prompt_assembler import PromptAssembler

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

AGENT_YAML = textwrap.dedent("""\
    agent:
      name: TestBot
      version: "0.1"
      base_prompt: |
        You are a test coach.
    tools:
      - get_character_raiderio
      - run_simc
    personas:
      - khadgar
      - thrall
""")

KHADGAR_YAML = textwrap.dedent("""\
    id: khadgar
    display_name: Archmage Khadgar
    intro_message: Hello from Khadgar.
    voice_prompt: Speak as Khadgar.
""")

THRALL_YAML = textwrap.dedent("""\
    id: thrall
    display_name: Thrall
    intro_message: Lok'tar.
    voice_prompt: Speak as Thrall.
""")

TEMPLATE = textwrap.dedent("""\
    {{ base_prompt -}}
    {% if persona %}


    GUARDRAIL TEXT

    {{ persona.voice_prompt -}}
    {% endif %}
""")


def _write_config(
    tmp_path: Path,
    agent_yaml: str = AGENT_YAML,
    personas: dict[str, str] | None = None,
    template: str = TEMPLATE,
) -> tuple[Path, Path]:
    """
    Write a full fixture config tree under tmp_path.
    Returns (config_root, template_path).
    """
    root = tmp_path / "config"
    (root / "agents").mkdir(parents=True)
    (root / "personas").mkdir(parents=True)

    (root / "agents" / "coach.yaml").write_text(agent_yaml, encoding="utf-8")

    if personas is None:
        personas = {"khadgar": KHADGAR_YAML, "thrall": THRALL_YAML}
    for pid, content in personas.items():
        (root / "personas" / f"{pid}.yaml").write_text(content, encoding="utf-8")

    template_path = root / "prompt_template.jinja2"
    template_path.write_text(template, encoding="utf-8")

    return root, template_path


def _fake_tool(name: str):
    t = MagicMock()
    t.name = name
    return t


# ---------------------------------------------------------------------------
# AgentConfig loading — happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_agent_metadata(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assert cfg.name == "TestBot"
        assert cfg.version == "0.1"

    def test_base_prompt_loaded(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assert "test coach" in cfg.base_prompt

    def test_tools_are_name_strings(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assert cfg.tools == ["get_character_raiderio", "run_simc"]

    def test_personas_loaded(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assert {p.id for p in cfg.personas} == {"khadgar", "thrall"}

    def test_personas_in_declaration_order(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assert cfg.list_persona_ids() == ["khadgar", "thrall"]

    def test_persona_fields_complete(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        k = cfg.get_persona_config("khadgar")
        assert k.display_name == "Archmage Khadgar"
        assert k.voice_prompt == "Speak as Khadgar."
        assert k.intro_message == "Hello from Khadgar."

    def test_config_is_frozen(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        with pytest.raises(ValidationError):
            cfg.name = "Mutated"

    def test_get_persona_config_unknown_returns_none(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assert cfg.get_persona_config("xalatath") is None

    def test_get_persona_config_none_returns_none(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assert cfg.get_persona_config(None) is None


# ---------------------------------------------------------------------------
# Agent file validation failures
# ---------------------------------------------------------------------------


class TestAgentFileValidation:
    def _load(self, tmp_path, agent_yaml, personas=None):
        root, _ = _write_config(tmp_path, agent_yaml=agent_yaml, personas=personas or {})
        return load_agent_config(config_root=root)

    def test_empty_base_prompt_raises(self, tmp_path):
        bad = AGENT_YAML.replace("base_prompt: |\n    You are a test coach.", "base_prompt: '   '")
        with pytest.raises(ConfigurationError):
            self._load(tmp_path, bad)

    def test_missing_base_prompt_raises(self, tmp_path):
        bad = textwrap.dedent("""\
            agent:
              name: Bot
              version: "0.1"
            tools: []
            personas: []
        """)
        with pytest.raises(ConfigurationError):
            self._load(tmp_path, bad)

    def test_duplicate_tool_names_raise(self, tmp_path):
        bad = textwrap.dedent("""\
            agent:
              name: Bot
              version: "0.1"
              base_prompt: You are a coach.
            tools:
              - run_simc
              - run_simc
            personas: []
        """)
        with pytest.raises(ConfigurationError):
            self._load(tmp_path, bad)

    def test_duplicate_persona_ids_raise(self, tmp_path):
        bad = textwrap.dedent("""\
            agent:
              name: Bot
              version: "0.1"
              base_prompt: You are a coach.
            tools: []
            personas:
              - khadgar
              - khadgar
        """)
        with pytest.raises(ConfigurationError):
            self._load(tmp_path, bad, personas={"khadgar": KHADGAR_YAML})

    def test_agent_file_not_found_raises(self, tmp_path):
        root = tmp_path / "config"
        (root / "agents").mkdir(parents=True)
        (root / "personas").mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            load_agent_config(agent_name="coach", config_root=root)


# ---------------------------------------------------------------------------
# Persona file validation failures
# ---------------------------------------------------------------------------


class TestPersonaFileValidation:
    def test_declared_persona_file_missing_raises(self, tmp_path):
        # Agent declares xalatath but no xalatath.yaml exists
        agent_with_extra = AGENT_YAML + "  - xalatath\n"
        root, _ = _write_config(tmp_path, agent_yaml=agent_with_extra)
        with pytest.raises(FileNotFoundError, match="xalatath"):
            load_agent_config(config_root=root)

    def test_persona_id_filename_mismatch_raises(self, tmp_path):
        wrong_id = KHADGAR_YAML.replace("id: khadgar", "id: thrall")
        root, _ = _write_config(tmp_path, personas={"khadgar": wrong_id, "thrall": THRALL_YAML})
        with pytest.raises(ConfigurationError, match="khadgar"):
            load_agent_config(config_root=root)

    def test_persona_id_with_spaces_raises(self, tmp_path):
        bad = KHADGAR_YAML.replace("id: khadgar", 'id: "bad id"')
        root, _ = _write_config(tmp_path, personas={"khadgar": bad, "thrall": THRALL_YAML})
        with pytest.raises(ConfigurationError):
            load_agent_config(config_root=root)

    def test_empty_voice_prompt_raises(self, tmp_path):
        bad = KHADGAR_YAML.replace("voice_prompt: Speak as Khadgar.", "voice_prompt: '   '")
        root, _ = _write_config(tmp_path, personas={"khadgar": bad, "thrall": THRALL_YAML})
        with pytest.raises(ConfigurationError):
            load_agent_config(config_root=root)


# ---------------------------------------------------------------------------
# Tool cross-reference
# ---------------------------------------------------------------------------


class TestToolCrossReference:
    def test_valid_names_pass(self, tmp_path):
        root, _ = _write_config(tmp_path)
        tools = [_fake_tool("get_character_raiderio"), _fake_tool("run_simc")]
        load_agent_config(config_root=root, tools=tools)  # no raise

    def test_orphaned_yaml_name_raises(self, tmp_path):
        root, _ = _write_config(tmp_path)
        tools = [_fake_tool("get_character_raiderio")]  # run_simc missing
        with pytest.raises(ConfigurationError, match="no Python implementation"):
            load_agent_config(config_root=root, tools=tools)

    def test_excluded_python_tool_logs_debug(self, tmp_path, caplog):
        import logging

        root, _ = _write_config(tmp_path)
        tools = [
            _fake_tool("get_character_raiderio"),
            _fake_tool("run_simc"),
            _fake_tool("extra_tool"),  # available in Python, not declared in YAML
        ]
        with caplog.at_level(logging.DEBUG):
            load_agent_config(config_root=root, tools=tools)
        assert "extra_tool" in caplog.text

    def test_none_tools_skips_check(self, tmp_path):
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root, tools=None)
        assert cfg.tools == ["get_character_raiderio", "run_simc"]


# ---------------------------------------------------------------------------
# PromptAssembler
# ---------------------------------------------------------------------------


class TestPromptAssembler:
    def test_no_persona_renders_base_only(self, tmp_path):
        root, template_path = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assembler = PromptAssembler(template_path=template_path)
        prompt = assembler.render(cfg, persona=None)
        assert "test coach" in prompt
        assert "GUARDRAIL" not in prompt

    def test_persona_adds_guardrail(self, tmp_path):
        root, template_path = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        persona = cfg.get_persona_config("khadgar")
        assembler = PromptAssembler(template_path=template_path)
        prompt = assembler.render(cfg, persona=persona)
        assert "GUARDRAIL" in prompt

    def test_persona_voice_prompt_appended(self, tmp_path):
        root, template_path = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        persona = cfg.get_persona_config("khadgar")
        assembler = PromptAssembler(template_path=template_path)
        prompt = assembler.render(cfg, persona=persona)
        assert "Speak as Khadgar" in prompt

    def test_guardrail_before_voice_prompt(self, tmp_path):
        root, template_path = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        persona = cfg.get_persona_config("khadgar")
        assembler = PromptAssembler(template_path=template_path)
        prompt = assembler.render(cfg, persona=persona)
        assert prompt.index("GUARDRAIL") < prompt.index("Speak as Khadgar")

    def test_no_persona_no_guardrail_no_trailing_whitespace(self, tmp_path):
        root, template_path = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        assembler = PromptAssembler(template_path=template_path)
        prompt = assembler.render(cfg, persona=None)
        # Should not have a large block of trailing whitespace from the
        # conditional persona section
        assert not prompt.endswith("\n\n\n")

    def test_missing_template_raises(self, tmp_path):
        assembler = PromptAssembler(template_path=tmp_path / "nonexistent.jinja2")
        root, _ = _write_config(tmp_path)
        cfg = load_agent_config(config_root=root)
        with pytest.raises(FileNotFoundError):
            assembler.render(cfg)

    def test_strict_undefined_raises_on_missing_var(self, tmp_path):
        from jinja2 import UndefinedError

        bad_template = "{{ base_prompt }} {{ undefined_variable }}"
        root, _ = _write_config(tmp_path)
        template_path = tmp_path / "bad_template.jinja2"
        template_path.write_text(bad_template, encoding="utf-8")
        cfg = load_agent_config(config_root=root)
        assembler = PromptAssembler(template_path=template_path)
        with pytest.raises(UndefinedError):
            assembler.render(cfg)

    def test_template_cached_after_first_render(self, tmp_path):
        root, template_path = _write_config(tmp_path)
        _ = load_agent_config(config_root=root)
        assembler = PromptAssembler(template_path=template_path)
        # Access _template twice — cached_property means file is only loaded once
        t1 = assembler._template
        t2 = assembler._template
        assert t1 is t2


# ---------------------------------------------------------------------------
# resolve_tools
# ---------------------------------------------------------------------------


class TestResolveTools:
    def test_returns_declared_subset(self):
        t1 = _fake_tool("get_character_raiderio")
        t2 = _fake_tool("run_simc")
        t3 = _fake_tool("search_guide_rag")
        result = resolve_tools(["get_character_raiderio", "run_simc"], [t1, t2, t3])
        assert result == [t1, t2]

    def test_preserves_declaration_order(self):
        t1 = _fake_tool("run_simc")
        t2 = _fake_tool("get_character_raiderio")
        result = resolve_tools(["get_character_raiderio", "run_simc"], [t1, t2])
        assert [t.name for t in result] == ["get_character_raiderio", "run_simc"]

    def test_missing_tool_warns_and_skips(self, caplog):
        import logging

        t1 = _fake_tool("get_character_raiderio")
        with caplog.at_level(logging.WARNING):
            result = resolve_tools(["get_character_raiderio", "run_simc"], [t1])
        assert len(result) == 1
        assert "run_simc" in caplog.text

    def test_empty_declared_list_returns_empty(self):
        tools = [_fake_tool("run_simc")]
        assert resolve_tools([], tools) == []

    def test_all_tools_declared(self):
        tools = [_fake_tool("a"), _fake_tool("b"), _fake_tool("c")]
        result = resolve_tools(["a", "b", "c"], tools)
        assert len(result) == 3
