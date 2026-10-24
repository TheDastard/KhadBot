"""
auth/store.py

What sign-in persists, as records and an ``AuthStore`` protocol, plus ``MemoryAuthStore`` for unit tests. The
Postgres store (``auth.pg``) has the same behaviour over the ``0003_auth.sql`` tables.

Nothing secret is stored in the clear: sessions and login states are keyed by the SHA-256 of their random value,
and tokens and PKCE verifiers are stored Fernet-encrypted (``auth.tokens.TokenCipher``). Store methods are
synchronous; the services call them in a worker thread.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal, Protocol

# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

AccessVia = Literal["user", "app"]


@dataclass(frozen=True)
class User:
    """A KhadBot user: one per Warcraft Logs account."""

    id: str
    wcl_user_id: int
    name: str


@dataclass(frozen=True)
class SessionRecord:
    """A signed-in browser. ``id_hash`` is the SHA-256 of the cookie value; the value itself is never stored."""

    id_hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class LoginState:
    """
    A sign-in in progress, between ``/v1/auth/login`` and the callback.

    Attributes:
        state_hash: SHA-256 of the OAuth ``state``.
        verifier_enc: The PKCE verifier, encrypted.
        browser_hash: SHA-256 of the nonce in the browser's login cookie, which binds the callback to that browser.
        next_path: Where to send the browser after signing in (a path on the web app).
        expires_at: After this the state is refused.
    """

    state_hash: str
    verifier_enc: str
    browser_hash: str
    next_path: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredTokens:
    """A user's WCL tokens, both encrypted. ``access_expires_at`` is None when WCL gave no lifetime."""

    user_id: str
    refresh_enc: str | None = field(repr=False)
    access_enc: str = field(repr=False)
    access_expires_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class AccessRecord:
    """A cached answer to "can this subject read this report", with the time WCL was asked."""

    readable: bool
    via: AccessVia | None
    checked_at: datetime


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class AuthStore(Protocol):
    """Persistence for users, sessions, login states, WCL tokens and report access."""

    # Users
    def upsert_user(self, wcl_user_id: int, name: str, now: datetime) -> User:
        """The user for ``wcl_user_id``, created on first sign-in; its name is updated to ``name``."""
        ...

    def get_user(self, user_id: str) -> User | None: ...

    # Login states
    def put_login_state(self, state: LoginState) -> None: ...

    def take_login_state(self, state_hash: str, now: datetime) -> LoginState | None:
        """Remove and return the state, so it can be used once; None when missing or expired."""
        ...

    def delete_expired_login_states(self, now: datetime) -> None: ...

    # Sessions
    def create_session(self, session: SessionRecord) -> None: ...

    def get_session(self, id_hash: str) -> SessionRecord | None: ...

    def touch_session(self, id_hash: str, last_seen_at: datetime, expires_at: datetime) -> None: ...

    def delete_session(self, id_hash: str) -> None: ...

    # WCL tokens
    def put_tokens(self, tokens: StoredTokens) -> None: ...

    def get_tokens(self, user_id: str) -> StoredTokens | None: ...

    def delete_tokens(self, user_id: str) -> None: ...

    def token_user_ids(self) -> list[str]: ...

    def refresh_lock(self, user_id: str) -> AbstractAsyncContextManager[None]:
        """Held while a user's tokens are refreshed, so two workers (or two tasks) never refresh at once."""
        ...

    # Report access
    def get_access(self, user_id: str, report_code: str) -> AccessRecord | None: ...

    def put_access(self, user_id: str, report_code: str, record: AccessRecord) -> None: ...

    def get_public_access(self, report_code: str) -> AccessRecord | None: ...

    def put_public_access(self, report_code: str, record: AccessRecord) -> None: ...


# ---------------------------------------------------------------------------
# In memory
# ---------------------------------------------------------------------------


class MemoryAuthStore:
    """An ``AuthStore`` in process memory: unit tests, and nothing else (it is lost on restart)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.users: dict[str, User] = {}
        self.login_states: dict[str, LoginState] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.tokens: dict[str, StoredTokens] = {}
        self.access: dict[tuple[str, str], AccessRecord] = {}
        self.public_access: dict[str, AccessRecord] = {}
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    def upsert_user(self, wcl_user_id: int, name: str, now: datetime) -> User:
        with self._lock:
            for user in self.users.values():
                if user.wcl_user_id == wcl_user_id:
                    updated = replace(user, name=name)
                    self.users[user.id] = updated
                    return updated
            user = User(id=uuid.uuid4().hex, wcl_user_id=wcl_user_id, name=name)
            self.users[user.id] = user
            return user

    def get_user(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    def put_login_state(self, state: LoginState) -> None:
        with self._lock:
            self.login_states[state.state_hash] = state

    def take_login_state(self, state_hash: str, now: datetime) -> LoginState | None:
        with self._lock:
            state = self.login_states.pop(state_hash, None)
        return state if state is not None and state.expires_at > now else None

    def delete_expired_login_states(self, now: datetime) -> None:
        with self._lock:
            for key in [k for k, s in self.login_states.items() if s.expires_at <= now]:
                del self.login_states[key]

    def create_session(self, session: SessionRecord) -> None:
        with self._lock:
            self.sessions[session.id_hash] = session

    def get_session(self, id_hash: str) -> SessionRecord | None:
        return self.sessions.get(id_hash)

    def touch_session(self, id_hash: str, last_seen_at: datetime, expires_at: datetime) -> None:
        with self._lock:
            if (session := self.sessions.get(id_hash)) is not None:
                self.sessions[id_hash] = replace(session, last_seen_at=last_seen_at, expires_at=expires_at)

    def delete_session(self, id_hash: str) -> None:
        with self._lock:
            self.sessions.pop(id_hash, None)

    def put_tokens(self, tokens: StoredTokens) -> None:
        with self._lock:
            self.tokens[tokens.user_id] = tokens

    def get_tokens(self, user_id: str) -> StoredTokens | None:
        return self.tokens.get(user_id)

    def delete_tokens(self, user_id: str) -> None:
        with self._lock:
            self.tokens.pop(user_id, None)

    def token_user_ids(self) -> list[str]:
        return sorted(self.tokens)

    @asynccontextmanager
    async def refresh_lock(self, user_id: str) -> AsyncIterator[None]:
        with self._lock:
            lock = self._refresh_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            yield

    def get_access(self, user_id: str, report_code: str) -> AccessRecord | None:
        return self.access.get((user_id, report_code))

    def put_access(self, user_id: str, report_code: str, record: AccessRecord) -> None:
        with self._lock:
            self.access[(user_id, report_code)] = record

    def get_public_access(self, report_code: str) -> AccessRecord | None:
        return self.public_access.get(report_code)

    def put_public_access(self, report_code: str, record: AccessRecord) -> None:
        with self._lock:
            self.public_access[report_code] = record


__all__ = [
    "AccessRecord",
    "AccessVia",
    "AuthStore",
    "LoginState",
    "MemoryAuthStore",
    "SessionRecord",
    "StoredTokens",
    "User",
]
