"""
api/art.py

The boss art routes, public in every mode (the art is the same for everyone):

- ``GET /v1/art/encounters``: every boss in ``config/registries/boss_art.yaml`` with its portrait URL (when the PNG is in
  the blob store) and its thumbnail URL (when Blizzard answered), plus the attribution line. Cacheable for an hour.
- ``GET /v1/art/portraits/{name}.png``: one stored portrait (``art/portraits/<name>.png``) with a year-long immutable
  cache. The listing puts a hash of the bytes in each portrait URL (``?v=``), so a re-export changes the URL.

The bucket is private, so portraits go out through this route. Thumbnail URLs come from Blizzard's Game Data API
(``khadbot.blizzard``) and are kept here, in memory and per process, for a day (``ThumbnailCache``): there is no table
and no refresh job, and the API terms' 30-day limit holds because a process's cache never lives that long. A failed
lookup leaves that one thumbnail out of the listing, is remembered for a few minutes so Blizzard is not asked on every
request, and never fails the route. Without Blizzard credentials there are no thumbnails at all.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from khadbot.api.boss_art import BossArt, portrait_key
from khadbot.api.schemas import EncounterArt, EncounterArtList, ErrorBody
from khadbot.api.views import ApiError
from khadbot.blizzard import BlizzardError, BlizzardRateLimitedError
from khadbot.storage import BlobStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ATTRIBUTION = (
    "Boss art is the property of Blizzard Entertainment, Inc. KhadBot is not affiliated with or endorsed by Blizzard "
    "Entertainment."
)

LISTING_MAX_AGE_S = 3600
PORTRAIT_CACHE_CONTROL = "public, max-age=31536000, immutable"

THUMBNAIL_TTL_S = 86400  # a resolved URL is kept a day; Blizzard's terms allow 30 days
THUMBNAIL_MISS_TTL_S = 300  # a failed or empty lookup is not repeated for five minutes
THUMBNAIL_RATE_LIMIT_MAX_S = 3600  # a Retry-After longer than this is not honoured further
THUMBNAIL_CONCURRENCY = 4  # lookups to Blizzard in flight at once

HASH_TTL_S = 300  # a portrait's hash is re-read this often, so an upload shows within five minutes
HASH_LENGTH = 16

# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------


class IconSource(Protocol):
    """What the thumbnail cache needs of Blizzard's client (``BlizzardClient`` satisfies it)."""

    async def achievement_icon_url(self, achievement_id: int) -> str | None: ...


