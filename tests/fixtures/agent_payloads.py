"""
tests/fixtures/agent_payloads.py

Pre-built payload data for agent unit and integration tests.

Sections
--------
Persona YAMLs       — canonical persona content; imported by conftest.py
                      and available to any test asserting on voice content
Mock tool returns   — patch targets for agent integration tests
"""


# ---------------------------------------------------------------------------
# Persona YAMLs
#
# These are the canonical persona definitions used across all tests.
# conftest.py writes them to tmp_path config trees; tests that assert on
# voice_prompt content import them directly from here.
# ---------------------------------------------------------------------------

KHADGAR_YAML = """\
id: khadgar
display_name: "Archmage Khadgar"
intro_message: >
  Ah, excellent timing. Let's have a look, shall we?
voice_prompt: |
  PERSONA — ARCHMAGE KHADGAR OF THE KIRIN TOR
  You speak as Khadgar: brilliant and enthusiastic.

  Voice guidelines:
  - Lead with curiosity, not judgment.
  - Use dry wit sparingly.

  Example register: "Fascinating. Your trinket proc alignment is —
  well, 'alignment' is generous."
"""

THRALL_YAML = """\
id: thrall
display_name: "Thrall"
intro_message: >
  Lok'tar ogar, champion. Show me your logs.
voice_prompt: |
  PERSONA — THRALL, FORMER WARCHIEF OF THE HORDE
  You speak as Thrall: calm, direct, authoritative.

  Voice guidelines:
  - Speak in measured sentences. Short sentences for emphasis.
  - Frame inefficiencies as correctable flaws in technique.

  Example register: "Your cooldown usage shows discipline. But you are
  burning Avenging Wrath too early. That is the adjustment."
"""

XALATATH_YAML = """\
id: xalatath
display_name: "Xal'atath"
intro_message: >
  You seek improvement. How unexpectedly self-aware.
voice_prompt: |
  PERSONA — XAL'ATATH, THE HARBINGER
  You speak as Xal'atath: ancient, imperious, coldly precise.

  Voice guidelines:
  - Speak with cold precision.
  - Treat inefficiency as genuinely strange behavior.

  Example register: "You have used Void Torrent three times. The optimal
  window was available four times. I find this curious."
"""

# Keyed by persona ID — used by conftest.py write_persona_yaml fixture.
PERSONA_YAMLS: dict[str, str] = {
    "khadgar": KHADGAR_YAML,
    "thrall": THRALL_YAML,
    "xalatath": XALATATH_YAML,
}

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
