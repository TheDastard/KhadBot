"""
tests/fixtures/agent_payloads.py

Pre-built FakeListChatModel response sequences and mock tool return values
for agent unit tests. Import these rather than redeclaring in each test file.
"""

from langchain_core.messages import AIMessage

# ---------------------------------------------------------------------------
# Fake LLM sequences
# Passed to FakeListChatModel(responses=[...]) in the order they'll be consumed.
# ---------------------------------------------------------------------------

# Sequence: agent calls raiderio, then synthesizes an answer
RAIDERIO_THEN_ANSWER = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_character_raiderio",
                "args": {"name": "Pyroblastus", "realm": "area-52", "region": "us"},
                "id": "call_001",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(
        content=(
            "Pyroblastus is a 2847 IO Fire Mage at ilvl 639. "
            "With 9/8M raid progression they're a serious player. "
            "Focus on optimizing Combustion windows and Living Bomb pandemic timing."
        )
    ),
]

# Sequence: agent calls raiderio then warcraftlogs (multi-tool turn)
RAIDERIO_THEN_LOGS_THEN_ANSWER = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_character_raiderio",
                "args": {"name": "Pyroblastus", "realm": "area-52", "region": "us"},
                "id": "call_001",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_warcraftlogs_report",
                "args": {"report_id": "abc123", "filters": {}},
                "id": "call_002",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(content="Here's what the log shows..."),
]

# Sequence: agent routes a build question to RAG only (no logs, no raiderio)
RAG_ONLY_ANSWER = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_guide_rag",
                "args": {"spec": "fire_mage", "question": "What talents for single target?"},
                "id": "call_001",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(content="According to Icy Veins, Fire Mage single-target talent setup is..."),
]

# Sequence: agent routes a sim question to run_simc only
SIMC_ONLY_ANSWER = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "run_simc",
                "args": {"simc_string": "mage=Pyroblastus\n...", "options": {}},
                "id": "call_001",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(content="Simulation complete. Your current setup does 487,234 DPS."),
]

# Sequence: first tool call returns an error; agent retries with modified args
TOOL_ERROR_THEN_RETRY = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_character_raiderio",
                "args": {"name": "Pyroblastus", "realm": "area52", "region": "us"},  # bad realm
                "id": "call_001",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_character_raiderio",
                "args": {"name": "Pyroblastus", "realm": "area-52", "region": "us"},  # corrected
                "id": "call_002",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(content="Found the character. Here's their profile..."),
]

# Sequence: all tools fail; agent acknowledges gracefully
ALL_TOOLS_FAIL_RESPONSE = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_character_raiderio",
                "args": {"name": "Unknown", "realm": "area-52", "region": "us"},
                "id": "call_001",
                "type": "tool_call",
            }
        ],
    ),
    AIMessage(
        content=(
            "I wasn't able to find that character on Raider.IO. "
            "Please double-check the name, realm, and region. "
            "You can also try logging in to WoW to refresh your profile."
        )
    ),
]


# ---------------------------------------------------------------------------
# Mock tool return values
# Used to patch tool calls in agent integration tests.
# ---------------------------------------------------------------------------

MOCK_RAIDERIO_RESULT = {
    "name": "Pyroblastus",
    "realm": "Area 52",
    "region": "us",
    "class": "Mage",
    "spec": "Fire",
    "item_level_equipped": 639,
    "mythic_plus_score": 2847.3,
    "highest_key_completed": {"dungeon": "Ara-Kara", "level": 12, "timed": True},
    "best_runs": [
        {"dungeon": "Ara-Kara", "level": 12, "score": 187.4, "timed": True},
    ],
    "raid_progression": {"nerub-ar-palace": {"summary": "9/8M"}},
}

MOCK_RAIDERIO_NOT_FOUND = {
    "error": True,
    "not_found": True,
    "message": "Character 'Unknown-area-52' (US) not found on Raider.IO.",
}

MOCK_RAIDERIO_INVALID_REGION = {
    "error": True,
    "message": "Invalid region 'xx'. Must be one of: eu, kr, tw, us",
}