class ThumbnailCache:
    """
    Achievement icon URLs, resolved lazily through ``source`` and kept in memory.

    A URL is kept for ``ttl_s``. A lookup that fails, or that finds no icon, is kept as a miss for ``miss_ttl_s`` (a
    429's ``Retry-After`` extends that, up to an hour). At most ``concurrency`` lookups run at once, and requests for
    the same achievement share one lookup. Without a ``source`` every URL is None.
    """

    def __init__(
        self,
        source: IconSource | None,
        *,
        ttl_s: float = THUMBNAIL_TTL_S,
        miss_ttl_s: float = THUMBNAIL_MISS_TTL_S,
        concurrency: int = THUMBNAIL_CONCURRENCY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self._ttl_s = ttl_s
        self._miss_ttl_s = miss_ttl_s
        self._clock = clock
        self._slots = asyncio.Semaphore(concurrency)
        self._entries: dict[int, tuple[float, str | None]] = {}
        self._inflight: dict[int, asyncio.Future[str | None]] = {}

    async def urls(self, achievement_ids: Iterable[int]) -> dict[int, str | None]:
        """The icon URL of each achievement, None where there is none. Never raises for a Blizzard failure."""
        ids = list(dict.fromkeys(achievement_ids))
        if self._source is None:
            return dict.fromkeys(ids)
        results = await asyncio.gather(*(self._url(i) for i in ids))
        return dict(zip(ids, results, strict=True))

    async def _url(self, achievement_id: int) -> str | None:
        entry = self._entries.get(achievement_id)
        if entry is not None and self._clock() < entry[0]:
            return entry[1]
        pending = self._inflight.get(achievement_id)
        if pending is None:
            pending = asyncio.ensure_future(self._resolve(achievement_id))
            self._inflight[achievement_id] = pending
            pending.add_done_callback(functools.partial(self._forget, achievement_id))
        return await asyncio.shield(pending)  # one caller going away does not cancel the others' lookup

    def _forget(self, achievement_id: int, _done: asyncio.Future[str | None]) -> None:
        self._inflight.pop(achievement_id, None)

    async def _resolve(self, achievement_id: int) -> str | None:
        assert self._source is not None
        async with self._slots:
            try:
                url = await self._source.achievement_icon_url(achievement_id)
            except BlizzardError as exc:
                wait = self._miss_ttl_s
                if isinstance(exc, BlizzardRateLimitedError) and exc.retry_after_s:
                    wait = max(wait, min(exc.retry_after_s, THUMBNAIL_RATE_LIMIT_MAX_S))
                log.warning("Blizzard thumbnail lookup failed: %s (status %s)", type(exc).__name__, exc.status)
                self._entries[achievement_id] = (self._clock() + wait, None)
                return None
        if not url or not url.startswith("https://"):  # the browser loads this URL, so only a secure one is served
            self._entries[achievement_id] = (self._clock() + self._miss_ttl_s, None)
            return None
        self._entries[achievement_id] = (self._clock() + self._ttl_s, url)
        return url


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArtService:
    """The boss art listing: the registry, the blob store the portraits are in and the thumbnail cache."""

    def __init__(
        self,
        registry: Mapping[int, BossArt],
        blobs: BlobStore,
        thumbnails: ThumbnailCache,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.blobs = blobs
        self.thumbnails = thumbnails
        self._clock = clock
        self._hashes: dict[str, tuple[float, str | None]] = {}
        self.portraits = frozenset(b.portrait for b in registry.values() if b.portrait)

    def portrait_hashes(self) -> dict[str, str | None]:
        """Each registered portrait's content hash, None when its PNG is not in the blob store. Blocking."""
        now = self._clock()
        out: dict[str, str | None] = {}
        for name in sorted(self.portraits):
            cached = self._hashes.get(name)
            if cached is None or now >= cached[0]:
                data = self.blobs.get(portrait_key(name))
                digest = hashlib.sha256(data).hexdigest()[:HASH_LENGTH] if data is not None else None
                cached = (now + HASH_TTL_S, digest)
                self._hashes[name] = cached
            out[name] = cached[1]
        return out

    async def listing(self) -> EncounterArtList:
        """Every registry entry with the art that is available now."""
        hashes, thumbnails = await asyncio.gather(
            run_in_threadpool(self.portrait_hashes),
            self.thumbnails.urls(b.kill_achievement_id for b in self.registry.values() if b.kill_achievement_id),
        )
        encounters = []
        for boss in self.registry.values():
            digest = hashes.get(boss.portrait) if boss.portrait else None
            portrait_url = (
                f"/v1/art/portraits/{quote(boss.portrait)}.png?v={digest}" if boss.portrait and digest else None
            )
            thumbnail_url = thumbnails.get(boss.kill_achievement_id) if boss.kill_achievement_id else None
            encounters.append(
                EncounterArt(
                    encounter_id=boss.encounter_id,
                    name=boss.name,
                    portrait_url=portrait_url,
                    thumbnail_url=thumbnail_url,
                )
            )
        return EncounterArtList(attribution=ATTRIBUTION, encounters=encounters)


def get_art(request: Request) -> ArtService:
    """The art service ``create_app`` built."""
    art: ArtService = request.app.state.art
    return art


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/v1/art",
    tags=["art"],
    responses={422: {"model": ErrorBody, "description": "Invalid path parameter (bad_request)"}},
)


@router.get("/encounters", response_model=EncounterArtList)
async def list_encounter_art(request: Request, response: Response) -> EncounterArtList:
    """
    The art of every raid boss in the registry, in registry order, with the attribution line to show beside it. Public:
    the art is the same for everyone, and signed-out visitors see it too. Each boss's `portrait_url` is a path on this
    API's origin (null until its portrait is uploaded) and `thumbnail_url` a Blizzard image URL (null without Blizzard
    credentials or when Blizzard could not answer); the web app draws its fallback for a null. Cacheable for an hour.
    """
    response.headers["Cache-Control"] = f"public, max-age={LISTING_MAX_AGE_S}"
    return await get_art(request).listing()


@router.get(
    "/portraits/{filename}",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}}, "description": "The portrait, a PNG"},
        404: {"model": ErrorBody, "description": "No such portrait in the registry, or not uploaded (art_not_found)"},
    },
)
def get_portrait(filename: str, request: Request) -> Response:
    """
    One boss portrait by file name (`<portrait>.png`, as in a `portrait_url`), the PNG stored in the blob store. Public.
    Served with a year-long immutable cache: the `v` hash in the listing's URL changes when the image does.
    """
    art = get_art(request)
    name = filename.removesuffix(".png") if filename.endswith(".png") else None
    data = art.blobs.get(portrait_key(name)) if name in art.portraits else None
    if data is None:
        raise ApiError(404, "art_not_found", "No such portrait.")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": PORTRAIT_CACHE_CONTROL})


__all__ = [
    "ATTRIBUTION",
    "ArtService",
    "IconSource",
    "ThumbnailCache",
    "get_art",
    "router",
]
