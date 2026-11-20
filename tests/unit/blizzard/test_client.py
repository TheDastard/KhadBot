"""
tests/unit/blizzard/test_client.py

The Blizzard client: the client-credentials token and its refresh, an achievement icon URL, the typed errors, and
missing credentials. HTTP is mocked with respx; the credentials and tokens are obviously fake values, and the
response shapes are the probed ones (``docs/game-art.md``).
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from khadbot.blizzard import (
    BlizzardAPIError,
    BlizzardAuthError,
    BlizzardClient,
    BlizzardNotFoundError,
    BlizzardRateLimitedError,
    BlizzardUnavailableError,
)
from khadbot.blizzard.client import TOKEN_URL
from khadbot.config import BlizzardConfig
from khadbot.errors import ConfigurationError

SECRET = "fake-secret-value"
MEDIA_URL = "https://us.api.blizzard.com/data/wow/media/achievement/63476"
ICON = "https://render.worldofwarcraft.com/us/icons/56/7966624.jpg"
MEDIA = {"assets": [{"key": "icon", "value": ICON, "file_data_id": 7966624}], "id": 63476}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def token_response(token: str = "tok-1", expires_in: int = 86399) -> httpx.Response:
    return httpx.Response(200, json={"access_token": token, "token_type": "bearer", "expires_in": expires_in})


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(clock: Clock) -> BlizzardClient:
    return BlizzardClient("fake-id", SECRET, clock=clock)


@respx.mock
async def test_icon_url_with_token_namespace_and_locale(client: BlizzardClient):
    token = respx.post(TOKEN_URL).mock(return_value=token_response())
    media = respx.get(MEDIA_URL).mock(return_value=httpx.Response(200, json=MEDIA))
    async with client:
        assert await client.achievement_icon_url(63476) == ICON
    basic = base64.b64encode(f"fake-id:{SECRET}".encode()).decode()
    assert token.calls[0].request.headers["Authorization"] == f"Basic {basic}"
    assert token.calls[0].request.content == b"grant_type=client_credentials"
    request = media.calls[0].request
    assert request.headers["Authorization"] == "Bearer tok-1"
    assert request.headers["Battlenet-Namespace"] == "static-us"
    assert request.url.params["locale"] == "en_US"


@respx.mock
async def test_region_picks_host_and_namespace(clock: Clock):
    respx.post(TOKEN_URL).mock(return_value=token_response())
    route = respx.get("https://eu.api.blizzard.com/data/wow/media/achievement/1").mock(
        return_value=httpx.Response(200, json=MEDIA)
    )
    async with BlizzardClient("fake-id", SECRET, region="eu", clock=clock) as eu:
        await eu.achievement_icon_url(1)
    assert route.calls[0].request.headers["Battlenet-Namespace"] == "static-eu"


@respx.mock
async def test_no_icon_asset_is_none(client: BlizzardClient):
    respx.post(TOKEN_URL).mock(return_value=token_response())
    respx.get(MEDIA_URL).mock(
        return_value=httpx.Response(200, json={"assets": [{"key": "other", "value": "x"}], "id": 63476})
    )
    async with client:
        assert await client.achievement_icon_url(63476) is None


@respx.mock
async def test_token_is_reused_then_refreshed_before_it_expires(client: BlizzardClient, clock: Clock):
    token = respx.post(TOKEN_URL).mock(side_effect=[token_response("tok-1", 1000), token_response("tok-2", 1000)])
    media = respx.get(MEDIA_URL).mock(return_value=httpx.Response(200, json=MEDIA))
    async with client:
        await client.achievement_icon_url(63476)
        clock.now += 600  # inside the 1000 s lifetime less the refresh buffer
        await client.achievement_icon_url(63476)
        assert token.call_count == 1
        clock.now += 200  # past lifetime minus the buffer, though not yet expired
        await client.achievement_icon_url(63476)
    assert token.call_count == 2
    assert [c.request.headers["Authorization"] for c in media.calls] == ["Bearer tok-1", "Bearer tok-1", "Bearer tok-2"]


@respx.mock
async def test_a_401_takes_a_fresh_token_and_retries_once(client: BlizzardClient):
    token = respx.post(TOKEN_URL).mock(side_effect=[token_response("tok-1"), token_response("tok-2")])
    media = respx.get(MEDIA_URL).mock(side_effect=[httpx.Response(401), httpx.Response(200, json=MEDIA)])
    async with client:
        assert await client.achievement_icon_url(63476) == ICON
    assert token.call_count == 2
    assert media.calls[1].request.headers["Authorization"] == "Bearer tok-2"


@respx.mock
async def test_404_is_typed(client: BlizzardClient):
    respx.post(TOKEN_URL).mock(return_value=token_response())
    respx.get(MEDIA_URL).mock(return_value=httpx.Response(404, json={"code": 404}))
    async with client:
        with pytest.raises(BlizzardNotFoundError) as info:
            await client.achievement_icon_url(63476)
    assert info.value.status == 404


@respx.mock
async def test_429_carries_retry_after(client: BlizzardClient):
    respx.post(TOKEN_URL).mock(return_value=token_response())
    respx.get(MEDIA_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "2"}))
    async with client:
        with pytest.raises(BlizzardRateLimitedError) as info:
            await client.achievement_icon_url(63476)
    assert info.value.retry_after_s == 2.0


@respx.mock
async def test_unreachable_api_and_token_host(client: BlizzardClient):
    respx.post(TOKEN_URL).mock(return_value=token_response())
    respx.get(MEDIA_URL).mock(side_effect=httpx.ConnectError("boom"))
    async with client:
        with pytest.raises(BlizzardUnavailableError):
            await client.achievement_icon_url(63476)
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    async with BlizzardClient("fake-id", SECRET) as fresh:
        with pytest.raises(BlizzardUnavailableError):
            await fresh.achievement_icon_url(63476)


@respx.mock
async def test_server_error_and_bad_body_are_api_errors(client: BlizzardClient):
    respx.post(TOKEN_URL).mock(return_value=token_response())
    respx.get(MEDIA_URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"id": 1})])
    async with client:
        with pytest.raises(BlizzardAPIError):
            await client.achievement_icon_url(63476)
        with pytest.raises(BlizzardAPIError):
            await client.achievement_icon_url(63476)


@respx.mock
async def test_refused_token_never_leaks_the_secret(client: BlizzardClient):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))
    async with client:
        with pytest.raises(BlizzardAuthError) as info:
            await client.achievement_icon_url(63476)
    assert "invalid_client" in str(info.value)
    assert SECRET not in str(info.value)
    assert SECRET not in repr(client)


@respx.mock
async def test_network_error_message_names_only_the_exception_class(client: BlizzardClient):
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError(f"failed with {SECRET}"))
    async with client:
        with pytest.raises(BlizzardUnavailableError) as info:
            await client.achievement_icon_url(63476)
    assert SECRET not in str(info.value)


def test_missing_credentials_mean_no_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BLIZZARD_CLIENT_ID", "fake-id")
    monkeypatch.delenv("BLIZZARD_CLIENT_SECRET", raising=False)
    config = BlizzardConfig()
    assert not config.enabled
    assert BlizzardClient.from_config(config) is None
    with pytest.raises(ConfigurationError):
        BlizzardClient("", "")


def test_config_reads_both_names_and_region(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BLIZZARD_CLIENT_ID", "fake-id")
    monkeypatch.setenv("BLIZZARD_CLIENT_SECRET", SECRET)
    monkeypatch.delenv("BLIZZARD_REGION", raising=False)
    config = BlizzardConfig()
    assert config.enabled and config.region == "us"
    assert SECRET not in repr(config)
    assert isinstance(BlizzardClient.from_config(config), BlizzardClient)
    monkeypatch.setenv("BLIZZARD_REGION", "cn")
    with pytest.raises(ValueError, match="BLIZZARD_REGION"):
        BlizzardConfig()
