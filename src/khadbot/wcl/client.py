"""
wcl/client.py

WarcraftLogs API client, shared by the coach tools and encounter ingest.

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
import re
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

RATE_LIMIT_QUERY = "query { rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn } }"

# Shave 60 s off the real expiry to avoid edge-case clock skew.
TOKEN_EXPIRY_BUFFER_SECONDS = 60

# ``op`` of the client-credentials token request on its ``wcl.request`` line.
TOKEN_OPERATION = "oauth_token"

_OPERATION_NAME = re.compile(r"^\s*(?:query|mutation)\s+(\w+)")
_FIRST_FIELD = re.compile(r"^\s*(?:query|mutation)?\s*\{\s*(\w+)")


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


class WarcraftLogsRateLimitError(WarcraftLogsAPIError):
    """
    Raised on HTTP 429 or a GraphQL rate-limit error.

    ``retry_after_s`` is the server's ``Retry-After`` hint when it sent one.
    The client never retries on its own; callers own the backoff policy.
    """

    def __init__(self, message: str, errors: list[dict] | None = None, retry_after_s: float | None = None) -> None:
        super().__init__(message, errors)
        self.retry_after_s = retry_after_s


class WarcraftLogsNetworkError(WarcraftLogsAPIError):
    """A request that failed below HTTP: a timeout, a dropped connection or DNS."""


class WarcraftLogsUnauthorizedError(WarcraftLogsAPIError):
    """
    Raised on HTTP 401: the bearer token was rejected, usually because a user access token expired.

    Callers that hold a refresh token can refresh and retry; the client never does.
    """


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
    transport:
        The ``httpx`` transport requests go through. None uses the network, or with ``KHADBOT_WCL_REPLAY`` set the
        process's replay transport (``wcl/replay.py``), so no caller has to ask for replay.
    """

    client_id: str
    client_secret: str
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    _token_cache: _TokenCache = field(default_factory=_TokenCache, init=False, repr=False)
    _http: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> WarcraftLogsClient:
        transport = self.transport
        if transport is None:
            from khadbot.wcl.replay import configured_transport  # imports this module

            transport = configured_transport()
        self._http = httpx.AsyncClient(timeout=30.0, transport=transport)
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

        started = time.perf_counter()
        try:
            resp = await self._http.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log_request(TOKEN_OPERATION, started, 1, exc.response.status_code, failed=True)
            raise WarcraftLogsAuthError(
                f"Failed to obtain WarcraftLogs token: HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            _log_request(TOKEN_OPERATION, started, 1, None, failed=True, reason=type(exc).__name__)
            raise WarcraftLogsAuthError(f"Network error fetching WarcraftLogs token: {exc}") from exc
        _log_request(TOKEN_OPERATION, started, 1, resp.status_code, failed=False)

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
        attempt: int = 1,
    ) -> dict[str, Any]:
        """
        Execute a GraphQL query and return the ``data`` field of the response.

        Each call sends one HTTP request and logs one ``wcl.request`` record: the operation name, HTTP status,
        duration and ``attempt``, at DEBUG, or WARNING when it fails. The query variables, headers and body are never
        logged, since variables can hold the codes of private reports.

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
        attempt:
            Which try of the caller's this is (1-based), for the log record only; the client never retries.

        Raises
        ------
        WarcraftLogsAuthError
            Client-credentials token acquisition failed.
        WarcraftLogsPrivateReportError
            The report is private and no ``user_token`` was supplied, or the
            supplied user token does not have access.
        WarcraftLogsUnauthorizedError
            WCL answered HTTP 401 (the token was rejected or has expired).
        WarcraftLogsNetworkError
            The request failed below HTTP (timeout, dropped connection, DNS). A subclass of
            ``WarcraftLogsAPIError``.
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

        op = operation_name(gql)
        started = time.perf_counter()
        try:
            resp = await self._http.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _log_request(op, started, attempt, exc.response.status_code, failed=True)
            if exc.response.status_code == 401:
                raise WarcraftLogsUnauthorizedError("WarcraftLogs rejected the access token (HTTP 401).") from exc
            if exc.response.status_code == 429:
                raise WarcraftLogsRateLimitError(
                    "WarcraftLogs rate limit hit (HTTP 429).",
                    retry_after_s=_parse_retry_after(exc.response.headers.get("Retry-After")),
                ) from exc
            raise WarcraftLogsAPIError(f"WarcraftLogs GraphQL HTTP error: {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            _log_request(op, started, attempt, None, failed=True, reason=type(exc).__name__)
            raise WarcraftLogsNetworkError(f"WarcraftLogs network error: {exc}") from exc

        body = resp.json()
        errors = body.get("errors")
        _log_request(
            op, started, attempt, resp.status_code, failed=bool(errors), reason="GraphQL error" if errors else ""
        )

        if errors:
            first_message = errors[0].get("message", "")
            if "rate limit" in first_message.lower():
                raise WarcraftLogsRateLimitError(
                    f"WarcraftLogs GraphQL error: {first_message}",
                    errors=errors,
                )
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

    async def rate_limit(self, *, user_token: str | None = None) -> dict[str, Any]:
        """Return ``{limitPerHour, pointsSpentThisHour, pointsResetIn}`` for the app, or for ``user_token``'s user."""
        data = await self.query(RATE_LIMIT_QUERY, user_token=user_token)
        return data.get("rateLimitData", {})


def operation_name(gql: str) -> str:
    """
    A query's label for logs: its operation name (``GetEventsPage``), else the first field an anonymous query selects
    (``rateLimitData``), else ``anonymous``. Never anything from the variables.
    """
    if match := _OPERATION_NAME.match(gql) or _FIRST_FIELD.match(gql):
        return match.group(1)
    return "anonymous"


def _log_request(op: str, started: float, attempt: int, status: int | None, *, failed: bool, reason: str = "") -> None:
    """Write the ``wcl.request`` record of one HTTP request: DEBUG when it succeeded, WARNING when it failed."""
    duration_ms = round((time.perf_counter() - started) * 1000)
    extra: dict[str, Any] = {"event": "wcl.request", "op": op, "duration_ms": duration_ms, "attempt": attempt}
    if status is not None:
        extra["status"] = status
    outcome = str(status) if status is not None else "failed"
    if reason:
        outcome = f"{outcome} ({reason})"
    logger.log(
        logging.WARNING if failed else logging.DEBUG,
        "WCL %s %s in %d ms",
        op,
        outcome,
        duration_ms,
        extra=extra,
    )


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header expressed in seconds; HTTP-date forms are ignored."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
