"""
WarcraftLogs API client.

Handles two OAuth flows:

  1. Client credentials (``/api/v2/client``) — public and unlisted reports.
     Token is fetched lazily from the app's client_id/client_secret pair and
     cached in-memory until expiry.

  2. PKCE user token (``/api/v2/user``) — private reports belonging to a
     specific WarcraftLogs user.  The caller is responsible for obtaining and
     refreshing the user access token (e.g. via the Discord OAuth callback);
     pass it as ``user_token`` to ``query()``.  When a user token is provided
     the client routes the request to the user endpoint automatically.

Typical usage (public report)::

    async with WarcraftLogsClient(client_id="...", client_secret="...") as wcl:
        data = await wcl.query(SOME_QUERY, variables={"code": "abc123"})

Typical usage (private report with PKCE token)::

    async with WarcraftLogsClient(client_id="...", client_secret="...") as wcl:
        data = await wcl.query(
            SOME_QUERY,
            variables={"code": "abc123"},
            user_token="<user-access-token>",
        )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"

# Client-credentials queries hit /client; user-token queries hit /user.
# The schema is identical — only the authorization scope differs.
GRAPHQL_CLIENT_URL = "https://www.warcraftlogs.com/api/v2/client"
GRAPHQL_USER_URL = "https://www.warcraftlogs.com/api/v2/user"

# Shave 60 s off the real expiry to avoid edge-case clock skew.
TOKEN_EXPIRY_BUFFER_SECONDS = 60


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WarcraftLogsAuthError(Exception):
    """Raised when OAuth token acquisition fails."""


class WarcraftLogsAPIError(Exception):
    """Raised when the GraphQL endpoint returns an error response."""

    def __init__(self, message: str, errors: list[dict] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class WarcraftLogsPrivateReportError(WarcraftLogsAPIError):
    """
    Raised when a report requires a user-level PKCE token.

    Callers should catch this and prompt the user to authenticate via the
    WarcraftLogs OAuth flow, then retry with the resulting user token.
    """


# ---------------------------------------------------------------------------
# Token cache (client credentials only — user tokens are caller-managed)
# ---------------------------------------------------------------------------


@dataclass
class _TokenCache:
    access_token: str = ""
    expires_at: float = 0.0  # unix timestamp

    def is_valid(self) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class WarcraftLogsClient:
    """
    Async HTTP client for the WarcraftLogs GraphQL v2 API.

    Parameters
    ----------
    client_id:
        WarcraftLogs application client ID.
    client_secret:
        WarcraftLogs application client secret.
    """

    client_id: str
    client_secret: str
    _token_cache: _TokenCache = field(default_factory=_TokenCache, init=False, repr=False)
    _http: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> WarcraftLogsClient:
        self._http = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Auth — client credentials
    # ------------------------------------------------------------------

    async def _ensure_client_token(self) -> str:
        """Return a valid client-credentials access token, fetching one if needed."""
        if self._token_cache.is_valid():
            return self._token_cache.access_token

        logger.debug("Fetching new WarcraftLogs client-credentials token.")
        assert self._http is not None, "Client must be used as an async context manager."

        try:
            resp = await self._http.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WarcraftLogsAuthError(
                f"Failed to obtain WarcraftLogs token: HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise WarcraftLogsAuthError(f"Network error fetching WarcraftLogs token: {exc}") from exc

        payload = resp.json()
        self._token_cache.access_token = payload["access_token"]
        self._token_cache.expires_at = time.time() + payload.get("expires_in", 3600) - TOKEN_EXPIRY_BUFFER_SECONDS
        logger.debug(
            "WarcraftLogs client token acquired, expires in ~%ds.",
            payload.get("expires_in", 3600),
        )
        return self._token_cache.access_token

    # ------------------------------------------------------------------
    # GraphQL execution
    # ------------------------------------------------------------------

    async def query(
        self,
        gql: str,
        variables: dict[str, Any] | None = None,
        *,
        user_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute a GraphQL query and return the ``data`` field of the response.

        Parameters
        ----------
        gql:
            GraphQL query string.
        variables:
            Optional variable dict passed alongside the query.
        user_token:
            If provided, the request is sent to the ``/user`` endpoint using
            this token instead of the app-level client-credentials token.
            Required for private reports.

        Raises
        ------
        WarcraftLogsAuthError
            Client-credentials token acquisition failed.
        WarcraftLogsPrivateReportError
            The report is private and no ``user_token`` was supplied, or the
            supplied user token does not have access.
        WarcraftLogsAPIError
            Any other GraphQL-level or HTTP error.
        """
        assert self._http is not None, "Client must be used as an async context manager."

        if user_token:
            token = user_token
            endpoint = GRAPHQL_USER_URL
        else:
            token = await self._ensure_client_token()
            endpoint = GRAPHQL_CLIENT_URL

        payload: dict[str, Any] = {"query": gql}
        if variables:
            payload["variables"] = variables

        try:
            resp = await self._http.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WarcraftLogsAPIError(f"WarcraftLogs GraphQL HTTP error: {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise WarcraftLogsAPIError(f"WarcraftLogs network error: {exc}") from exc

        body = resp.json()

        if errors := body.get("errors"):
            first_message = errors[0].get("message", "")
            if "private" in first_message.lower() or "forbidden" in first_message.lower():
                raise WarcraftLogsPrivateReportError(
                    "This WarcraftLogs report is private. "
                    "The log owner needs to authenticate via WarcraftLogs OAuth so "
                    "their user token can be used to access private reports.",
                    errors=errors,
                )
            raise WarcraftLogsAPIError(
                f"WarcraftLogs GraphQL error: {first_message}",
                errors=errors,
            )

        return body.get("data", {})
