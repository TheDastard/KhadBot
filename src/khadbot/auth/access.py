"""
auth/access.py

Report access: ``ReportAccess.can_read(user, code)`` decides whether a user may read a report. A report is readable
when WCL lets the user's own token read it, or when it is public (the app token can read it). Answers are cached
per user, and per report for the public check, with the time WCL was asked; a cached "readable on the user's token"
stands for ``access_recheck_s``, a cached "public" (readable on the app's token) for the longer
``access_public_recheck_s``, and a cached "not readable" for the shorter ``access_denied_recheck_s``.

Callers turn an unreadable report into a 404 (``require_readable`` raises ``ReportNotFoundError``), so the API never
confirms that a private report exists. When WCL can't be reached nothing is cached and ``WclUnavailableError`` is
raised: the check fails closed without recording a wrong answer.

Every answer that is not cached costs a WCL request, so a caller who names new report codes spends the app's points.
``ReportAccess.budget`` (an ``AccessBudget``; the hosted API sets one) reserves each request's points first, against
the caller's own allowance or, signed out, the visitors' shared one. With none free the check raises
``AccessBudgetError`` (429) before WCL is asked. Without a budget (workers, tests) checks are not counted.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Protocol

from khadbot.auth.clock import Clock, utc_now
from khadbot.auth.errors import AccessBudgetError, ReportNotFoundError, WclSignInRequiredError, WclUnavailableError
from khadbot.auth.store import AccessRecord, AccessVia, AuthStore
from khadbot.wcl.client import WarcraftLogsAuthError, WarcraftLogsClient, WarcraftLogsUnauthorizedError
from khadbot.wcl.reports import ReportCheckUnavailableError, report_readable

logger = logging.getLogger(__name__)

# WCL report codes are short and alphanumeric; anything else is refused without asking WCL.
_REPORT_CODE = re.compile(r"^[A-Za-z0-9]{1,40}$")


class UserTokenSource(Protocol):
    """Hands out a user's WCL access token (``auth.tokens.WclTokens``)."""

    async def access_token(self, user_id: str, *, force_refresh: bool = False) -> str: ...


class AccessBudget(Protocol):
    """Reserves the WCL points of one uncached access check (the hosted API's is ``api.budget.AccessCheckBudget``)."""

    async def reserve(self, user_id: str | None, code: str) -> str:
        """
        Hold the points for one WCL request about ``code`` and return the reservation's ID.

        Raises:
            AccessBudgetError: No points are free for this caller now.
        """
        ...

    async def finish(self, reservation: str, *, answered: bool) -> None:
        """Close the reservation: charged when WCL ``answered``, released when the request never counted."""
        ...


# ---------------------------------------------------------------------------
# Access checks
# ---------------------------------------------------------------------------


