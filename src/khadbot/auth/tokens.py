"""
auth/tokens.py

A signed-in user's Warcraft Logs tokens: stored Fernet-encrypted (``TokenCipher``, a MultiFernet so keys can
rotate), handed out by ``WclTokens.access_token`` and refreshed when they expire, under the store's per-user lock so
two workers never spend the same refresh token.

Hosted only: it needs ``cryptography`` (the ``hosted`` group). Tokens never leave this module except as the return
value of ``access_token``, which callers pass straight to the WCL client.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from khadbot.auth.clock import Clock, utc_now
from khadbot.auth.errors import WclSignInRequiredError, WclUnavailableError
from khadbot.auth.store import AuthStore, StoredTokens
from khadbot.errors import ConfigurationError
from khadbot.wcl.oauth import TokenSet, WclGrantRejectedError, WclOAuthError, refresh_tokens

logger = logging.getLogger(__name__)

# An access token this close to expiry is refreshed before use.
REFRESH_MARGIN = timedelta(seconds=60)

HttpFactory = Callable[[], httpx.AsyncClient]


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


class TokenDecryptError(Exception):
    """A stored value could not be decrypted with any configured key."""


class TokenCipher:
    """
    Fernet encryption with key rotation. ``keys`` are newest first: the first encrypts, every key decrypts, and
    ``rotate`` re-encrypts a value under the first key.

    Raises:
        ConfigurationError: No key is configured, or a key is not a valid Fernet key.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        if not keys:
            raise ConfigurationError(
                "KHADBOT_TOKEN_KEYS is not set: hosted sign-in needs at least one Fernet key to encrypt WCL tokens."
            )
        try:
            self._fernet = MultiFernet([Fernet(key.encode("ascii")) for key in keys])
        except (ValueError, UnicodeEncodeError):
            raise ConfigurationError(
                "KHADBOT_TOKEN_KEYS holds a value that is not a Fernet key (32 url-safe base64-encoded bytes)."
            ) from None

    def __repr__(self) -> str:
        return "TokenCipher(<keys redacted>)"

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError):
            raise TokenDecryptError("A stored token could not be decrypted with the configured keys.") from None

    def rotate(self, value: str) -> str:
        try:
            return self._fernet.rotate(value.encode("ascii")).decode("ascii")
        except (InvalidToken, UnicodeError):
            raise TokenDecryptError("A stored token could not be decrypted with the configured keys.") from None


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class WclTokens:
    """
    Stores, refreshes and hands out users' WCL access tokens.

    Args:
        store: Where the encrypted tokens live.
        cipher: Encrypts and decrypts them.
        client_id: The WCL API client's ID, for the refresh grant.
        client_secret: Its secret.
        http: Builds the HTTP client for token requests (tests pass one respx mocks).
        clock: The current UTC time.
    """

    def __init__(
        self,
        store: AuthStore,
        cipher: TokenCipher,
        *,
        client_id: str,
        client_secret: str,
        http: HttpFactory | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or (lambda: httpx.AsyncClient(timeout=30.0))
        self._clock = clock

    def __repr__(self) -> str:
        return "WclTokens(<redacted>)"

    async def save(self, user_id: str, tokens: TokenSet, *, keep_refresh: str | None = None) -> None:
        """
        Store ``tokens`` for ``user_id``. ``keep_refresh`` is the encrypted refresh token to keep when ``tokens``
        brings no new one (a refresh grant that doesn't rotate it).
        """
        now = self._clock()
        refresh = tokens.refresh_token.get_secret_value() if tokens.refresh_token is not None else None
        record = StoredTokens(
            user_id=user_id,
            refresh_enc=self._cipher.encrypt(refresh) if refresh else keep_refresh,
            access_enc=self._cipher.encrypt(tokens.access_token.get_secret_value()),
            access_expires_at=now + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None,
            updated_at=now,
        )
        await asyncio.to_thread(self._store.put_tokens, record)

    async def forget(self, user_id: str) -> None:
        """Delete the user's stored tokens."""
        await asyncio.to_thread(self._store.delete_tokens, user_id)

    async def has_tokens(self, user_id: str) -> bool:
        return await asyncio.to_thread(self._store.get_tokens, user_id) is not None

    async def access_token(self, user_id: str, *, force_refresh: bool = False) -> str:
        """
        A usable access token for ``user_id``, refreshed first if it is expired (or ``force_refresh``, after WCL
        rejected it).

        Raises:
            WclSignInRequiredError: No tokens are stored, they can't be decrypted, or WCL refused the refresh token
                (the stored tokens are then deleted).
            WclUnavailableError: The refresh failed for a reason that may pass (network, WCL server error).
        """
        stored = await asyncio.to_thread(self._store.get_tokens, user_id)
        if stored is None:
            raise WclSignInRequiredError("This user has not signed in to Warcraft Logs.")
        if not force_refresh and self._fresh(stored):
            return self._decrypt(stored.access_enc)
        async with self._store.refresh_lock(user_id):
            # Another worker may have refreshed while this one waited for the lock.
            current = await asyncio.to_thread(self._store.get_tokens, user_id)
            if current is None:
                raise WclSignInRequiredError("This user has not signed in to Warcraft Logs.")
            if self._fresh(current) and (not force_refresh or current.access_enc != stored.access_enc):
                return self._decrypt(current.access_enc)
            return await self._refresh(current)

    async def rotate_keys(self) -> int:
        """Re-encrypt every stored token under the newest key. Returns the number of users rewritten."""
        count = 0
        for user_id in await asyncio.to_thread(self._store.token_user_ids):
            async with self._store.refresh_lock(user_id):
                stored = await asyncio.to_thread(self._store.get_tokens, user_id)
                if stored is None:
                    continue
                rotated = StoredTokens(
                    user_id=user_id,
                    refresh_enc=self._cipher.rotate(stored.refresh_enc) if stored.refresh_enc else None,
                    access_enc=self._cipher.rotate(stored.access_enc),
                    access_expires_at=stored.access_expires_at,
                    updated_at=stored.updated_at,
                )
                await asyncio.to_thread(self._store.put_tokens, rotated)
                count += 1
        return count

    # ------------------------------------------------------------------

    def _fresh(self, stored: StoredTokens) -> bool:
        return stored.access_expires_at is None or stored.access_expires_at - REFRESH_MARGIN > self._clock()

    def _decrypt(self, value: str) -> str:
        try:
            return self._cipher.decrypt(value)
        except TokenDecryptError:
            raise WclSignInRequiredError(
                "The stored Warcraft Logs sign-in can't be read with the configured keys; sign in again."
            ) from None

    async def _refresh(self, stored: StoredTokens) -> str:
        if not stored.refresh_enc:
            raise WclSignInRequiredError("The Warcraft Logs sign-in has expired and can't be renewed; sign in again.")
        refresh = self._decrypt(stored.refresh_enc)
        try:
            async with self._http() as http:
                tokens = await refresh_tokens(
                    http, client_id=self._client_id, client_secret=self._client_secret, refresh_token=refresh
                )
        except WclGrantRejectedError as exc:
            logger.info("WCL refused the refresh token for user %s (%s); deleting it", stored.user_id, exc.error)
            await asyncio.to_thread(self._store.delete_tokens, stored.user_id)
            raise WclSignInRequiredError("The Warcraft Logs sign-in was revoked or expired; sign in again.") from None
        except WclOAuthError as exc:
            logger.warning("WCL token refresh failed for user %s: HTTP %s", stored.user_id, exc.status)
            raise WclUnavailableError(
                "Warcraft Logs could not renew the sign-in just now; try again shortly."
            ) from None
        await self.save(stored.user_id, tokens, keep_refresh=stored.refresh_enc)
        logger.info("Refreshed the WCL access token for user %s", stored.user_id)
        return tokens.access_token.get_secret_value()


__all__ = ["REFRESH_MARGIN", "TokenCipher", "TokenDecryptError", "WclTokens"]
