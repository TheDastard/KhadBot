"""
auth/service.py

``AuthService``: the sign-in flow end to end, plus the session, token and access services it owns.

Sign-in, step by step:

1. ``start_login`` makes a PKCE pair, an OAuth ``state`` and a browser nonce. The state's digest, the encrypted
   verifier and the nonce's digest are stored server-side for ``login_timeout_s``; the nonce goes to the browser in
   a short-lived cookie; the browser is sent to WCL's authorize page.
2. ``finish_login`` takes the state out of the store (so a replayed callback finds nothing), checks that the
   browser's nonce matches (so an attacker's callback link can't sign a victim in to the attacker's account), trades
   the code for tokens with the verifier, reads the WCL user, creates or updates the KhadBot user, stores the
   encrypted tokens and starts a session.

Hosted only (``tokens`` needs ``cryptography``). Messages and logs carry user IDs, never a token, code or cookie.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import httpx

from khadbot.auth.access import ReportAccess
from khadbot.auth.clock import Clock, utc_now
from khadbot.auth.errors import LoginError
from khadbot.auth.sessions import SessionManager, digest, new_secret
from khadbot.auth.store import AuthStore, LoginState, User
from khadbot.auth.tokens import TokenCipher, TokenDecryptError, WclTokens
from khadbot.config import AuthConfig, is_local_http
from khadbot.errors import ConfigurationError
from khadbot.wcl.client import WarcraftLogsAPIError, WarcraftLogsClient
from khadbot.wcl.oauth import (
    WclGrantRejectedError,
    WclIdentityError,
    WclOAuthError,
    authorize_url,
    current_user,
    exchange_code,
    make_pkce,
)

logger = logging.getLogger(__name__)

MAX_NEXT_PATH = 512


@dataclass(frozen=True)
class LoginStart:
    """Where to send the browser, and the nonce to put in its login cookie."""

    authorize_url: str
    browser_nonce: str


@dataclass(frozen=True)
class LoginResult:
    """A finished sign-in: the new session's cookie value, the user, and where to send the browser."""

    session_id: str
    user: User
    redirect_to: str


