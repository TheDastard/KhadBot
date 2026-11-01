"""
api/schemas/abilities.py

``GET /v1/reports/{code}/abilities``: the abilities a report names, with the name and icon name WCL gives them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AbilityIcon(BaseModel):
    """One ability the report names: its game ID, name and icon."""

    id: int
    name: str
    icon: str | None = Field(
        default=None,
        description="WCL's icon name for the ability, lowercase and without a file extension. Null when the cached "
        "report metadata predates the icon field (the recorded fixtures).",
    )


class ReportAbilities(BaseModel):
    """The abilities of a report's cached metadata, by game ID."""

    code: str
    abilities: list[AbilityIcon]
