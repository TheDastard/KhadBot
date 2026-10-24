"""
auth/errors.py

Errors the sign-in and access code raises. None of their messages ever contains a token, a code or a cookie. They
need no hosted library, so callers on any path (job functions) can catch them.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """
    A sign-in or access failure the API answers with ``{code, message}`` and ``status``.

    Attributes:
        status: The HTTP status to answer with.
        code: A stable machine-readable code (``invalid_state``, ``not_signed_in``).
    """

    status = 400
    code = "auth_error"

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status


class LoginError(AuthError):
    """The callback could not complete a sign-in: bad or replayed state, WCL denied it, or the exchange failed."""

    status = 400
    code = "sign_in_failed"


class NotSignedInError(AuthError):
    """The request carries no valid session."""

    status = 401
    code = "not_signed_in"


class CsrfError(AuthError):
    """A state-changing request failed the CSRF check (missing header or a foreign origin)."""

    status = 403
    code = "csrf_failed"


class ReportNotFoundError(AuthError):
    """The report does not exist or the caller may not read it. Always 404, so a private report is not confirmed."""

    status = 404
    code = "report_not_found"


class WclSignInRequiredError(AuthError):
    """The user has no usable WCL tokens (never stored, or the refresh token was refused): sign in again."""

    status = 401
    code = "wcl_not_signed_in"


class WclUnavailableError(AuthError):
    """WCL could not be reached or refused for a temporary reason; nothing was cached. Try again later."""

    status = 503
    code = "wcl_unavailable"


__all__ = [
    "AuthError",
    "CsrfError",
    "LoginError",
    "NotSignedInError",
    "ReportNotFoundError",
    "WclSignInRequiredError",
    "WclUnavailableError",
]
