"""
api/raids/search_cache.py

What guild and character search keep between requests, so a repeat search spends as little as it can:

- *Identity.* A guild's ID, name, server, region and faction, and a character's ID, name, server and region, are kept
  in the blob store for good, keyed by region, server and the name in lower case (``search/guilds/...``,
  ``search/characters/...``). A "no such guild or character" answer stands ``not_found_max_age_s``.
- *Listings on the app token* hold public reports only, so one is shared by every caller without a WCL sign-in for
  ``listing_max_age_s``, in the blob store (``search/listings/...``): it survives a restart and is shared by every API
  process, for one small object per guild or character.
- *Listings on a user's own token* may hold that user's private reports, so they are kept per user, in memory only,
  for the same window, and never shared.

Everything here lives outside the expiring ``raw/`` prefix. A lock per key (``locked``) makes concurrent searches for
the same thing share one WCL request.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import orjson
from fastapi import Request

from khadbot.api.deps import auth_service
from khadbot.api.views import ApiContext, ApiError
from khadbot.auth.clock import Clock
from khadbot.auth.store import User
from khadbot.config import SearchConfig
from khadbot.storage import BlobStore, join_key
from khadbot.wcl.search import (
    GuildSource,
    ListedReport,
    Listing,
)

CACHE_PREFIX = "search"  # blob store keys: search/guilds/..., search/characters/..., search/listings/...
MAX_CACHED = 512  # user listings kept in memory, least recently used dropped first


@dataclass
class _Entry:
    at: datetime
    value: Any


class _Memo:
    """
    The per-app part of the cache: users' own listings, in memory, and a lock per key, so concurrent searches for one
    thing share a WCL request.
    """

    def __init__(self) -> None:
        self.entries: OrderedDict[tuple[Any, ...], _Entry] = OrderedDict()
        self.locks: dict[tuple[Any, ...], asyncio.Lock] = {}

    def get(self, key: tuple[Any, ...], max_age_s: float, now: datetime) -> Any | None:
        entry = self.entries.get(key)
        if entry is None or now - entry.at >= timedelta(seconds=max_age_s):
            return None
        self.entries.move_to_end(key)
        return entry.value

    def put(self, key: tuple[Any, ...], value: Any, now: datetime) -> None:
        self.entries[key] = _Entry(now, value)
        self.entries.move_to_end(key)
        while len(self.entries) > MAX_CACHED:
            self.entries.popitem(last=False)

    def lock(self, key: tuple[Any, ...]) -> asyncio.Lock:
        lock = self.locks.get(key)
        if lock is None:
            if len(self.locks) > MAX_CACHED:
                for k in [k for k, v in self.locks.items() if not v.locked()]:
                    del self.locks[k]
            lock = self.locks[key] = asyncio.Lock()
        return lock


def _memo(request: Request) -> _Memo:
    memo: _Memo | None = getattr(request.app.state, "search_memo", None)
    if memo is None:
        memo = request.app.state.search_memo = _Memo()
    return memo


class Store:
    """The blob store part of the cache: JSON objects under ``search/``, each stamped with when it was written."""

    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs

    @staticmethod
    def key(*parts: str) -> str:
        return join_key(CACHE_PREFIX, *parts)

    async def get(self, key: str, max_age_s: float | None, now: datetime) -> dict[str, Any] | None:
        """The object at ``key`` written less than ``max_age_s`` ago (None: whatever its age), else None."""
        data = await asyncio.to_thread(self.blobs.get, key)
        if data is None:
            return None
        try:
            value = orjson.loads(data)
            at = datetime.fromisoformat(value["at"])
        except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if max_age_s is not None and now - at >= timedelta(seconds=max_age_s):
            return None
        return value  # type: ignore[no-any-return]

    async def put(self, key: str, value: dict[str, Any], now: datetime) -> None:
        await asyncio.to_thread(self.blobs.put, key, orjson.dumps({**value, "at": now.isoformat()}))


def store(ctx: ApiContext) -> Store:
    if ctx.raw is None:
        raise ApiError(503, "search_unavailable", "Search has no cache to use.")
    return Store(ctx.raw.blobs)


def name_key(source: GuildSource) -> tuple[str, str, str]:
    return source.region, source.server, quote(source.name.casefold(), safe="") + ".json"


async def locked[T](
    request: Request, key: tuple[Any, ...], load: Callable[[], Awaitable[T | None]], make: Callable[[], Awaitable[T]]
) -> T:
    """``load()``'s answer, else ``make()``'s, with one ``make`` at a time per key (a second caller loads its answer)."""
    if (hit := await load()) is not None:
        return hit
    async with _memo(request).lock(key):
        if (hit := await load()) is not None:
            return hit
        return await make()


async def own_token(request: Request, user: User | None) -> bool:
    """Whether a search for ``user`` lists on their own WCL token (their listing may then hold private reports)."""
    service = auth_service(request)
    return service is not None and user is not None and await service.tokens.has_tokens(user.id)


def _listing_value(listing: Listing) -> dict[str, Any]:
    return {"reports": [asdict(r) for r in listing.reports], "more": listing.more}


def _listing_of(value: dict[str, Any]) -> Listing:
    return Listing(tuple(ListedReport(**r) for r in value.get("reports") or []), bool(value.get("more")))


async def cached_listing(
    request: Request, ctx: ApiContext, config: SearchConfig, now: Clock, kind: str, owner: int, user: User | None,
    shared: bool,
) -> Listing | None:  # fmt: skip
    """A listing still inside ``listing_max_age_s``: the shared one (blob store) or ``user``'s own (memory)."""
    if shared:
        value = await store(ctx).get(Store.key("listings", kind, f"{owner}.json"), config.listing_max_age_s, now())
        return _listing_of(value) if value is not None else None
    assert user is not None
    hit: Listing | None = _memo(request).get((f"{kind}-listing", user.id, owner), config.listing_max_age_s, now())
    return hit


async def keep_listing(
    request: Request, ctx: ApiContext, now: Clock, kind: str, owner: int, user: User | None, shared: bool,
    listing: Listing,
) -> None:  # fmt: skip
    if shared:
        await store(ctx).put(Store.key("listings", kind, f"{owner}.json"), _listing_value(listing), now())
    else:
        assert user is not None
        _memo(request).put((f"{kind}-listing", user.id, owner), listing, now())


async def cached_identity(
    ctx: ApiContext, config: SearchConfig, now: Clock, key: str, not_found: Callable[[], ApiError]
) -> dict[str, Any] | None:
    """
    A cached identity: the stored one (it stands for good), None when there is none or a stale "not found".

    Raises:
        ApiError: ``not_found`` while a cached "not found" stands (``not_found_max_age_s``).
    """
    value = await store(ctx).get(key, None, now())
    if value is None:
        return None
    if value.get("found"):
        identity: dict[str, Any] = value["identity"]
        return identity
    if now() - datetime.fromisoformat(value["at"]) < timedelta(seconds=config.not_found_max_age_s):
        raise not_found()
    return None


__all__ = [
    "MAX_CACHED",
    "Store",
    "cached_identity",
    "cached_listing",
    "keep_listing",
    "locked",
    "name_key",
    "own_token",
    "store",
]
