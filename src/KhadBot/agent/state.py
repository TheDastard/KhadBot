"""
agent/state.py

Shared state contract for the KhadBot LangGraph graph.

Every node reads from and writes to KhadbotState.  The TypedDict fields are
the only coupling between graph components — nodes must not import each other
directly.

CharacterContext
----------------
Persists resolved character identity across conversation turns via LangGraph
checkpointing.  Once a character is resolved (name/realm/region confirmed via
find_character_reports), subsequent turns skip re-discovery and use the cached
context.  Subgraphs update this when they successfully resolve a character,
so the orchestrator can surface it to future turns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# Character context — persists across turns via checkpointing
# ---------------------------------------------------------------------------


@dataclass
class CharacterContext:
    """
    Resolved character identity for the current session.

    Set by any subgraph that successfully calls find_character_reports.
    Persisted in LangGraph checkpoint state so subsequent turns skip
    re-discovery.
    """

    name: str
    realm: str
    realm_slug: str
    region: str

    # Set after the first WarcraftLogs lookup — reused until the user
    # explicitly asks about a different report or fight.
    last_report_code: str | None = None
    last_fight_id: int | None = None

    # Raider.IO snapshot — fetched once per session if the active skill
    # requests it (fetch_raiderio: true in the skill YAML).
    raiderio_profile: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Shared graph state
# ---------------------------------------------------------------------------


class KhadbotState(TypedDict):
    """
    Single shared state object for the entire KhadBot graph.

    Annotated fields use LangGraph reducers where append semantics are needed
    (messages).  All other fields are last-write-wins.
    """

    # ── Conversation ──────────────────────────────────────────────────────
    # Full conversation history.  add_messages reducer appends new messages
    # rather than replacing the list — safe for concurrent node writes.
    messages: Annotated[list, add_messages]

    # ── Routing ───────────────────────────────────────────────────────────
    # The current user's request, extracted from the latest HumanMessage.
    # Router and orchestrator both read this; avoids re-parsing messages.
    task: str

    # Output of the router node — list of skill names to invoke.
    needed_skills: list[str]

    # Output of the skill loader node — live SkillDefinition objects keyed
    # by skill name.  Populated from SKILL_REGISTRY using needed_skills.
    # Import from skills.py; typed as Any here to avoid circular import.
    active_skills: dict[str, Any]  # dict[str, SkillDefinition]

    # Output of each skill subgraph — raw analysis text keyed by skill name.
    # The orchestrator reads all entries and synthesises a final response.
    skill_results: dict[str, str]

    # ── Session context ───────────────────────────────────────────────────
    # Resolved character identity.  None on fresh sessions; populated by the
    # first subgraph that successfully calls find_character_reports.
    # Persisted across turns by the LangGraph checkpoint layer.
    character_context: CharacterContext | None

    # ── Persona ───────────────────────────────────────────────────────────
    # Active persona ID for this session.  Set at session initialisation
    # from KHADBOT_PERSONA env var or caller argument.  Constant for the
    # session lifetime — swapping persona requires a new session.
    persona_id: str | None
