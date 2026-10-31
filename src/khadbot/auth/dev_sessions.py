"""
auth/dev_sessions.py

Test sessions: signed-in users for development and tests, without a Warcraft Logs login. ``issue_session`` creates
or reuses a named test user, starts a session for it and can mark reports readable for that user. It is a deliberate
hole in sign-in, so it holds three lines:

- **Gate.** It refuses to run unless ``KHADBOT_TEST_SESSIONS`` is on (``config.DevSessionsConfig``). Only the local
  stack's, the dev loop's and CI's environment files turn it on; staging only for the load test; production never.
- **Marked users.** A test user's ``wcl_user_id`` is negative, in ``TEST_WCL_USER_ID_MIN`` to ``-1``. WCL account IDs
  are positive and ``AuthService.finish_login`` refuses any other, so a real sign-in can never reach a test user and a
  test user is recognisable by its ID alone (``is_test_user_id``).
- **No secrets kept.** A test user holds no WCL tokens, so its jobs run on the app's auth. The session cookie value
  is returned to the caller (stdout or a Playwright storage-state file are the command's job) and never logged here.

Only a digest of the cookie value is stored, as for a real session.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from khadbot.auth.clock import Clock, utc_now
from khadbot.auth.sessions import SessionManager
from khadbot.auth.store import AccessRecord, AuthStore, User
from khadbot.config import DevSessionsConfig
from khadbot.errors import ConfigurationError

logger = logging.getLogger(__name__)

GATE_VARIABLE = "KHADBOT_TEST_SESSIONS"

# Test users' WCL user IDs: -1 down to -(2**40). WCL's are positive; see the module docstring.
TEST_WCL_USER_ID_MAX = -1
TEST_WCL_USER_ID_MIN = -(2**40)

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_REPORT_CODE = re.compile(r"^[A-Za-z0-9]{1,40}$")  # the same shape auth.access accepts
DEFAULT_TTL = timedelta(hours=24)


class SessionsDisabledError(ConfigurationError):
    """Test sessions were asked for while ``KHADBOT_TEST_SESSIONS`` is off."""


@dataclass(frozen=True)
class IssuedSession:
    """A started test session. ``cookie`` is the ``khadbot_session`` value: treat it as a password."""

    user: User
    cookie: str = field(repr=False)
    expires_at: datetime
    readable: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Gate and IDs
# ---------------------------------------------------------------------------


def require_enabled(config: DevSessionsConfig) -> None:
    """
    Raises:
        SessionsDisabledError: Test sessions are off.
    """
    if not config.enabled:
        raise SessionsDisabledError(
            f"Test sessions are off. Set {GATE_VARIABLE}=1 in the local stack's or CI's environment to use them; "
            "staging sets it only for the load test and production never does."
        )


def reserved_wcl_user_id(name: str) -> int:
    """
    The WCL user ID of the test user ``name``: stable per name, negative, and in the reserved range.

    Raises:
        ValueError: ``name`` is not 1 to 32 characters of lowercase letters, digits and hyphens, starting with a
            letter or digit.
    """
    if not _NAME.match(name):
        raise ValueError(f"A test user's name is 1 to 32 lowercase letters, digits or hyphens, not {name!r}.")
    value = int.from_bytes(hashlib.sha256(f"khadbot-test-user:{name}".encode()).digest()[:5], "big")  # 40 bits
    return -(value + 1)


def is_test_user_id(wcl_user_id: int) -> bool:
    """Whether ``wcl_user_id`` is in the range reserved for test users."""
    return TEST_WCL_USER_ID_MIN <= wcl_user_id <= TEST_WCL_USER_ID_MAX


def display_name(name: str) -> str:
    return f"Test {name}"


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------


async def issue_session(
    store: AuthStore,
    config: DevSessionsConfig,
    name: str,
    *,
    reports: Sequence[str] = (),
    ttl: timedelta = DEFAULT_TTL,
    clock: Clock = utc_now,
) -> IssuedSession:
    """
    Start a session for the test user ``name`` (created on first use), marking ``reports`` readable for it.

    Each marked report is cached as readable for this user and as not public, so every other user and any visitor
    gets 404 for it without Warcraft Logs being asked (the stack cannot ask it). The answers stand for the usual
    recheck ages (``KHADBOT_ACCESS_RECHECK_S``, ``KHADBOT_ACCESS_DENIED_RECHECK_S``); running this again renews them.

    Raises:
        SessionsDisabledError: The gate is off.
        ValueError: A bad user name or report code.
    """
    require_enabled(config)
    wcl_user_id = reserved_wcl_user_id(name)
    for code in reports:
        if not _REPORT_CODE.match(code):
            raise ValueError(f"{code!r} is not a Warcraft Logs report code.")
    now = clock()
    user = await asyncio.to_thread(store.upsert_user, wcl_user_id, display_name(name), now)
    for code in reports:
        await asyncio.to_thread(
            store.put_access, user.id, code, AccessRecord(readable=True, via="user", checked_at=now)
        )
        await asyncio.to_thread(store.put_public_access, code, AccessRecord(readable=False, via=None, checked_at=now))
    sessions = SessionManager(store, idle=ttl, max_age=ttl, clock=clock)
    cookie = await sessions.create(user.id)
    logger.info("Test session started for user %s (%d reports marked readable)", user.id, len(reports))
    return IssuedSession(user=user, cookie=cookie, expires_at=now + ttl, readable=tuple(reports))


def storage_state(cookie: str, *, domain: str, secure: bool, expires_at: datetime, cookie_name: str) -> dict:
    """
    A Playwright storage-state document holding the session cookie, for ``storageState`` in a browser context.
    Browsers share a host's cookies across ports, so ``domain="localhost"`` serves the web app and the API alike.
    """
    return {
        "cookies": [
            {
                "name": cookie_name,
                "value": cookie,
                "domain": domain,
                "path": "/",
                "expires": expires_at.timestamp(),
                "httpOnly": True,
                "secure": secure,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


__all__ = [
    "DEFAULT_TTL",
    "GATE_VARIABLE",
    "TEST_WCL_USER_ID_MAX",
    "TEST_WCL_USER_ID_MIN",
    "IssuedSession",
    "SessionsDisabledError",
    "display_name",
    "is_test_user_id",
    "issue_session",
    "require_enabled",
    "storage_state",
    "reserved_wcl_user_id",
]
