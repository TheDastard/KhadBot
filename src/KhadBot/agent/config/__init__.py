"""
agent/config/__init__.py

Public surface for the KhadBot agent configuration subpackage.

Callers import from here rather than from individual modules:

    from khadbot.agent.config import (
        AgentConfig,
        PersonaConfig,
        CoachPersona,
        ConfigurationError,
        get_agent_config,
        load_agent_config,
        get_persona,
        list_personas,
        resolve_session_persona,
        get_assembler,
        PromptAssembler,
    )

Internal modules:
    loader.py       — YAML schemas, AgentConfig, PersonaConfig, loading machinery
    personas.py     — CoachPersona, persona accessor functions
    assembler.py    — PromptAssembler, Jinja2 prompt rendering
"""

from khadbot.agent.config.assembler import (
    PromptAssembler,
    get_assembler,
)
from khadbot.agent.config.loader import (
    AgentConfig,
    ConfigurationError,
    PersonaConfig,
    get_agent_config,
    load_agent_config,
)
from khadbot.agent.config.personas import (
    CoachPersona,
    get_persona,
    list_personas,
    resolve_session_persona,
)

__all__ = [
    # Types
    "AgentConfig",
    "PersonaConfig",
    "CoachPersona",
    "ConfigurationError",
    # Loaders
    "get_agent_config",
    "load_agent_config",
    # Persona accessors
    "get_persona",
    "list_personas",
    "resolve_session_persona",
    # Prompt assembly
    "PromptAssembler",
    "get_assembler",
]
