"""
agent/personas.py

Persona accessors for KhadBot.

Personas are loaded as part of AgentConfig — this module provides the
CoachPersona dataclass (kept for backwards compatibility with CLI/Discord
code) and accessor functions that wrap AgentConfig lookups.

get_persona() now accepts an optional AgentConfig so lookups are scoped to
the personas actually available to the current agent. When no config is
provided it falls back to the default cached config, preserving the original
call signature for callers that don't have a config reference handy.
"""

from __future__ import annotations

from dataclasses import dataclass

from khadbot.agent.agent_config import AgentConfig, PersonaConfig


@dataclass(frozen=True)
class CoachPersona:
    """
    Runtime persona representation. Thin wrapper around PersonaConfig kept
    for backwards compatibility — CLI and Discord code that already imports
    CoachPersona doesn't need to change.
    """

    id: str
    display_name: str
    voice_prompt: str
    intro_message: str


def _to_coach_persona(cfg: PersonaConfig) -> CoachPersona:
    return CoachPersona(
        id=cfg.id,
        display_name=cfg.display_name,
        voice_prompt=cfg.voice_prompt,
        intro_message=cfg.intro_message,
    )


def get_persona(
    persona_id: str | None,
    config: AgentConfig | None = None,
) -> PersonaConfig | None:
    """
    Return the PersonaConfig for the given ID, scoped to the active agent.

    Returns None for missing/empty IDs and for IDs not available in the
    agent's declared persona list — never raises.

    Args:
        persona_id: Persona slug (e.g. "khadgar"), None, or empty string.
        config:     AgentConfig to scope the lookup to. Defaults to
                    get_agent_config() when not provided.
    """
    if not persona_id:
        return None
    cfg = config or _default_config()
    return cfg.get_persona_config(persona_id)


def list_personas(config: AgentConfig | None = None) -> list[CoachPersona]:
    """Return all personas available to the active agent, in declaration order."""
    cfg = config or _default_config()
    return [_to_coach_persona(p) for p in cfg.personas]


def _default_config() -> AgentConfig:
    from khadbot.agent.agent_config import get_agent_config

    return get_agent_config()
