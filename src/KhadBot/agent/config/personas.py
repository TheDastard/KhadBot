"""
agent/config/personas.py

Persona accessors for KhadBot.

Personas are loaded as part of AgentConfig — this module provides the
CoachPersona dataclass and accessor functions that wrap AgentConfig lookups.

Key design constraint: personas are *agent-scoped*.  There is no global
persona registry.  Lookups always go through an AgentConfig so only the
personas declared in the active agent YAML are reachable.

get_persona() accepts an optional AgentConfig.  When not provided it falls
back to get_agent_config() so callers that don't have a config reference
handy keep a clean call signature.

intro_message
-------------
The intro_message field on CoachPersona is the opening line the persona
delivers when a fresh session starts.  The CLI surfaces it before the first
user turn.  It is NOT injected into the synthesis prompt — it is a UX
affordance only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from khadbot.agent.config.loader import AgentConfig, PersonaConfig

# ---------------------------------------------------------------------------
# CoachPersona — public dataclass used by CLI, Discord, and graph code
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoachPersona:
    """
    Runtime persona representation.  Thin wrapper around PersonaConfig that
    preserves a clean public type for callers that don't need the full
    Pydantic model.
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


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def get_persona(
    persona_id: str | None,
    config: AgentConfig | None = None,
) -> PersonaConfig | None:
    """
    Return the PersonaConfig for the given ID, scoped to the active agent.

    Returns None for missing or empty IDs and for IDs not declared in the
    agent's persona list — never raises.

    Args:
        persona_id: Persona slug e.g. "thrall", None, or empty string.
        config:     AgentConfig to scope the lookup to.  Defaults to
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


def resolve_session_persona(
    explicit_id: str | None = None,
    config: AgentConfig | None = None,
) -> PersonaConfig | None:
    """
    Resolve the active persona for a new session.

    Priority: explicit_id argument → KHADBOT_PERSONA env var → None.
    Always scoped to the provided (or default) AgentConfig.
    """
    persona_id = explicit_id or os.environ.get("KHADBOT_PERSONA", "").strip() or None
    return get_persona(persona_id, config)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _default_config() -> AgentConfig:
    from khadbot.agent.config.loader import get_agent_config

    return get_agent_config()
