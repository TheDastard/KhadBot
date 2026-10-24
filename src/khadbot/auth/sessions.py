"""
auth/sessions.py

Browser sessions: an opaque random ID in a cookie, stored only as its SHA-256. A session expires after
``session_idle_hours`` without a request (sliding) and in any case ``session_max_days`` after sign-in.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import timedelta

from khadbot.auth.clock import Clock, utc_now
from khadbot.auth.store import AuthStore, SessionRecord, User

# A session's last-seen time is written at most this often, so reads don't each cost a write.
TOUCH_INTERVAL = timedelta(minutes=5)


def new_secret() -> str:
    """A 256-bit random value, url-safe: session IDs, OAuth states and browser nonces."""
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    """The SHA-256 hex digest by which a secret value is stored and looked up."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionManager:
    """Creates, resolves and revokes sessions."""

    def __init__(self, store: AuthStore, *, idle: timedelta, max_age: timedelta, clock: Clock = utc_now) -> None:
        self._store = store
        self.idle = idle
        self.max_age = max_age
        self._clock = clock

    async def create(self, user_id: str) -> str:
        """Start a session for ``user_id`` and return its ID, the cookie value. Only its digest is stored."""
        session_id = new_secret()
        now = self._clock()
        record = SessionRecord(
            id_hash=digest(session_id),
            user_id=user_id,
            created_at=now,
            expires_at=min(now + self.idle, now + self.max_age),
            last_seen_at=now,
        )
        await asyncio.to_thread(self._store.create_session, record)
        return session_id

    async def resolve(self, session_id: str | None) -> User | None:
        """The signed-in user for a cookie value, extending the session; None for a missing or expired one."""
        if not session_id:
            return None
        id_hash = digest(session_id)
        record = await asyncio.to_thread(self._store.get_session, id_hash)
        now = self._clock()
        if record is None:
            return None
        if record.expires_at <= now or record.created_at + self.max_age <= now:
            await asyncio.to_thread(self._store.delete_session, id_hash)
            return None
        user = await asyncio.to_thread(self._store.get_user, record.user_id)
        if user is None:
            return None
        if now - record.last_seen_at >= TOUCH_INTERVAL:
            expires = min(now + self.idle, record.created_at + self.max_age)
            await asyncio.to_thread(self._store.touch_session, id_hash, now, expires)
        return user

    async def revoke(self, session_id: str | None) -> None:
        """End the session for a cookie value; nothing happens for an unknown one."""
        if session_id:
            await asyncio.to_thread(self._store.delete_session, digest(session_id))


__all__ = ["TOUCH_INTERVAL", "SessionManager", "digest", "new_secret"]