def safe_next_path(value: str | None) -> str:
    """``value`` when it is a plain path on this site (``/reports/abc``), else ``/``. Prevents open redirects."""
    if not value or len(value) > MAX_NEXT_PATH or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(c) < 0x21 or ord(c) == 0x7F for c in value):
        return "/"
    return value


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AuthService:
    """
    Sign-in with Warcraft Logs, sessions, WCL tokens and report access over one ``AuthStore``.

    Args:
        store: Persistence (``MemoryAuthStore`` in tests, ``pg.PostgresAuthStore`` hosted).
        config: The sign-in settings.
        client_id: The WCL API client's ID.
        client_secret: Its secret (WCL refuses the code exchange without it).
        http: Builds the HTTP client for token requests.
        wcl_client: Builds an unentered ``WarcraftLogsClient`` for GraphQL (identity and access checks).
        clock: The current UTC time.

    Raises:
        ConfigurationError: No token key, an invalid key, or a plain-HTTP redirect URL that isn't localhost.
    """

    def __init__(
        self,
        store: AuthStore,
        config: AuthConfig,
        *,
        client_id: str,
        client_secret: str,
        http: Callable[[], httpx.AsyncClient] | None = None,
        wcl_client: Callable[[], WarcraftLogsClient] | None = None,
        clock: Clock = utc_now,
    ) -> None:
        if not config.redirect_uri.startswith("https://") and not is_local_http(config.redirect_uri):
            raise ConfigurationError(
                "KHADBOT_WCL_REDIRECT_URI must be https:// (plain http is allowed only for localhost)."
            )
        if not client_id or not client_secret:
            raise ConfigurationError("Hosted sign-in needs WARCRAFTLOGS_CLIENT_ID and WARCRAFTLOGS_CLIENT_SECRET.")
        self.config = config
        self.store = store
        self._cipher = TokenCipher(config.token_keys)
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or (lambda: httpx.AsyncClient(timeout=30.0))
        self._wcl_client = wcl_client or (lambda: WarcraftLogsClient(client_id=client_id, client_secret=client_secret))
        self._clock = clock
        self.tokens = WclTokens(
            store, self._cipher, client_id=client_id, client_secret=client_secret, http=self._http, clock=clock
        )
        self.sessions = SessionManager(
            store,
            idle=timedelta(hours=config.session_idle_hours),
            max_age=timedelta(days=config.session_max_days),
            clock=clock,
        )
        self.access = ReportAccess(
            store,
            self.tokens,
            self._wcl_client,
            recheck=timedelta(seconds=config.access_recheck_s),
            denied_recheck=timedelta(seconds=config.access_denied_recheck_s),
            clock=clock,
        )

    def __repr__(self) -> str:
        return "AuthService(<redacted>)"

    # ------------------------------------------------------------------
    # Sign-in
    # ------------------------------------------------------------------

    async def start_login(self, next_path: str | None = None) -> LoginStart:
        """Record a new sign-in attempt and return WCL's authorize URL and the browser nonce."""
        verifier, challenge = make_pkce()
        state, nonce = new_secret(), new_secret()
        now = self._clock()
        record = LoginState(
            state_hash=digest(state),
            verifier_enc=self._cipher.encrypt(verifier),
            browser_hash=digest(nonce),
            next_path=safe_next_path(next_path),
            expires_at=now + timedelta(seconds=self.config.login_timeout_s),
        )
        await asyncio.to_thread(self.store.delete_expired_login_states, now)
        await asyncio.to_thread(self.store.put_login_state, record)
        url = authorize_url(
            client_id=self._client_id,
            redirect_uri=self.config.redirect_uri,
            challenge=challenge,
            state=state,
            scope=self.config.wcl_scope,
        )
        return LoginStart(authorize_url=url, browser_nonce=nonce)

    async def finish_login(
        self, *, state: str | None, code: str | None, error: str | None, browser_nonce: str | None
    ) -> LoginResult:
        """
        Complete a sign-in from WCL's callback parameters and the browser's login cookie.

        Raises:
            LoginError: ``invalid_state`` (missing, unknown, expired, replayed, or from another browser),
                ``wcl_denied`` (the user declined or WCL reported an error) or ``sign_in_failed`` (the exchange or
                identity lookup failed).
        """
        if not state:
            raise LoginError("The sign-in response has no state.", code="invalid_state")
        record = await asyncio.to_thread(self.store.take_login_state, digest(state), self._clock())
        if record is None:
            raise LoginError(
                "This sign-in link is unknown, expired or already used. Start again.", code="invalid_state"
            )
        if not browser_nonce or not hmac.compare_digest(record.browser_hash, digest(browser_nonce)):
            raise LoginError("This sign-in was started in another browser. Start again.", code="invalid_state")
        if error:
            raise LoginError("Warcraft Logs did not grant the sign-in.", code="wcl_denied")
        if not code:
            raise LoginError("The sign-in response has no authorization code.", code="sign_in_failed")
        try:
            verifier = self._cipher.decrypt(record.verifier_enc)
        except TokenDecryptError:
            raise LoginError("The sign-in could not be completed. Start again.", code="sign_in_failed") from None
        try:
            async with self._http() as http:
                tokens = await exchange_code(
                    http,
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    code=code,
                    verifier=verifier,
                    redirect_uri=self.config.redirect_uri,
                )
            async with self._wcl_client() as wcl:
                wcl_user = await current_user(wcl, tokens.access_token.get_secret_value())
        except WclGrantRejectedError as exc:
            logger.info("WCL refused a sign-in code exchange (%s)", exc.error)
            raise LoginError("Warcraft Logs refused the sign-in. Start again.", code="sign_in_failed") from None
        except (WclOAuthError, WclIdentityError, WarcraftLogsAPIError) as exc:
            logger.warning("A WCL sign-in failed: %s", type(exc).__name__)
            raise LoginError(
                "Warcraft Logs could not complete the sign-in. Try again.", code="sign_in_failed"
            ) from None

        user = await asyncio.to_thread(self.store.upsert_user, wcl_user.id, wcl_user.name, self._clock())
        await self.tokens.save(user.id, tokens)
        session_id = await self.sessions.create(user.id)
        logger.info("User %s signed in with Warcraft Logs", user.id)
        return LoginResult(session_id=session_id, user=user, redirect_to=self._post_login(record.next_path))

    async def logout(self, session_id: str | None) -> None:
        """End the session. The user's WCL tokens stay, so their queued jobs can still run."""
        await self.sessions.revoke(session_id)

    async def current_user(self, session_id: str | None) -> User | None:
        """The user a session cookie belongs to, or None."""
        return await self.sessions.resolve(session_id)

    def _post_login(self, next_path: str) -> str:
        base = self.config.post_login_url or "/"
        if next_path == "/":
            return base
        return base.rstrip("/") + next_path


__all__ = ["AuthService", "LoginResult", "LoginStart", "safe_next_path"]
