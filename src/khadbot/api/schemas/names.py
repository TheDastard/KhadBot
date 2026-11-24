"""
api/schemas/names.py

Display names the pull page joins onto the IDs records carry: ``GET /v1/encounters/{id}/names``, ``GET /v1/cooldowns``
and ``GET /v1/reports/{code}/players``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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


class MechanicInfo(BaseModel):
    """What the pull page shows about a mechanic beside its name: its abilities, icon, kind, severity and rule."""

    ability_ids: list[int] = Field(description="The game's spell IDs of the mechanic's abilities, in the file's order")
    icon_ability_id: int = Field(
        description=(
            "The one of `ability_ids` that stands for the mechanic as an icon: the encounter file's `icon_ability_id`, "
            "else the first ability ID"
        )
    )
    kind: Literal["avoidable", "soak", "interrupt", "dispel", "debuff", "unavoidable"]
    severity: Literal["low", "medium", "high"]
    failure_summary: str | None = Field(
        description=(
            "How the mechanic's failure rule counts failures and who they are charged to, in plain words built from "
            "the rule and its parameters; null for a mechanic without a failure rule"
        )
    )


class EncounterNames(BaseModel):
    """
    Display names for the IDs records carry: phases, mechanics (with their descriptions) and damage windows from the
    encounter file's view for one difficulty. Without an encounter file for that difficulty the lookup is empty (``encounter_name`` null).
    Cooldown names are ``GET /v1/cooldowns``.
    """

    encounter_id: int
    difficulty: int  # WCL difficulty code
    difficulty_name: DifficultyName | None
    encounter_name: str | None  # the encounter file's name
    phases: dict[str, PhaseName]  # phase ID -> names, in the file's order
    mechanics: dict[str, str]  # mechanic ID -> name
    mechanic_descriptions: dict[str, str] = Field(
        description=(
            "Mechanic ID -> the encounter file's one- or two-sentence plain-text description: what the mechanic does "
            "and what a failure means. Only mechanics that have one; every mechanic with a failure rule does."
        )
    )
    mechanic_verified: dict[str, bool] = Field(
        description=(
            "Mechanic ID -> whether a person has inspected the encounter file's entry against the logs; every "
            "mechanic in ``mechanics`` is listed. False entries are shown as unverified."
        )
    )
    mechanic_info: dict[str, MechanicInfo] = Field(
        description="Mechanic ID -> its abilities, icon ability, kind, severity and failure summary; every mechanic"
    )
    damage_windows: dict[str, str]  # damage window ID -> name


class Player(BaseModel):
    """A player actor of the report. Records name players by ``name``; deaths also carry ``actor_id`` (``id``)."""

    id: int
    name: str
    class_name: PlayerClass | None


class PlayerList(BaseModel):
    code: str
    players: list[Player]  # player actors in at least one fight of the report metadata, by actor ID
