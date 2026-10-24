"""
auth/routes.py

The sign-in routes and the request dependencies the API's other routers use.

Routes (all under ``/v1``):

- ``GET /auth/login[?next=/path]``: starts a sign-in and redirects to Warcraft Logs.
- ``GET /auth/callback``: WCL's redirect back; signs the browser in and redirects to the web app.
- ``POST /auth/logout``: ends the session (needs the CSRF header).
- ``GET /me``: the signed-in user.

Cookies are HttpOnly, SameSite=Lax and Secure (off only when the API runs on plain-HTTP localhost). The session
cookie holds an opaque ID; the login cookie holds a nonce that ties a callback to the browser that started it.

CSRF: every state-changing route depends on ``require_csrf``. SameSite=Lax already keeps the cookie off cross-site
POSTs; on top of that the request must carry ``X-KhadBot-CSRF: 1``, a custom header a cross-site page cannot send
without a CORS preflight the API refuses, and a browser ``Origin`` header, when present, must be the web app's or
the API's own.

``mount(app, service)`` adds the routes, keeps the service on ``app.state.auth`` and answers ``AuthError`` with the
API's ``{code, message}`` body. It also installs ``RedactCallbackQuery`` on the server access log, which would
otherwise record the callback URL with its authorization code and state.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from khadbot.auth.errors import AuthError, CsrfError, LoginError, NotSignedInError
from khadbot.auth.service import AuthService
from khadbot.auth.store import User

SESSION_COOKIE = "khadbot_session"
LOGIN_COOKIE = "khadbot_login"
LOGIN_COOKIE_PATH = "/v1/auth"
CSRF_HEADER = "X-KhadBot-CSRF"
CSRF_VALUE = "1"

_NO_STORE = {"Cache-Control": "no-store"}


class Me(BaseModel):
    """The signed-in user."""

    id: str
    name: str
    wcl_user_id: int


class AuthErrorBody(BaseModel):
    code: str
    message: str


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_auth(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth
    return service


Auth = Annotated[AuthService, Depends(get_auth)]


async def optional_user(request: Request, auth: Auth) -> User | None:
    """The signed-in user, or None for a visitor."""
    return await auth.current_user(request.cookies.get(SESSION_COOKIE))


async def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    """The signed-in user; answers 401 ``not_signed_in`` without a valid session."""
    if user is None:
        raise NotSignedInError("Sign in with Warcraft Logs first.")
    return user


async def require_csrf(request: Request, auth: Auth) -> None:
    """Refuse a state-changing request without the CSRF header or from a foreign browser origin (403)."""
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise CsrfError(f"This request needs the {CSRF_HEADER} header.")
    origin = request.headers.get("origin")
    if origin is not None and origin not in {*auth.config.web_origins, auth.config.api_origin}:
        raise CsrfError("This request comes from an origin that may not change state here.")


OptionalUser = Annotated[User | None, Depends(optional_user)]
CurrentUser = Annotated[User, Depends(current_user)]

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_ERRORS: dict[int | str, dict] = {
    400: {"model": AuthErrorBody, "description": "Sign-in failed (invalid_state, wcl_denied, sign_in_failed)"},
    401: {"model": AuthErrorBody, "description": "Not signed in (not_signed_in)"},
    403: {"model": AuthErrorBody, "description": "CSRF check failed (csrf_failed)"},
}

router = APIRouter(prefix="/v1", tags=["auth"])


@router.get("/auth/login", status_code=303, response_class=RedirectResponse)
async def login(auth: Auth, next: str | None = None) -> RedirectResponse:  # noqa: A002 - the query parameter's name
    """Start a Warcraft Logs sign-in: redirects the browser to WCL. ``next`` is a path to return to afterwards."""
    start = await auth.start_login(next)
    response = RedirectResponse(start.authorize_url, status_code=303, headers=_NO_STORE)
    _set_cookie(response, auth, LOGIN_COOKIE, start.browser_nonce, max_age=auth.config.login_timeout_s,
                path=LOGIN_COOKIE_PATH)  # fmt: skip
    return response


@router.get("/auth/callback", status_code=303, response_class=RedirectResponse, responses=_ERRORS)
async def callback(
    request: Request,
    auth: Auth,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> Response:
    """WCL's redirect after sign-in. Starts a session and redirects to the web app, or answers 400."""
    try:
        result = await auth.finish_login(
            state=state, code=code, error=error, browser_nonce=request.cookies.get(LOGIN_COOKIE)
        )
    except LoginError as exc:
        failed = JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message},
                              headers=_NO_STORE)  # fmt: skip
        _clear_cookie(failed, auth, LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)
        return failed
    await auth.logout(request.cookies.get(SESSION_COOKIE))  # never carry an earlier session over a new sign-in
    response = RedirectResponse(result.redirect_to, status_code=303, headers=_NO_STORE)
    _set_cookie(response, auth, SESSION_COOKIE, result.session_id, max_age=int(auth.sessions.max_age.total_seconds()))
    _clear_cookie(response, auth, LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)
    return response


