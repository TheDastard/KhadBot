"""
wcl/oauth.py

The Warcraft Logs side of user sign-in: OAuth 2.0 authorization code with PKCE (RFC 7636), the code exchange, the
refresh grant and the signed-in user's identity. It knows nothing about KhadBot users, sessions or storage; the
``auth`` package builds those on top of it.

Where each value comes from:

- ``AUTHORIZE_URL`` and the authorize parameters (``client_id``, ``redirect_uri``, ``response_type=code``,
  ``code_challenge``, ``code_challenge_method=S256``, ``state``): the live PKCE sign-in in
  ``scripts/wcl/probe_wcl_budget.py`` (``docs/wcl-api.md``, "Budget probe results"). That sign-in sent no
  ``scope``, so none is sent unless the caller passes one.
- ``TOKEN_URL`` (``wcl.client``) and the ``authorization_code`` grant with ``code_verifier`` and the client secret in
  the form body: the same probe. WCL refused the exchange without the secret, so the client is confidential.
- The ``refresh_token`` grant: standard OAuth 2.0 (RFC 6749 section 6). WCL returns a refresh token with the code
  exchange, but the refresh grant has not been exercised against WCL yet.
- ``CURRENT_USER_QUERY`` (``userData { currentUser { id name } }``): WCL's published v2 schema, which documents
  ``currentUser`` as working only on the user endpoint. Not yet seen in a recorded response.

Nothing here logs or formats a token. Errors carry the HTTP status and WCL's short ``error`` code only.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from khadbot.wcl.client import TOKEN_URL, WarcraftLogsClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTHORIZE_URL = "https://www.warcraftlogs.com/oauth/authorize"

CURRENT_USER_QUERY = "query CurrentUser { userData { currentUser { id name } } }"

# WCL's OAuth ``error`` field is a short code such as ``invalid_grant``; anything else is not repeated.
_ERROR_CODE = re.compile(r"^[a-z_]{1,40}$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WclOAuthError(Exception):
    """
    A token request failed. The message names the HTTP status and WCL's error code, never a token or a code.

    Attributes:
        status: The HTTP status, or None for a network error.
        error: WCL's OAuth ``error`` code when it sent a recognisable one (``invalid_grant``), else None.
    """

    def __init__(self, message: str, *, status: int | None = None, error: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.error = error


class WclGrantRejectedError(WclOAuthError):
    """WCL refused the grant itself (HTTP 400 or 401): the code or refresh token is spent, expired or revoked."""


class WclIdentityError(Exception):
    """The current-user query returned no user. The message never contains a token."""


# ---------------------------------------------------------------------------
# PKCE and the authorize URL
# ---------------------------------------------------------------------------


def make_pkce() -> tuple[str, str]:
    """
    A fresh PKCE ``(verifier, challenge)`` pair: a 64-byte random verifier and its S256 challenge (RFC 7636).
    """
    verifier = secrets.token_urlsafe(64)
    return verifier, pkce_challenge(verifier)


def pkce_challenge(verifier: str) -> str:
    """The S256 code challenge for ``verifier``: unpadded base64url of its SHA-256."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(*, client_id: str, redirect_uri: str, challenge: str, state: str, scope: str = "") -> str:
    """
    The URL that sends a browser to WCL's sign-in page.

    Args:
        client_id: The WCL API client's ID.
        redirect_uri: Where WCL sends the browser back; it must be registered on the WCL client exactly.
        challenge: The S256 PKCE challenge (``make_pkce``).
        state: An unguessable value the callback must return unchanged.
        scope: Space-separated scopes, sent only when not empty.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if scope:
        params["scope"] = scope
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Token requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenSet:
    """
    What a token request returns. Tokens are ``SecretStr``, so printing or logging a ``TokenSet`` shows asterisks.

    Attributes:
        access_token: The bearer token for ``/api/v2/user``.
        refresh_token: The refresh token, when WCL sent one (a refresh may return a new one; keep the newest).
        expires_in: Seconds the access token lives, when WCL said.
    """

    access_token: SecretStr = field(repr=False)
    refresh_token: SecretStr | None = field(default=None, repr=False)
    expires_in: int | None = None


async def exchange_code(
    http: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> TokenSet:
    """
    Trade an authorization code for tokens (the ``authorization_code`` grant with the PKCE verifier).

    Raises:
        WclGrantRejectedError: WCL refused the code (spent, expired, or the verifier or redirect doesn't match).
        WclOAuthError: Any other failure: network, server error, or a response with no access token.
    """
    return await _token_request(
        http,
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
        "code exchange",
    )


async def refresh_tokens(
    http: httpx.AsyncClient, *, client_id: str, client_secret: str, refresh_token: str
) -> TokenSet:
    """
    Get a new access token with a refresh token (the ``refresh_token`` grant).

    Raises:
        WclGrantRejectedError: WCL refused the refresh token: the user has to sign in again.
        WclOAuthError: Any other failure; the refresh token may still be good, so try again later.
    """
    return await _token_request(
        http,
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        "token refresh",
    )


async def _token_request(http: httpx.AsyncClient, data: dict[str, str], what: str) -> TokenSet:
    try:
        resp = await http.post(TOKEN_URL, data=data, headers={"Accept": "application/json"})
    except httpx.RequestError as exc:
        raise WclOAuthError(f"WCL {what} failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        error = _error_code(resp)
        cls = WclGrantRejectedError if resp.status_code in (400, 401) else WclOAuthError
        detail = f", error={error}" if error else ""
        raise cls(f"WCL refused the {what}: HTTP {resp.status_code}{detail}", status=resp.status_code, error=error)
    try:
        body = resp.json()
    except ValueError:
        raise WclOAuthError(f"WCL {what} returned a body that is not JSON", status=200) from None
    access = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(access, str) or not access:
        raise WclOAuthError(f"WCL {what} returned no access token", status=200)
    refresh = body.get("refresh_token")
    expires_in = body.get("expires_in")
    return TokenSet(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh) if isinstance(refresh, str) and refresh else None,
        expires_in=int(expires_in) if isinstance(expires_in, int | float) and expires_in > 0 else None,
    )


def _error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and _ERROR_CODE.match(error) else None


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WclUser:
    """The signed-in Warcraft Logs account: its numeric ID and display name."""

    id: int
    name: str


async def current_user(client: WarcraftLogsClient, access_token: str) -> WclUser:
    """
    The WCL account ``access_token`` belongs to, from ``userData.currentUser`` on the user endpoint.

    Raises:
        WarcraftLogsUnauthorizedError: WCL rejected the token.
        WarcraftLogsAPIError: Any other API failure.
        WclIdentityError: The response named no user.
    """
    data = await client.query(CURRENT_USER_QUERY, user_token=access_token)
    user = ((data or {}).get("userData") or {}).get("currentUser")
    if not isinstance(user, dict):
        raise WclIdentityError("WCL returned no current user for this token.")
    try:
        return WclUser(id=int(user["id"]), name=str(user["name"]))
    except (KeyError, TypeError, ValueError):
        raise WclIdentityError("WCL's current user has no usable id or name.") from None


__all__ = [
    "AUTHORIZE_URL",
    "CURRENT_USER_QUERY",
    "TokenSet",
    "WclGrantRejectedError",
    "WclIdentityError",
    "WclOAuthError",
    "WclUser",
    "authorize_url",
    "current_user",
    "exchange_code",
    "make_pkce",
    "pkce_challenge",
    "refresh_tokens",
]
