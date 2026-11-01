"""
api/schemas/names.py

Display names the pull page joins onto the IDs records carry: ``GET /v1/encounters/{id}/names``, ``GET /v1/cooldowns``
and ``GET /v1/reports/{code}/players``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from khadbot.api.schemas.common import DifficultyName

# WCL's class names for player actors (``masterData.actors``: ``type`` Player, ``subType`` the class), as the recorded
# reports carry them. WCL also writes ``Unknown``; that, and any value not listed here, is served as no class.
PlayerClass = Literal[
    "DeathKnight",
    "DemonHunter",
    "Druid",
    "Evoker",
    "Hunter",
    "Mage",
    "Monk",
    "Paladin",
    "Priest",
    "Rogue",
    "Shaman",
    "Warlock",
    "Warrior",
]


class CooldownName(BaseModel):
    """A cooldown file entry's display name, the file it is in and whether it has been checked against the game."""

    name: str
    category: Literal["defensive", "offensive", "utility"]
    verified: bool


class CooldownList(BaseModel):
    """Every cooldown file entry by spell ID, whatever the report or encounter (``GET /v1/cooldowns``)."""

    cooldowns: dict[int, CooldownName]  # spell ID -> entry; defensive, then offensive, then utility file order


class PhaseName(BaseModel):
    """A phase's name from the encounter file, and the part before its first ": " (the whole name when it has none)."""

    name: str
    short_name: str  # "Stage Three: Ula'tek's Ascension" -> "Stage Three"


class EncounterNames(BaseModel):
    """
    Display names for the IDs records carry: phases, mechanics and damage windows from the encounter file's view for
    one difficulty. Without an encounter file for that difficulty the lookup is empty (``encounter_name`` null).
    Cooldown names are ``GET /v1/cooldowns``.
    """

    encounter_id: int
    difficulty: int  # WCL difficulty code
    difficulty_name: DifficultyName | None
    encounter_name: str | None  # the encounter file's name
    phases: dict[str, PhaseName]  # phase ID -> names, in the file's order
    mechanics: dict[str, str]  # mechanic ID -> name
    damage_windows: dict[str, str]  # damage window ID -> name


class Player(BaseModel):
    """A player actor of the report. Records name players by ``name``; deaths also carry ``actor_id`` (``id``)."""

    id: int
    name: str
    class_name: PlayerClass | None


class PlayerList(BaseModel):
    code: str
    players: list[Player]  # player actors in at least one fight of the report metadata, by actor ID
