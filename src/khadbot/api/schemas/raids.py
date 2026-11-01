"""
api/schemas/raids.py

The Raids page (hosted mode): ``GET /v1/lookup/report``, a report's boss pulls before any is loaded; the server list
and guild and character search, whose nights have the lookup's shape; and the body of a 429 that says how long until
the WCL budget has points.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from khadbot.api.schemas.common import DifficultyName, ErrorBody, Outcome


class BudgetWaitBody(ErrorBody):
    """The answer when the WCL budget has no points for a request now: how long until it would."""

    wait_s: float = Field(description="Seconds until the budget would have the points; try again then")


class LookupZone(BaseModel):
    """The raid zone WCL files the report under."""

    id: int
    name: str


class LookupPull(BaseModel):
    """One boss pull of a looked-up report, from the report's metadata; nothing about it has to be loaded."""

    fight: int = Field(description="WCL fight ID; the pull page is /r/{code}/p/{fight}")
    ordinal: int = Field(description="1-based within its group, time order")
    label: str = Field(description='"P<ordinal>": a display label only')
    outcome: Outcome
    duration_ms: int
    boss_pct: float | None = Field(description="WCL's fight percentage left, 0-100; the primary figure")
    boss_hp_pct: float | None = Field(description="The boss's health left, 0-100")
    ready: bool = Field(description="Whether the pull's digest is already written, so the pull page opens at once")


class LookupGroup(BaseModel):
    """One encounter and difficulty of the night."""

    encounter_id: int
    encounter_name: str
    difficulty: int | None
    difficulty_name: DifficultyName | None
    pulls: list[LookupPull] = Field(description="Time order; ordering kills first is the client's job")
    best_fight: int = Field(description="The group's best pull: its first kill, else the one with the lowest fight %")


class ReportLookup(BaseModel):
    """A report's boss pulls, grouped by encounter and difficulty, as WCL lists them now."""

    code: str
    title: str | None
    started_at: datetime = Field(description="When the report was started (UTC)")
    zone: LookupZone | None
    focus_fight: int | None = Field(description="The fight a URL's #fight= named, when it names a boss pull here")
    groups: list[LookupGroup] = Field(description="In the order first pulled")


class ServerEntry(BaseModel):
    name: str = Field(description="As players know it, e.g. Aggra (Português)")
    slug: str = Field(description="WCL's server slug, e.g. aggra-português: what the searches take")
    region: str = Field(description="WCL's region slug, e.g. eu")
    match: str = Field(
        description="The name folded for matching as the user types: lower case, accents removed, only letters and "
        'digits kept, e.g. "aggraportugues". Fold the typed text the same way and match on it'
    )


class ServerRegion(BaseModel):
    slug: str = Field(description="WCL's region slug, e.g. us: what the searches take")
    name: str = Field(description='The short name shown, e.g. "US"')
    servers: list[ServerEntry] = Field(description="Sorted by name")
    complete: bool = Field(description="Whether the list holds every server WCL had for the region")


class ServerList(BaseModel):
    """
    Every server a search can name, by region, in the file's order (United States, then Europe): the whole list in one
    response, with no filtering. The web app filters it in the browser as the user types.
    """

    regions: list[ServerRegion]


class SearchGuild(BaseModel):
    id: int = Field(description="WCL's guild ID")
    name: str
    server_name: str
    server_slug: str
    region: str = Field(description='The short name of the region, e.g. "US"')
    faction: str | None = Field(description='The faction name WCL gives, e.g. "Horde"; None when it gives none')


class GuildSearch(BaseModel):
    """A guild and its raid nights in the window, newest first, each in the report lookup's shape."""

    guild: SearchGuild
    window_start: datetime = Field(description="The window's start (UTC): reports that started after it are listed")
    window_end: datetime
    nights: list[ReportLookup] = Field(description="Newest first; only reports the caller may read, with boss pulls")
    capped: bool = Field(description="True when the guild has more reports in the window than one search lists")
    left_out: int = Field(
        description="Listed reports not shown: ones the caller may not read, or that Warcraft Logs could not load"
    )


class SearchCharacter(BaseModel):
    name: str
    server_name: str
    server_slug: str
    region: str = Field(description='The short name of the region, e.g. "US"')


class CharacterSearch(BaseModel):
    """
    A character's recent nights, newest first, each holding only the boss pulls the character took part in (pull
    labels and ordinals are the night's own, so P7 here is P7 in the log search).
    """

    character: SearchCharacter
    encounter_id: int | None = Field(description="The encounter the pulls were limited to, if one was asked for")
    nights: list[ReportLookup]
    capped: bool = Field(description="True when the character has more recent reports than one search lists")
    left_out: int = Field(
        description="Listed reports not shown: ones the caller may not read, or that Warcraft Logs could not load"
    )