class ReportAccess:
    """
    The one access rule every report route uses.

    Args:
        store: Where answers are cached.
        tokens: Users' WCL tokens.
        client: Builds an (unentered) WCL client; its app credentials serve the public check.
        recheck: How long a "readable on the user's token" answer stands.
        denied_recheck: How long a "not readable" answer stands.
        public_recheck: How long a "public" answer stands (readable on the app's token); default ``recheck``.
        clock: The current UTC time.
        budget: Reserves the points of each uncached check; None counts nothing. It can be set later (``budget``).
    """

    def __init__(
        self,
        store: AuthStore,
        tokens: UserTokenSource,
        client: Callable[[], WarcraftLogsClient],
        *,
        recheck: timedelta,
        denied_recheck: timedelta,
        public_recheck: timedelta | None = None,
        clock: Clock = utc_now,
        budget: AccessBudget | None = None,
    ) -> None:
        self._store = store
        self._tokens = tokens
        self.budget = budget
        self._client = client
        self._recheck = recheck
        self._denied_recheck = denied_recheck
        self._public_recheck = public_recheck if public_recheck is not None else recheck
        self._clock = clock

    async def can_read(self, user_id: str | None, code: str) -> bool:
        """
        Whether ``user_id`` (None: a visitor who is not signed in) may read report ``code``.

        Raises:
            WclUnavailableError: WCL could not answer and no cached answer stands.
            AccessBudgetError: WCL has to be asked and the budget has no points for this caller now.
        """
        if not _REPORT_CODE.match(code):
            return False
        if user_id is not None:
            cached = await asyncio.to_thread(self._store.get_access, user_id, code)
            if cached is not None and self._stands(cached):
                return cached.readable
        readable, via = False, None
        if user_id is not None:
            readable = await self._user_check(user_id, code)
            via = "user" if readable else None
        if not readable and await self._public(code, user_id):
            readable, via = True, "app"
        if user_id is not None:
            record = AccessRecord(readable=readable, via=_via(via), checked_at=self._clock())
            await asyncio.to_thread(self._store.put_access, user_id, code, record)
        return readable

    async def require_readable(self, user_id: str | None, code: str) -> None:
        """
        Raise ``ReportNotFoundError`` unless ``user_id`` may read ``code``.

        Raises:
            ReportNotFoundError: The report is missing or not readable by this user (always 404).
            WclUnavailableError: WCL could not answer.
            AccessBudgetError: The check needs WCL and the budget has no points for this caller now.
        """
        if not await self.can_read(user_id, code):
            raise ReportNotFoundError("Report not found or not shared with your Warcraft Logs account.")

    # ------------------------------------------------------------------

    def _stands(self, record: AccessRecord) -> bool:
        if not record.readable:
            age = self._denied_recheck
        else:
            age = self._public_recheck if record.via == "app" else self._recheck
        return self._clock() - record.checked_at < age

    async def _user_check(self, user_id: str, code: str) -> bool:
        try:
            token = await self._tokens.access_token(user_id)
            async with self._client() as wcl:
                try:
                    return await self._spend(user_id, code, lambda: report_readable(wcl, code, user_token=token))
                except WarcraftLogsUnauthorizedError:
                    fresh = await self._tokens.access_token(user_id, force_refresh=True)
                    return await self._spend(user_id, code, lambda: report_readable(wcl, code, user_token=fresh))
        except WclSignInRequiredError:
            return False  # no usable user token: only the public check is left
        except WarcraftLogsUnauthorizedError:
            logger.info("WCL rejected user %s's refreshed token during an access check", user_id)
            return False
        except ReportCheckUnavailableError:
            raise WclUnavailableError("Warcraft Logs could not be reached to check access to this report.") from None

    async def _public(self, code: str, user_id: str | None = None) -> bool:
        cached = await asyncio.to_thread(self._store.get_public_access, code)
        if cached is not None and self._stands(cached):
            return cached.readable
        try:
            async with self._client() as wcl:
                readable = await self._spend(user_id, code, lambda: report_readable(wcl, code))
        except ReportCheckUnavailableError:
            raise WclUnavailableError("Warcraft Logs could not be reached to check access to this report.") from None
        except (WarcraftLogsUnauthorizedError, WarcraftLogsAuthError):
            logger.warning("WCL rejected the app credentials during an access check")
            raise WclUnavailableError("Warcraft Logs could not be reached to check access to this report.") from None
        record = AccessRecord(readable=readable, via="app" if readable else None, checked_at=self._clock())
        await asyncio.to_thread(self._store.put_public_access, code, record)
        return readable

    async def _spend(self, user_id: str | None, code: str, ask: Callable[[], Awaitable[bool]]) -> bool:
        """Run one WCL request under a budget reservation (when there is a budget): charged only if WCL answered."""
        if self.budget is None:
            return await ask()
        reservation = await self.budget.reserve(user_id, code)
        answered = False
        try:
            readable = await ask()
            answered = True
            return readable
        finally:
            await self.budget.finish(reservation, answered=answered)


def _via(value: str | None) -> AccessVia | None:
    if value == "user":
        return "user"
    if value == "app":
        return "app"
    return None


__all__ = ["AccessBudget", "AccessBudgetError", "ReportAccess", "UserTokenSource"]