@router.post("/auth/logout", status_code=204, responses=_ERRORS, dependencies=[Depends(require_csrf)])
async def logout(request: Request, auth: Auth) -> Response:
    """End the session and clear its cookie. Signing out twice is fine."""
    await auth.logout(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204, headers=_NO_STORE)
    _clear_cookie(response, auth, SESSION_COOKIE)
    return response


@router.get("/me", response_model=Me, responses=_ERRORS)
async def me(user: CurrentUser, response: Response) -> Me:
    """The signed-in user."""
    response.headers.update(_NO_STORE)
    return Me(id=user.id, name=user.name, wcl_user_id=user.wcl_user_id)


# ---------------------------------------------------------------------------
# Mounting
# ---------------------------------------------------------------------------


def mount(app: FastAPI, service: AuthService) -> None:
    """Add the sign-in routes to ``app``, keep ``service`` for the dependencies, and map ``AuthError`` responses."""
    app.state.auth = service
    app.include_router(router)
    install_log_redaction()

    @app.exception_handler(AuthError)
    async def _auth_error(request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message},
                            headers=_NO_STORE)  # fmt: skip


def _set_cookie(response: Response, auth: AuthService, key: str, value: str, *, max_age: int, path: str = "/") -> None:
    response.set_cookie(key, value, max_age=max_age, path=path, httponly=True, secure=auth.config.cookie_secure,
                        samesite="lax")  # fmt: skip


def _clear_cookie(response: Response, auth: AuthService, key: str, *, path: str = "/") -> None:
    response.delete_cookie(key, path=path, httponly=True, secure=auth.config.cookie_secure, samesite="lax")


__all__ = [
    "CSRF_HEADER",
    "CSRF_VALUE",
    "LOGIN_COOKIE",
    "SESSION_COOKIE",
    "CurrentUser",
    "Me",
    "OptionalUser",
    "RedactCallbackQuery",
    "current_user",
    "get_auth",
    "install_log_redaction",
    "mount",
    "optional_user",
    "require_csrf",
    "router",
]


# ---------------------------------------------------------------------------
# Access-log redaction
# ---------------------------------------------------------------------------

_CALLBACK_QUERY = re.compile(r"(/v1/auth/callback)\?[^\s\"']*")

# Loggers that write request lines: uvicorn's access log, and httpx (whose request log names the full URL).
REDACTED_LOGGERS = ("uvicorn.access", "uvicorn.error", "httpx")


class RedactCallbackQuery(logging.Filter):
    """Strips the query (``code``, ``state``) from any callback URL in a log record's message or arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _CALLBACK_QUERY.sub(r"\1?<redacted>", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact(value) for key, value in record.args.items()}
        return True


def _redact(value: object) -> object:
    if isinstance(value, str):
        return _CALLBACK_QUERY.sub(r"\1?<redacted>", value)
    text = str(value)  # httpx logs a URL object
    return _CALLBACK_QUERY.sub(r"\1?<redacted>", text) if "/v1/auth/callback?" in text else value


def install_log_redaction() -> None:
    """Add ``RedactCallbackQuery`` to the request-logging loggers once."""
    for name in REDACTED_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, RedactCallbackQuery) for f in target.filters):
            target.addFilter(RedactCallbackQuery())
