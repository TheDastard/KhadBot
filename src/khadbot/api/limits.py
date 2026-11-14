"""
api/limits.py

``RequestLimits``: the hosted API's own limits on what one client may send, as ASGI middleware.

- **Body size.** A request body larger than ``max_body_bytes`` answers 413 ``body_too_large``. A declared
  ``Content-Length`` is refused before anything is read; a body without one is counted as it arrives.
- **Requests per client.** More than ``per_minute`` requests from one client address in a minute answer 429
  ``rate_limited`` with ``Retry-After``. ``GET /v1/health`` and CORS preflights are not counted. 0 turns it off.

The client address is the connection's peer (``scope["client"]``). Behind a proxy that is the proxy, unless the server
is told which proxies to trust (uvicorn's ``FORWARDED_ALLOW_IPS``); ``docs/hosting.md`` says how. Counters live in
this process: with several API processes each allows ``per_minute``.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
Asgi = Callable[[Scope, Receive, Send], Awaitable[None]]

WINDOW_S = 60.0
MAX_CLIENTS = 10_000  # counters kept; finished windows are dropped once there are more
UNCOUNTED_PATHS = frozenset({"/v1/health"})

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestLimits:
    """
    Args:
        app: The application to protect.
        max_body_bytes: The largest request body accepted; 0 accepts any.
        per_minute: Requests one client address may make per minute; 0 accepts any.
        clock: A monotonic clock in seconds (tests pass their own).
    """

    def __init__(
        self,
        app: Asgi,
        *,
        max_body_bytes: int,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.per_minute = per_minute
        self._clock = clock
        self._windows: dict[str, tuple[float, int]] = {}  # client -> (window start, requests in it)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        wait = self._over_rate(scope)
        if wait is not None:
            await _answer(
                send,
                429,
                "rate_limited",
                "Too many requests from this address. Try again shortly.",
                {"retry-after": str(max(1, math.ceil(wait)))},
            )
            return
        if not self.max_body_bytes:
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            await _too_large(send, self.max_body_bytes)
            return
        await self._with_counted_body(scope, receive, send)

    # ------------------------------------------------------------------

    def _over_rate(self, scope: Scope) -> float | None:
        """Count this request; the seconds until the client's window ends when it is over the limit, else None."""
        if not self.per_minute or scope.get("method") == "OPTIONS" or scope.get("path") in UNCOUNTED_PATHS:
            return None
        client = scope.get("client")
        key = str(client[0]) if client else "unknown"
        now = self._clock()
        start, count = self._windows.get(key, (now, 0))
        if now - start >= WINDOW_S:
            start, count = now, 0
        count += 1
        self._windows[key] = (start, count)
        if len(self._windows) > MAX_CLIENTS:
            self._windows = {k: v for k, v in self._windows.items() if now - v[0] < WINDOW_S}
        return start + WINDOW_S - now if count > self.per_minute else None

    async def _with_counted_body(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the app, cutting the body off (and answering 413) once it passes the limit."""
        read = 0
        overflowed = False
        started = False

        async def counted() -> Message:
            nonlocal read, overflowed
            if overflowed:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                read += len(message.get("body") or b"")
                if read > self.max_body_bytes:
                    overflowed = True
                    return {"type": "http.disconnect"}  # the app stops reading; this middleware answers
            return message

        async def guarded(message: Message) -> None:
            nonlocal started
            if overflowed and not started:
                return  # whatever the app says about a cut-off body is replaced by the 413 below
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not overflowed or started:
                raise
        if overflowed and not started:
            await _too_large(send, self.max_body_bytes)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large(send: Send, limit: int) -> None:
    await _answer(send, 413, "body_too_large", f"The request body is larger than {limit} bytes.", {})


async def _answer(send: Send, status: int, code: str, message: str, headers: dict[str, str]) -> None:
    body = json.dumps({"code": code, "message": message}).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    raw += [(k.encode(), v.encode()) for k, v in headers.items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


__all__ = ["RequestLimits"]
