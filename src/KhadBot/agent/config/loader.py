"""
agent/config/loader.py

Loads, validates, and merges split agent + persona YAML configs into a single
AgentConfig object used by the rest of the codebase.

File layout under config/:

    config/
      agents/
        coach.yaml            ← name, version, base_prompt, tool names, persona IDs
        coach_mythic.yaml     ← future: different prompt + tool subset
      personas/
        khadgar.yaml
        thrall.yaml
        xalatath.yaml
      templates/
        prompt.jinja2         ← system prompt template; rendered by PromptAssembler
      skills/
        *.yaml                ← per-skill tool subsets; loaded separately by skills.py

Loading sequence (load_agent_config):
    1. Parse and validate the agent YAML         → AgentFileSchema
    2. For each declared persona ID, load and
       validate config/personas/{id}.yaml        → PersonaFileSchema
    3. Cross-reference declared tool names
       against the live Python TOOLS list
    4. Merge into a frozen AgentConfig

AgentConfig is the only type the rest of the codebase touches.
AgentFileSchema / PersonaFileSchema are internal parse intermediates.

Role in the skill-based graph architecture
------------------------------------------
In the LangGraph skill system, AgentConfig has two runtime responsibilities:

  1. base_prompt   — read by PromptAssembler to render the orchestrator's
                     Layer 1 system prompt (identity + persona framing).
  2. personas      — accessed by personas.get_persona() to resolve the active
                     persona for a session, scoped to this agent's declarations.

The tools list in coach.yaml now serves as a **master validation registry**
rather than a runtime tool selector.  Per-skill tool selection is declared
in config/skills/*.yaml and resolved by agent/skills.py.  The agent-level
tools list is cross-referenced at startup to catch name mismatches early,
but is not used to construct tool lists at runtime.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_ROOT = Path(__file__).parent.parent.parent.parent.parent / "config"


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


class ConfigurationError(Exception):
    """
    Raised when a config file fails schema validation, a declared persona
    file is missing, or a tool name has no Python implementation.
    Surfaced at startup — misconfigurations fail before the first request.
    """

    pass


# ---------------------------------------------------------------------------
# File-level schemas (parse intermediates)
# ---------------------------------------------------------------------------


class AgentFileSchema(BaseModel):
    """Schema for config/agents/{name}.yaml."""

    class _AgentBlock(BaseModel):
        name: str
        version: str
        base_prompt: str

        @field_validator("name", "version", "base_prompt")
        @classmethod
        def not_empty(cls, v: str) -> str:
            if not v.strip():
                raise ValueError("Field must not be empty.")
            return v

    agent: _AgentBlock
    tools: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_tool_names(self) -> AgentFileSchema:
        dupes = {n for n in self.tools if self.tools.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate tool names in agent config: {dupes}")
        return self

    @model_validator(mode="after")
    def unique_persona_ids(self) -> AgentFileSchema:
        dupes = {p for p in self.personas if self.personas.count(p) > 1}
        if dupes:
            raise ValueError(f"Duplicate persona IDs in agent config: {dupes}")
        return self


class PersonaFileSchema(BaseModel):
    """Schema for config/personas/{id}.yaml."""

    id: str
    display_name: str
    intro_message: str
    voice_prompt: str

    @field_validator("id")
    @classmethod
    def id_is_slug(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Persona id must not be empty.")
        if " " in v:
            raise ValueError(f"Persona id '{v}' must not contain spaces.")
        return v.strip()

    @field_validator("display_name", "intro_message", "voice_prompt")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v


# ---------------------------------------------------------------------------
# Merged AgentConfig (public type used by orchestrator.py, personas.py, assembler)
# ---------------------------------------------------------------------------


class PersonaConfig(BaseModel):
    """Validated, ready-to-use persona."""

    id: str
    display_name: str
    intro_message: str
    voice_prompt: str

    model_config = {"frozen": True}


class AgentConfig(BaseModel):
    """
    Fully merged agent configuration. Always produced by load_agent_config()
    — never constructed directly.
    """

    name: str
    version: str
    base_prompt: str
    tools: list[str]  # master tool registry for startup validation only;
    # runtime tool selection is per-skill (config/skills/*.yaml)
    personas: list[PersonaConfig]

    model_config = {"frozen": True}

    def get_persona_config(self, persona_id: str | None) -> PersonaConfig | None:
        if not persona_id:
            return None
        return next((p for p in self.personas if p.id == persona_id), None)

    def list_persona_ids(self) -> list[str]:
        return [p.id for p in self.personas]


# ---------------------------------------------------------------------------
# Loader internals
# ---------------------------------------------------------------------------


def _parse_yaml_file(path: Path, label: str) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found at: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigurationError(f"YAML parse error in {label} ({path}):\n{e}") from e


def _load_agent_file(path: Path) -> AgentFileSchema:
    raw = _parse_yaml_file(path, label=f"agent config '{path.name}'")
    try:
        return AgentFileSchema.model_validate(raw)
    except Exception as e:
        raise ConfigurationError(f"Schema validation failed for agent config '{path.name}':\n{e}") from e


def _load_persona_file(path: Path, expected_id: str) -> PersonaFileSchema:
    raw = _parse_yaml_file(path, label=f"persona config '{path.name}'")
    try:
        schema = PersonaFileSchema.model_validate(raw)
    except Exception as e:
        raise ConfigurationError(f"Schema validation failed for persona config '{path.name}':\n{e}") from e
    if schema.id != expected_id:
        raise ConfigurationError(
            f"Persona file '{path.name}' declares id='{schema.id}' "
            f"but expected id='{expected_id}'. "
            f"Rename the file or correct the id field."
        )
    return schema


def _validate_tool_names(declared: list[str], tools: list) -> None:
    """
    Cross-reference declared tool names against live Python @tool objects.

    Declared names with no Python counterpart → ConfigurationError.
    Python tools not declared in the agent YAML → DEBUG log only (they're
    simply not available to this agent, which is intentional).
    """
    python_names = {getattr(t, "name", None) for t in tools}
    declared_set = set(declared)

    orphaned = declared_set - python_names
    if orphaned:
        raise ConfigurationError(
            f"Agent config declares tools with no Python implementation: {orphaned}\n"
            f"Available Python tools: {sorted(python_names)}"
        )

    excluded = python_names - declared_set
    if excluded:
        logger.debug(f"Tools available but not declared for this agent: {excluded}")


def _merge(agent: AgentFileSchema, personas: list[PersonaFileSchema]) -> AgentConfig:
    """Pure data assembly — no IO, no validation."""
    return AgentConfig(
        name=agent.agent.name,
        version=agent.agent.version,
        base_prompt=agent.agent.base_prompt,
        tools=list(agent.tools),
        personas=[
            PersonaConfig(
                id=p.id,
                display_name=p.display_name,
                intro_message=p.intro_message,
                voice_prompt=p.voice_prompt,
            )
            for p in personas
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_agent_config(
    agent_name: str = "coach",
    config_root: Path | str | None = None,
    tools: list | None = None,
) -> AgentConfig:
    """
    Load, validate, and merge agent + persona YAML files into an AgentConfig.

    File resolution:
        {config_root}/agents/{agent_name}.yaml
        {config_root}/personas/{persona_id}.yaml  (one per declared persona)

    Args:
        agent_name:   Stem of the agent YAML file. Default: "coach".
        config_root:  Config directory root. Default: project_root/config/.
                      Pass an explicit path in tests to use fixture files.
        tools:        Live TOOLS list from src/tools/__init__.py. When provided,
                      declared tool names are cross-referenced against Python
                      implementations. Pass None to skip (unit tests).

    Raises:
        FileNotFoundError:   Agent or persona YAML file does not exist.
        ConfigurationError:  Schema validation failure or tool name mismatch.
    """
    root = Path(config_root) if config_root else _DEFAULT_CONFIG_ROOT

    agent_path = root / "agents" / f"{agent_name}.yaml"
    agent_schema = _load_agent_file(agent_path)
    logger.debug(
        f"Loaded agent '{agent_schema.agent.name}' from {agent_path} — "
        f"{len(agent_schema.tools)} tools, {len(agent_schema.personas)} personas declared"
    )

    personas_dir = root / "personas"
    persona_schemas: list[PersonaFileSchema] = []
    for persona_id in agent_schema.personas:
        persona_path = personas_dir / f"{persona_id}.yaml"
        persona_schemas.append(_load_persona_file(persona_path, expected_id=persona_id))
        logger.debug(f"Loaded persona '{persona_id}' from {persona_path}")

    if tools is not None:
        _validate_tool_names(agent_schema.tools, tools)

    config = _merge(agent_schema, persona_schemas)
    logger.info(
        f"Agent config ready: {config.name} v{config.version} | "
        f"{len(config.tools)} tools | {len(config.personas)} personas"
    )
    return config


@cache
def get_agent_config(agent_name: str = "coach") -> AgentConfig:
    """
    Cached AgentConfig for the given agent name. Loads on first call.
    Bypass with load_agent_config(config_root=...) in tests.
    """
    from khadbot.tools import TOOLS

    return load_agent_config(agent_name=agent_name, tools=TOOLS)
