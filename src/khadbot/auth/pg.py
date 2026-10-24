"""
auth/pg.py

``PostgresAuthStore``: the ``AuthStore`` over the ``0003_auth.sql`` tables, for hosted mode. The per-user refresh
lock is a Postgres advisory lock, so it holds across worker processes, not just within one.

Hosted only: it needs ``psycopg`` and ``psycopg_pool`` (the ``hosted`` group).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import LiteralString

from psycopg import Connection
from psycopg_pool import ConnectionPool

from khadbot.auth.store import AccessRecord, LoginState, SessionRecord, StoredTokens, User

# Advisory-lock namespace for token refreshes (the first key of the two-key form); the second is hashtext(user_id).
REFRESH_LOCK_NAMESPACE = 0x4B424154
_LOCK: LiteralString = "SELECT pg_advisory_lock(%s::integer, hashtext(%s::text))"
_UNLOCK: LiteralString = "SELECT pg_advisory_unlock(%s::integer, hashtext(%s::text))"

# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class PostgresAuthStore:
    """An ``AuthStore`` in Postgres. Every method runs in its own short transaction."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # Users ---------------------------------------------------------------

    def upsert_user(self, wcl_user_id: int, name: str, now: datetime) -> User:
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO users (id, wcl_user_id, name, created_at, last_login_at) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (wcl_user_id) DO UPDATE SET name = EXCLUDED.name, last_login_at = EXCLUDED.last_login_at "
                "RETURNING id, wcl_user_id, name",
                (uuid.uuid4().hex, wcl_user_id, name, now, now),
            ).fetchone()
        assert row is not None
        return User(id=row[0], wcl_user_id=int(row[1]), name=row[2])

    def get_user(self, user_id: str) -> User | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT id, wcl_user_id, name FROM users WHERE id = %s", (user_id,)).fetchone()
        return User(id=row[0], wcl_user_id=int(row[1]), name=row[2]) if row else None

    # Login states --------------------------------------------------------

    def put_login_state(self, state: LoginState) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO oauth_states (state_hash, verifier_enc, browser_hash, next_path, expires_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (state.state_hash, state.verifier_enc, state.browser_hash, state.next_path, state.expires_at),
            )

    def take_login_state(self, state_hash: str, now: datetime) -> LoginState | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "DELETE FROM oauth_states WHERE state_hash = %s "
                "RETURNING state_hash, verifier_enc, browser_hash, next_path, expires_at",
                (state_hash,),
            ).fetchone()
        if row is None or row[4] <= now:
            return None
        return LoginState(state_hash=row[0], verifier_enc=row[1], browser_hash=row[2], next_path=row[3],
                          expires_at=row[4])  # fmt: skip

    def delete_expired_login_states(self, now: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires_at <= %s", (now,))

    # Sessions ------------------------------------------------------------

    def create_session(self, session: SessionRecord) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id_hash, user_id, created_at, expires_at, last_seen_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (session.id_hash, session.user_id, session.created_at, session.expires_at, session.last_seen_at),
            )

    def get_session(self, id_hash: str) -> SessionRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT id_hash, user_id, created_at, expires_at, last_seen_at FROM sessions WHERE id_hash = %s",
                (id_hash,),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(id_hash=row[0], user_id=row[1], created_at=row[2], expires_at=row[3],
                             last_seen_at=row[4])  # fmt: skip

    def touch_session(self, id_hash: str, last_seen_at: datetime, expires_at: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen_at = %s, expires_at = %s WHERE id_hash = %s",
                (last_seen_at, expires_at, id_hash),
            )

    def delete_session(self, id_hash: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE id_hash = %s", (id_hash,))

    # WCL tokens ----------------------------------------------------------

    def put_tokens(self, tokens: StoredTokens) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO wcl_tokens (user_id, refresh_enc, access_enc, access_expires_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET refresh_enc = EXCLUDED.refresh_enc, "
                "access_enc = EXCLUDED.access_enc, access_expires_at = EXCLUDED.access_expires_at, "
                "updated_at = EXCLUDED.updated_at",
                (tokens.user_id, tokens.refresh_enc, tokens.access_enc, tokens.access_expires_at, tokens.updated_at),
            )

    def get_tokens(self, user_id: str) -> StoredTokens | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT user_id, refresh_enc, access_enc, access_expires_at, updated_at FROM wcl_tokens "
                "WHERE user_id = %s",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredTokens(user_id=row[0], refresh_enc=row[1], access_enc=row[2], access_expires_at=row[3],
                            updated_at=row[4])  # fmt: skip

    def delete_tokens(self, user_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM wcl_tokens WHERE user_id = %s", (user_id,))

    def token_user_ids(self) -> list[str]:
        with self._pool.connection() as conn:
            return [row[0] for row in conn.execute("SELECT user_id FROM wcl_tokens ORDER BY user_id").fetchall()]

    @asynccontextmanager
    async def refresh_lock(self, user_id: str) -> AsyncIterator[None]:
        """
        A session-level advisory lock on its own pooled connection, held across the refresh's HTTP call and
        released however the block ends. If the unlock can't be confirmed (a cancelled or failed call), the
        connection is closed instead of reused, which ends its session and with it the lock.
        """
        conn = await asyncio.to_thread(self._pool.getconn)
        released = False
        try:
            await asyncio.to_thread(_run_lock, conn, _LOCK, user_id)
            try:
                yield
            finally:
                await asyncio.to_thread(_run_lock, conn, _UNLOCK, user_id)
                released = True
        finally:
            if not released:
                await asyncio.to_thread(conn.close)
            await asyncio.to_thread(self._pool.putconn, conn)

    # Report access -------------------------------------------------------

    def get_access(self, user_id: str, report_code: str) -> AccessRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT readable, via, checked_at FROM report_access WHERE user_id = %s AND report_code = %s",
                (user_id, report_code),
            ).fetchone()
        return AccessRecord(readable=row[0], via=row[1], checked_at=row[2]) if row else None

    def put_access(self, user_id: str, report_code: str, record: AccessRecord) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO report_access (user_id, report_code, readable, via, checked_at) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id, report_code) DO UPDATE SET readable = EXCLUDED.readable, via = EXCLUDED.via, "
                "checked_at = EXCLUDED.checked_at",
                (user_id, report_code, record.readable, record.via, record.checked_at),
            )

    def get_public_access(self, report_code: str) -> AccessRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT readable, checked_at FROM report_public_access WHERE report_code = %s", (report_code,)
            ).fetchone()
        if row is None:
            return None
        return AccessRecord(readable=row[0], via="app" if row[0] else None, checked_at=row[1])

    def put_public_access(self, report_code: str, record: AccessRecord) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO report_public_access (report_code, readable, checked_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (report_code) DO UPDATE SET readable = EXCLUDED.readable, checked_at = EXCLUDED.checked_at",
                (report_code, record.readable, record.checked_at),
            )


def _run_lock(conn: Connection, sql: LiteralString, user_id: str) -> None:
    conn.execute(sql, (REFRESH_LOCK_NAMESPACE, user_id))
    conn.commit()  # leave the connection idle; the advisory lock is held by the session, not the transaction


__all__ = ["REFRESH_LOCK_NAMESPACE", "PostgresAuthStore"]
