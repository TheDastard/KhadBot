"""
tests/unit/api/test_art.py

The boss art routes: the listing's portrait and thumbnail URLs present and absent, the day-long thumbnail cache and its
short miss cache, a Blizzard error that leaves one thumbnail out, bounded concurrency, the portrait route's 404s and
cache headers, and the hash in the portrait URL changing with the bytes. No request leaves the process.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from khadbot.api import create_app, openapi_document
from khadbot.api.art import ATTRIBUTION, ThumbnailCache
from khadbot.api.boss_art import BossArt, portrait_key
from khadbot.blizzard import BlizzardAPIError, BlizzardRateLimitedError
from khadbot.storage import LocalBlobStore

PNG = b"\x89PNG fake one"
REGISTRY = {
    1: BossArt(encounter_id=1, name="Alpha", portrait="Brute", kill_achievement_id=11, verified=False),
    2: BossArt(encounter_id=2, name="Beta Two", portrait="Two Words", kill_achievement_id=22, verified=False),
    3: BossArt(encounter_id=3, name="Gamma", portrait=None, kill_achievement_id=None, verified=False),
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class StubIcons:
    def __init__(self, answers: dict[int, object]) -> None:
        self.answers = answers
        self.calls: list[int] = []
        self.running = 0
        self.peak = 0

    async def achievement_icon_url(self, achievement_id: int) -> str | None:
        self.calls.append(achievement_id)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(0)
            answer = self.answers[achievement_id]
            if isinstance(answer, Exception):
                raise answer
            return answer  # type: ignore[return-value]
        finally:
            self.running -= 1


def _client(tmp_path: Path, icons: StubIcons | None) -> TestClient:
    return TestClient(create_app(mode="local", data_dir=tmp_path, boss_art=REGISTRY, icon_source=icons))


def _store(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path.resolve())


def test_listing_has_urls_for_what_is_uploaded_and_resolved(tmp_path):
    _store(tmp_path).put(portrait_key("Brute"), PNG)
    icons = StubIcons(
        {11: "https://render.worldofwarcraft.com/us/icons/56/1.jpg", 22: BlizzardAPIError("x", status=500)}
    )
    response = _client(tmp_path, icons).get("/v1/art/encounters")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=3600"
    body = response.json()
    assert body["attribution"] == ATTRIBUTION
    alpha, beta, gamma = body["encounters"]
    assert alpha["portrait_url"].startswith("/v1/art/portraits/Brute.png?v=")
    assert alpha["thumbnail_url"] == "https://render.worldofwarcraft.com/us/icons/56/1.jpg"
    assert beta == {"encounter_id": 2, "name": "Beta Two", "portrait_url": None, "thumbnail_url": None}
    assert gamma["portrait_url"] is None and gamma["thumbnail_url"] is None


def test_without_blizzard_credentials_there_are_no_thumbnails(tmp_path, monkeypatch):
    monkeypatch.setenv("BLIZZARD_CLIENT_ID", "")
    monkeypatch.setenv("BLIZZARD_CLIENT_SECRET", "")
    body = _client(tmp_path, None).get("/v1/art/encounters").json()
    assert [e["thumbnail_url"] for e in body["encounters"]] == [None, None, None]


def test_the_portrait_is_served_with_a_year_long_immutable_cache(tmp_path):
    _store(tmp_path).put(portrait_key("Two Words"), PNG)
    client = _client(tmp_path, StubIcons({11: None, 22: None}))
    url = next(e["portrait_url"] for e in client.get("/v1/art/encounters").json()["encounters"] if e["portrait_url"])
    assert "Two%20Words.png" in url
    response = client.get(url)
    assert response.status_code == 200 and response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("path", ["Brute.png", "Nobody.png", "Brute", "Brute.jpg"])
def test_unknown_or_unuploaded_portraits_are_404(tmp_path, path):
    response = _client(tmp_path, StubIcons({})).get(f"/v1/art/portraits/{path}")
    assert response.status_code == 404
    assert response.json()["code"] == "art_not_found"


def test_the_hash_changes_with_the_bytes(tmp_path):
    clock = Clock()
    from khadbot.api.art import ArtService

    store = _store(tmp_path)
    service = ArtService(REGISTRY, store, ThumbnailCache(None), clock=clock)
    store.put(portrait_key("Brute"), PNG)
    first = service.portrait_hashes()["Brute"]
    store.put(portrait_key("Brute"), PNG + b"2")
    assert service.portrait_hashes()["Brute"] == first  # kept for a few minutes
    clock.now += 301
    assert service.portrait_hashes()["Brute"] != first
    assert service.portrait_hashes()["Two Words"] is None


async def test_urls_are_kept_a_day_and_misses_a_few_minutes():
    clock = Clock()
    icons = StubIcons({1: "https://x/1.jpg", 2: BlizzardAPIError("down", status=503)})
    cache = ThumbnailCache(icons, clock=clock)
    assert await cache.urls([1, 2]) == {1: "https://x/1.jpg", 2: None}
    assert await cache.urls([1, 2]) == {1: "https://x/1.jpg", 2: None}
    assert icons.calls == [1, 2]  # nothing asked again
    clock.now += 301
    await cache.urls([1, 2])
    assert icons.calls == [1, 2, 2]  # only the miss is retried
    clock.now += 86400
    await cache.urls([1])
    assert icons.calls == [1, 2, 2, 1]


async def test_a_rate_limit_extends_the_miss():
    clock = Clock()
    icons = StubIcons({1: BlizzardRateLimitedError("slow", retry_after_s=1200)})
    cache = ThumbnailCache(icons, clock=clock)
    await cache.urls([1])
    clock.now += 600
    await cache.urls([1])
    assert icons.calls == [1]


async def test_lookups_are_bounded_and_shared():
    icons = StubIcons({i: f"https://x/{i}.jpg" for i in range(10)})
    cache = ThumbnailCache(icons, concurrency=3)
    results = await asyncio.gather(cache.urls(range(10)), cache.urls(range(10)))
    assert results[0] == results[1] and len(icons.calls) == 10
    assert icons.peak <= 3


async def test_a_non_https_url_is_not_served():
    cache = ThumbnailCache(StubIcons({1: "http://x/1.jpg"}))
    assert await cache.urls([1]) == {1: None}


def test_the_contract_shows_both_routes():
    paths = openapi_document()
    assert "/v1/art/encounters" in paths and "/v1/art/portraits/{filename}" in paths
