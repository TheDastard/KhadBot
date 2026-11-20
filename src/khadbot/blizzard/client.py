"""
blizzard/client.py

An async client for the one Blizzard Game Data API call KhadBot makes: an achievement's media, whose ``icon`` asset is
a boss thumbnail's image URL. It owns the OAuth client-credentials token and refreshes it before it expires.

Where each value comes from (probed live against namespace ``static-12.1.5_70077-us``, ``docs/game-art.md``):

- ``TOKEN_URL`` with ``grant_type=client_credentials`` and the client ID and secret as HTTP basic auth; the response
  carries ``access_token`` and ``expires_in`` (86399 seconds).
- ``GET https://{region}.api.blizzard.com/data/wow/media/achievement/{id}`` with the header
  ``Battlenet-Namespace: static-{region}`` and ``locale=en_US`` answers ``{"assets": [{"key": "icon", "value":
  "https://render.worldofwarcraft.com/..."}], "id": ...}``. Only ``key`` and ``value`` are read.

Nothing here logs or formats the client secret or a token: errors carry the HTTP status and Blizzard's short
``error`` code only, and a network failure names only the exception class.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from types import TracebackType

import httpx

from khadbot.config import BlizzardConfig
from khadbot.errors import ConfigurationError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOKEN_URL = "https://oauth.battle.net/token"
API_HOST = "https://{region}.api.blizzard.com"
ACHIEVEMENT_MEDIA_PATH = "/data/wow/media/achievement/{achievement_id}"
LOCALE = "en_US"

# Refresh a token this long before it expires, so a request never carries one that lapses in flight.
TOKEN_EXPIRY_BUFFER_SECONDS = 300
# Used when the token response names no lifetime; short, so the next request asks again soon.
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600
REQUEST_TIMEOUT_SECONDS = 15.0

# Blizzard's OAuth ``error`` field is a short code such as ``invalid_client``; anything else is not repeated.
_ERROR_CODE = re.compile(r"^[a-z_]{1,40}$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BlizzardError(Exception):
    """
    Base of every Blizzard client error. Messages never contain the client secret or a token.

    Attributes:
        status: The HTTP status, or None when no response arrived.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class BlizzardAuthError(BlizzardError):
    """The token request failed: bad credentials, a refused grant or a response with no access token."""


class BlizzardNotFoundError(BlizzardError):
    """The API has no such achievement media (HTTP 404)."""


