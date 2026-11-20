"""
api/schemas/art.py

``GET /v1/art/encounters``: each raid boss's art, as the web app draws it (a journal portrait and a kill-achievement
thumbnail) and the attribution line that goes with it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EncounterArt(BaseModel):
    """One raid boss's art. Either image is null when it is not available; the web app then draws its fallback."""

    encounter_id: int = Field(description="WCL's encounter ID")
    name: str = Field(description="The boss's name as WCL spells it")
    portrait_url: str | None = Field(
        default=None,
        description="Path on this API's own origin of the journal portrait (a 128 by 64 PNG), ending in `?v=` and a "
        "hash of the image, so a re-export changes the URL. Null when no portrait is registered or uploaded.",
    )
    thumbnail_url: str | None = Field(
        default=None,
        description="Absolute URL of the boss's Mythic kill achievement icon (a 56 by 56 JPG) on Blizzard's image "
        "host; the browser loads it directly. Null when the API has no Blizzard credentials, Blizzard could not "
        "answer, or the boss has no registered achievement.",
    )


class EncounterArtList(BaseModel):
    """The art of every boss in the registry, in registry order."""

    attribution: str = Field(description="The line to show wherever this art is drawn, naming Blizzard as its source")
    encounters: list[EncounterArt]
