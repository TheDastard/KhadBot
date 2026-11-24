"""
api/request_log.py

``RequestLog``: request IDs and one ``http.request`` log line per API request, as plain ASGI middleware (a context
variable set here covers the whole request, which ``BaseHTTPMiddleware`` would not guarantee).

- **Request ID.** An incoming ``X-Request-ID`` matching ``REQUEST_ID_PATTERN`` is kept; anything else is replaced by 16
  hex characters and never echoed. The ID is in ``log_context(request_id=...)`` for every line written while the
  request is handled, and goes back in the response's ``X-Request-ID`` header.
- **The line.** ``"<METHOD> <route> <status>"`` with the event fields ``method``, ``route``, ``status``,
  ``duration_ms`` and ``error_code``, written when the response finishes or fails. ``route`` is the matched route's
  template (``/v1/reports/{code}``), or ``unmatched``, never the raw path, so scanners cannot create unbounded values.
  INFO, WARNING for a 5xx, DEBUG for ``GET /v1/health``.
- **The user.** The request runs inside ``log_context_holder()``; ``auth.routes.optional_user`` adds the signed-in
  user with ``add_log_ids(user_id=...)``, so every line logged after it in the request, this one included, carries
  ``user_id``. (A context variable set inside a dependency would not reach the handler or this middleware.)
- **The error code.** The error handlers leave ``log_error_code`` on ``request.state``; the line carries it as
  ``error_code``.
- **An unhandled exception** is logged with its traceback as status 500. When no response has started, the middleware
  answers it itself: 500 with the API's usual ``{code, message}`` body (``internal_error``) and the ``X-Request-ID``
  header, so CORS, outside it, adds its headers too and the web app can show the reference. Nothing is re-raised then,
  so Starlette's own error middleware never sees it. When the response had already started, the exception is
  re-raised as before.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from khadbot.logging_setup import log_context, log_context_holder, new_request_id

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
Asgi = Callable[[Scope, Receive, Send], Awaitable[None]]

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")
UNMATCHED = "unmatched"
QUIET_PATHS = frozenset({"/v1/health"})  # health checks poll; their lines are DEBUG

# The ``request.state`` name the error handlers fill (Starlette keeps it in ``scope["state"]``).
STATE_ERROR_CODE = "log_error_code"
INTERNAL_ERROR = "internal_error"
INTERNAL_ERROR_MESSAGE = "Something went wrong on our side."

logger = logging.getLogger(__name__)

_HEADER = REQUEST_ID_HEADER.lower().encode()

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestLog:
    """
    Args:
        app: The application to wrap.
        clock: A monotonic clock in seconds (tests pass their own).
    """

    def __init__(self, app: Asgi, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self.app = app
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = incoming_request_id(scope) or new_request_id()
        state = scope.setdefault("state", {})  # shared with request.state, so handlers' notes come back here
        started = self._clock()
        status = 500
        response_started = False

        async def tagged(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = int(message["status"])
                headers = [(k, v) for k, v in message.get("headers") or () if k.lower() != _HEADER]
                message["headers"] = [*headers, (_HEADER, request_id.encode())]
            await send(message)

        with log_context(request_id=request_id), log_context_holder():
            try:
                await self.app(scope, receive, tagged)
            except Exception:
                if response_started:
                    self._write(scope, state, 500, started, exc_info=True)
                    raise
                state[STATE_ERROR_CODE] = INTERNAL_ERROR
                self._write(scope, state, 500, started, exc_info=True)
                await _internal_error(tagged)
                return
            self._write(scope, state, status, started)

    # ------------------------------------------------------------------

    def _write(
        self, scope: Scope, state: MutableMapping[str, Any], status: int, started: float, *, exc_info: bool = False
    ) -> None:
        method = str(scope.get("method", ""))
        route = route_template(scope)
        level = logging.WARNING if status >= 500 else logging.INFO
        if scope.get("path") in QUIET_PATHS and status < 500:
            level = logging.DEBUG
        logger.log(
            level,
            "%s %s %s",
            method,
            route,
            status,
            exc_info=exc_info,
            extra={
                "event": "http.request",
                "method": method,
                "route": route,
                "status": status,
                "duration_ms": round((self._clock() - started) * 1000),
                "error_code": state.get(STATE_ERROR_CODE),
            },
        )


async def _internal_error(send: Send) -> None:
    """Answer 500 with the API's ``{code, message}`` error body (``errors.ErrorBody``)."""
    body = json.dumps({"code": INTERNAL_ERROR, "message": INTERNAL_ERROR_MESSAGE}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": 500, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def incoming_request_id(scope: Scope) -> str | None:
    """The request's own ``X-Request-ID`` when it matches ``REQUEST_ID_PATTERN``, else None."""
    for name, value in scope.get("headers") or ():
        if name.lower() == _HEADER:
            text = value.decode("latin-1")
            return text if REQUEST_ID_PATTERN.match(text) else None
    return None


def route_template(scope: Scope) -> str:
    """The matched route's path template, or ``unmatched`` when routing found none."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else UNMATCHED


__all__ = [
    "INTERNAL_ERROR",
    "INTERNAL_ERROR_MESSAGE",
    "REQUEST_ID_HEADER",
    "REQUEST_ID_PATTERN",
    "STATE_ERROR_CODE",
    "RequestLog",
    "incoming_request_id",
    "route_template",
]
