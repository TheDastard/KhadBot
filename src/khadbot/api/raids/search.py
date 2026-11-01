"""
api/raids/search.py

Guild and character search (``wcl/search.py`` holds the queries). Hosted only: ``create_app`` mounts this router with
the sign-in routes, beside the server list (``servers.py``), and sets ``features.raid_search``.

- ``GET /v1/search/guild?region=&server=&name=`` (or ``?url=`` a warcraftlogs.com guild URL): the guild (name, server,
  region, faction) and its nights from the last ``guild_window_days`` days, newest first, each in the report lookup's
  shape (``nights.py``).
- ``GET /v1/search/character?region=&server=&name=&encounter=``: the character's recent nights, newest first, each
  holding only the boss pulls the character took part in, optionally of one encounter.

Both check the region and server against the server list first (404 ``server_unknown`` without spending a point).

**Database points.** Listing a guild's reports (``reportData.reports``) and a character's (``Character.recentReports``)
are ``[Database Points]`` fields, so those requests reserve ``SearchConfig.database_points`` as well as
``SearchConfig.points`` (``wcl_request.py``); the budget holds database points against their own cap
(``jobs/budget.py``).

**At most ``max_reports`` reports per search.** Each listed night needs the report's metadata, the same request (and
raw page cache entry) as the report lookup and ingest, 2.01 points when it isn't cached, so the cap bounds a search's
spend. ``capped`` says when there were more.

**Caching** (``search_cache.py``): identities are kept for good, listings for ``listing_max_age_s`` (shared when made
on the app token, the caller's own when made on theirs), and every listed report passes the access check on every read
(``nights.py``).

**Errors.** 400 ``bad_guild_url``; 404 ``server_unknown``, ``guild_not_found``, ``character_not_found``; 429
``budget_exhausted`` with ``wait_s``; 503 ``wcl_unavailable``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from khadbot.api.budget import get_budget
from khadbot.api.deps import Caller
from khadbot.api.raids.lookup import (
    BudgetWaitError,
    get_clock,
    get_lookup_config,
)
from khadbot.api.raids.nights import build_nights, character_actor_ids, only_pulls_of
from khadbot.api.raids.search_cache import (
    Store,
    cached_identity,
    cached_listing,
    keep_listing,
    locked,
    name_key,
    own_token,
    store,
)
from khadbot.api.raids.servers import check_server, get_server_list
from khadbot.api.raids.wcl_request import GraphQLAnswerError, budget_response, get_search_config, wcl_request
from khadbot.api.routes import get_context
from khadbot.api.schemas import (
    BudgetWaitBody,
    CharacterSearch,
    ErrorBody,
    GuildSearch,
    ReportLookup,
    SearchCharacter,
    SearchGuild,
)
from khadbot.api.views import ApiContext, ApiError
from khadbot.auth.clock import Clock
from khadbot.auth.store import User
from khadbot.config import LookupConfig, SearchConfig
from khadbot.jobs.budget_store import BudgetManager
from khadbot.wcl.search import (
    CHARACTER_QUERY,
    GUILD_QUERY,
    GUILD_REPORTS_QUERY,
    Character,
    Guild,
    GuildSource,
    Listing,
    Region,
    character_variables,
    guild_reports_variables,
    guild_source,
    guild_variables,
    parse_character,
    parse_guild,
    parse_guild_reports,
    parse_guild_source,
)

Settings = Annotated[SearchConfig, Depends(get_search_config)]
Servers = Annotated["tuple[Region, ...]", Depends(get_server_list)]
Lookup = Annotated[LookupConfig, Depends(get_lookup_config)]
Now = Annotated[Clock, Depends(get_clock)]
Ctx = Annotated[ApiContext, Depends(get_context)]
Budget = Annotated[BudgetManager, Depends(get_budget)]


def _source(region: str | None, server: str | None, name: str | None, url: str | None = None) -> GuildSource:
    if url:
        try:
            return parse_guild_source(url)
        except ValueError:
            raise ApiError(400, "bad_guild_url", "Enter a warcraftlogs.com guild URL.") from None
    if not (region and server and name):
        raise ApiError(422, "bad_request", "Give region, server and name, or a guild url.")
    try:
        return guild_source(region, server, name)
    except ValueError as exc:
        raise ApiError(422, "bad_request", str(exc)) from None


# ---------------------------------------------------------------------------
# Guild
# ---------------------------------------------------------------------------


def _no_guild() -> ApiError:
    return ApiError(404, "guild_not_found", "No such guild on Warcraft Logs.")


def _no_character() -> ApiError:
    return ApiError(404, "character_not_found", "No such character on Warcraft Logs.")


async def guild_identity(
    request: Request, user: User | None, ctx: ApiContext, budget: BudgetManager, config: SearchConfig, now: Clock,
    source: GuildSource,
) -> Guild:  # fmt: skip
    """
    The guild's ID, name, server, region and faction: from the blob store (kept for good), else from WCL.

    Raises:
        ApiError: 404 ``guild_not_found``, from WCL or a cached answer that still stands.
    """
    key = Store.key("guilds", *name_key(source))

    async def load() -> Guild | None:
        identity = await cached_identity(ctx, config, now, key, _no_guild)
        return Guild(**identity) if identity is not None else None

    async def make() -> Guild:
        try:
            data = await wcl_request(
                request, user, budget, now, group=_group("guild", source), gql=GUILD_QUERY,
                variables=guild_variables(source), points=config.points,
            )  # fmt: skip
        except GraphQLAnswerError:
            data = {}
        guild = parse_guild(data)
        await store(ctx).put(
            key, {"found": True, "identity": asdict(guild)} if guild is not None else {"found": False}, now()
        )
        if guild is None:
            raise _no_guild()
        return guild

    return await locked(request, ("guild", *name_key(source)), load, make)


async def guild_listing(
    request: Request, user: User | None, ctx: ApiContext, budget: BudgetManager, config: SearchConfig, now: Clock,
    guild: Guild, window: tuple[datetime, datetime],
) -> Listing:  # fmt: skip
    """
    The guild's reports in ``window``: a listing younger than ``listing_max_age_s`` (shared when it was made on the app
    token, the caller's own when on theirs), else a new one from WCL.
    """
    shared = not await own_token(request, user)

    async def load() -> Listing | None:
        return await cached_listing(request, ctx, config, now, "guild", guild.id, user, shared)

    async def make() -> Listing:
        start, end = window
        variables = guild_reports_variables(
            guild.id, start.timestamp() * 1000, end.timestamp() * 1000, config.max_reports
        )
        try:
            data = await wcl_request(
                request, user, budget, now, group=f"search:guild:{guild.id}", gql=GUILD_REPORTS_QUERY,
                variables=variables, points=config.points, database_points=config.database_points,
                user_token=not shared,
            )  # fmt: skip
        except GraphQLAnswerError:
            raise ApiError(503, "wcl_unavailable", "Warcraft Logs could not list the guild's reports.") from None
        listing = parse_guild_reports(data)
        await keep_listing(request, ctx, now, "guild", guild.id, user, shared, listing)
        return listing

    scope = "app" if shared else f"user:{user.id if user else ''}"
    return await locked(request, ("guild-listing", scope, guild.id), load, make)


def _group(kind: str, source: GuildSource) -> str:
    return f"search:{kind}:{source.region}/{source.server}/{source.name.casefold()}"


# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------


async def character_and_listing(
    request: Request, user: User | None, ctx: ApiContext, budget: BudgetManager, config: SearchConfig, now: Clock,
    source: GuildSource,
) -> Character:  # fmt: skip
    """
    The character and its recent reports. The identity is kept for good and the listing like a guild's. WCL gives
    both in one request (``Character.recentReports``), so a stale listing costs that one request.

    Raises:
        ApiError: 404 ``character_not_found``, from WCL or a cached answer that still stands.
    """
    key = Store.key("characters", *name_key(source))
    shared = not await own_token(request, user)

    async def load() -> Character | None:
        identity = await cached_identity(ctx, config, now, key, _no_character)
        if identity is None:
            return None
        listing = await cached_listing(request, ctx, config, now, "character", identity["id"], user, shared)
        return Character(**identity, reports=listing) if listing is not None else None

    async def make() -> Character:
        try:
            data = await wcl_request(
                request, user, budget, now, group=_group("character", source), gql=CHARACTER_QUERY,
                variables=character_variables(source, config.max_reports), points=config.points,
                database_points=config.database_points, user_token=not shared,
            )  # fmt: skip
        except GraphQLAnswerError:
            data = {}
        character = parse_character(data)
        if character is None:
            await store(ctx).put(key, {"found": False}, now())
            raise _no_character()
        identity = {k: v for k, v in asdict(character).items() if k != "reports"}
        await store(ctx).put(key, {"found": True, "identity": identity}, now())
        await keep_listing(request, ctx, now, "character", character.id, user, shared, character.reports)
        return character

    scope = "app" if shared else f"user:{user.id if user else ''}"
    return await locked(request, ("character", scope, *name_key(source)), load, make)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

router = APIRouter(tags=["search"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorBody, "description": "Missing or invalid query parameter (bad_request)"},
    429: {"model": BudgetWaitBody, "description": "No WCL points free now (budget_exhausted); see wait_s"},
    503: {"model": ErrorBody, "description": "Warcraft Logs could not answer (wcl_unavailable)"},
}

Name = Annotated[str | None, Query(max_length=64)]
Slug = Annotated[str | None, Query(max_length=64)]


@router.get(
    "/v1/search/guild",
    response_model=GuildSearch,
    responses={
        400: {"model": ErrorBody, "description": "Not a warcraftlogs.com guild URL (bad_guild_url)"},
        404: {"model": ErrorBody, "description": "Unknown server (server_unknown) or guild (guild_not_found)"},
        **_ERRORS,
    },
)
async def search_guild(
    request: Request,
    response: Response,
    user: Caller,
    ctx: Ctx,
    budget: Budget,
    config: Settings,
    servers: Servers,
    lookup: Lookup,
    now: Now,
    region: Slug = None,
    server: Slug = None,
    name: Name = None,
    url: Annotated[str | None, Query(max_length=300, description="A warcraftlogs.com guild URL")] = None,
) -> Any:
    """
    A guild and its raid nights from the last 30 days, newest first: each night is a report lookup holding the
    report's boss pulls. Only reports the caller may read are listed.
    """
    source = _source(region, server, name, url)
    check_server(servers, source)
    end = now()
    start = end - timedelta(days=config.guild_window_days)
    try:
        guild = await guild_identity(request, user, ctx, budget, config, now, source)
        listing = await guild_listing(request, user, ctx, budget, config, now, guild, (start, end))
        in_window = [r for r in listing.reports if r.start_ms >= start.timestamp() * 1000][: config.max_reports]
        built = await build_nights(request, user, ctx, budget, lookup, now, in_window)
    except BudgetWaitError as wait:
        return budget_response(wait)
    except GraphQLAnswerError:
        raise ApiError(503, "wcl_unavailable", "Warcraft Logs could not answer the search.") from None
    response.headers["Cache-Control"] = "private, no-store"
    return GuildSearch(
        guild=SearchGuild(
            id=guild.id,
            name=guild.name,
            server_name=guild.server_name,
            server_slug=guild.server_slug or source.server,
            region=guild.region or source.region.upper(),
            faction=guild.faction,
        ),
        window_start=start.astimezone(UTC),
        window_end=end.astimezone(UTC),
        nights=built.nights,
        capped=listing.more or len(listing.reports) > config.max_reports,
        left_out=built.left_out,
    )


@router.get(
    "/v1/search/character",
    response_model=CharacterSearch,
    responses={
        404: {"model": ErrorBody, "description": "Unknown server (server_unknown) or character (character_not_found)"},
        **_ERRORS,
    },
)
async def search_character(
    request: Request,
    response: Response,
    user: Caller,
    ctx: Ctx,
    budget: Budget,
    config: Settings,
    servers: Servers,
    lookup: Lookup,
    now: Now,
    region: Annotated[str, Query(min_length=2, max_length=4)],
    server: Annotated[str, Query(min_length=1, max_length=64)],
    name: Annotated[str, Query(min_length=1, max_length=64)],
    encounter: Annotated[int | None, Query(ge=1, description="Only this encounter's pulls")] = None,
) -> Any:
    """
    A character's recent nights, newest first, each holding only the boss pulls the character took part in
    (optionally only one encounter's). Only reports the caller may read are listed.
    """
    source = _source(region, server, name)
    check_server(servers, source)
    try:
        character = await character_and_listing(request, user, ctx, budget, config, now, source)

        def keep(code: str, meta: dict[str, Any], night: ReportLookup) -> ReportLookup | None:
            actors = character_actor_ids(meta, character.name)
            if not actors:
                return None
            fights = {
                f["id"]
                for f in meta.get("fights") or []
                if isinstance(f.get("id"), int) and actors.intersection(f.get("friendlyPlayers") or [])
            }
            return only_pulls_of(night, fights, encounter)

        reports = list(character.reports.reports[: config.max_reports])
        built = await build_nights(request, user, ctx, budget, lookup, now, reports, keep)
    except BudgetWaitError as wait:
        return budget_response(wait)
    response.headers["Cache-Control"] = "private, no-store"
    return CharacterSearch(
        character=SearchCharacter(
            name=character.name,
            server_name=character.server_name,
            server_slug=character.server_slug or source.server,
            region=character.region or source.region.upper(),
        ),
        encounter_id=encounter,
        nights=built.nights,
        capped=character.reports.more or len(character.reports.reports) > config.max_reports,
        left_out=built.left_out,
    )


__all__ = [
    "router",
]