class BlizzardRateLimitedError(BlizzardError):
    """The API answered 429. ``retry_after_s`` is its ``Retry-After`` in seconds when it sent one."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message, status=429)
        self.retry_after_s = retry_after_s


class BlizzardUnavailableError(BlizzardError):
    """The API or the token host could not be reached (network error or timeout)."""


class BlizzardAPIError(BlizzardError):
    """Any other unexpected answer: a 5xx, another 4xx, or a body that is not the documented shape."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class BlizzardClient:
    """
    Resolves achievement icon URLs on the maintainer's Blizzard client credentials.

    Build it with ``from_config``, which returns None when no credentials are set ("no Blizzard art"). Use it as an
    async context manager, or call ``aclose``.

    Args:
        client_id: The Blizzard API client's ID.
        client_secret: The client's secret.
        region: ``us``, ``eu``, ``kr`` or ``tw``; picks the API host and the ``static-{region}`` namespace.
        http: An ``httpx.AsyncClient`` to use (tests); the client then leaves closing it to the caller.
        clock: Seconds on a monotonic clock, replaced in tests.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        region: str = "us",
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not client_id or not client_secret:
            raise ConfigurationError("The Blizzard client needs BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET.")
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = API_HOST.format(region=region)
        self._namespace = f"static-{region}"
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @classmethod
    def from_config(cls, config: BlizzardConfig, *, http: httpx.AsyncClient | None = None) -> BlizzardClient | None:
        """The client for ``config``, or None when its credentials are not both set."""
        if not config.enabled:
            return None
        return cls(config.client_id, config.client_secret, region=config.region, http=http)

    def __repr__(self) -> str:
        return f"BlizzardClient(namespace={self._namespace!r})"

    async def __aenter__(self) -> BlizzardClient:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client this object created; one passed in is left to its owner."""
        if self._owns_http:
            await self._http.aclose()

    # ---------------------------------------------------------------------
    # Public
    # ---------------------------------------------------------------------

    async def achievement_icon_url(self, achievement_id: int) -> str | None:
        """
        The image URL of an achievement's ``icon`` asset.

        Returns:
            The URL (on ``render.worldofwarcraft.com``), or None when the media lists no ``icon`` asset.

        Raises:
            BlizzardNotFoundError: The API has no media for this achievement.
            BlizzardRateLimitedError: The API answered 429.
            BlizzardUnavailableError: The API could not be reached.
            BlizzardAuthError: No token could be obtained.
            BlizzardAPIError: Any other answer, including a body that is not the documented shape.
        """
        achievement_id = int(achievement_id)
        path = ACHIEVEMENT_MEDIA_PATH.format(achievement_id=achievement_id)
        resp = await self._get(path)
        if resp.status_code == 401:
            # The token was revoked or lapsed early: take a fresh one and ask once more.
            self._token = None
            resp = await self._get(path)
        self._raise_for_status(resp, f"achievement {achievement_id}")
        try:
            body = resp.json()
        except ValueError:
            raise BlizzardAPIError("Blizzard's achievement media is not JSON", status=resp.status_code) from None
        assets = body.get("assets") if isinstance(body, dict) else None
        if not isinstance(assets, list):
            raise BlizzardAPIError("Blizzard's achievement media has no assets list", status=resp.status_code)
        for asset in assets:
            if isinstance(asset, dict) and asset.get("key") == "icon":
                value = asset.get("value")
                return value if isinstance(value, str) and value else None
        return None

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    async def _get(self, path: str) -> httpx.Response:
        token = await self._access_token()
        try:
            return await self._http.get(
                self._base_url + path,
                params={"locale": LOCALE},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Battlenet-Namespace": self._namespace,
                    "Accept": "application/json",
                },
            )
        except httpx.RequestError as exc:
            raise BlizzardUnavailableError(f"Blizzard API unreachable: {type(exc).__name__}") from None

    @staticmethod
    def _raise_for_status(resp: httpx.Response, what: str) -> None:
        status = resp.status_code
        if status == 200:
            return
        if status == 404:
            raise BlizzardNotFoundError(f"Blizzard has no media for {what}", status=404)
        if status == 429:
            raw = resp.headers.get("Retry-After", "")
            raise BlizzardRateLimitedError(
                "Blizzard rate limit reached (HTTP 429)", retry_after_s=float(raw) if raw.isdigit() else None
            )
        raise BlizzardAPIError(f"Blizzard answered HTTP {status} for {what}", status=status)

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token is not None and self._clock() < self._token_expires_at:
                return self._token
            self._token, self._token_expires_at = await self._request_token()
            return self._token

    async def _request_token(self) -> tuple[str, float]:
        try:
            resp = await self._http.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self._client_id, self._client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.RequestError as exc:
            raise BlizzardUnavailableError(f"Blizzard token host unreachable: {type(exc).__name__}") from None
        if resp.status_code != 200:
            error = _error_code(resp)
            detail = f", error={error}" if error else ""
            raise BlizzardAuthError(
                f"Blizzard refused the token request: HTTP {resp.status_code}{detail}", status=resp.status_code
            )
        try:
            body = resp.json()
        except ValueError:
            raise BlizzardAuthError("Blizzard's token response is not JSON", status=200) from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise BlizzardAuthError("Blizzard's token response has no access token", status=200)
        expires_in = body.get("expires_in")
        lifetime = (
            float(expires_in)
            if isinstance(expires_in, int | float) and expires_in > 0
            else DEFAULT_TOKEN_LIFETIME_SECONDS
        )
        return token, self._clock() + max(lifetime - TOKEN_EXPIRY_BUFFER_SECONDS, 0.0)


def _error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and _ERROR_CODE.match(error) else None
