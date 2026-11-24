"""
logging_setup.py

Logging for the hosted service: one JSON object per line on stdout, carrying the process that wrote it, the request,
job, user and report IDs of the work it belongs to and any allowlisted event fields, scrubbed of anything shaped like a
secret.

- ``configure_logging(service=...)`` replaces the root handler. ``KHADBOT_LOG_FORMAT`` is ``json`` (default) or
  ``text``; ``KHADBOT_LOG_LEVEL`` is the level. Every JSON line carries ``service``, ``version`` (``KHADBOT_VERSION``)
  and ``env`` (``KHADBOT_ENV``) when each has a value.
- ``log_context(request_id=..., job_id=..., ...)`` attaches IDs to every record logged inside the block, including
  from tasks it starts. Only IDs are accepted: a value is a name or an identifier, never a credential.
  ``new_request_id()`` makes the request ID of work no caller named: an API request without a usable
  ``X-Request-ID``, a live watch poll, a backfill run.
- ``log_context_holder()`` opens a block whose IDs are learnt part-way through it: ``add_log_ids(user_id=...)``,
  called anywhere inside (a FastAPI dependency in the thread pool or another task included), adds them to every record
  logged in the block from then on. A context variable set in such a place would not reach the rest of the block; the
  holder is one mutable dict that every copy of the context shares. ``log_context()`` IDs win over added ones.
- Event fields come from ``log.info(..., extra={"event": "job.finish", "duration_ms": 12})``. Only the names in
  ``EVENT_FIELDS`` are written; numbers and booleans stay JSON numbers and booleans, anything else is redacted text.
- ``redact()`` is the last line of defence. Code must not log tokens, cookies, client secrets, encryption keys or
  connection strings in the first place (``.claude/rules/hosted.md``); this removes the ones that slip into a message,
  an exception text or a URL. It masks credentials in URLs (``postgres://user:***@host``), ``key=value`` and
  ``"key": "value"`` pairs whose name says secret (token, secret, password, key, cookie, session, authorization,
  credential), ``Bearer`` values, JWTs, Fernet-shaped keys and well-known API key prefixes. Token counts
  (``input_tokens``) are not secrets and are left alone.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO

from khadbot.config import LoggingConfig

REDACTED = "<redacted>"
CONTEXT_FIELDS = ("request_id", "job_id", "user_id", "report_code", "pull_key")
EVENT_FIELDS = (
    "event",
    "duration_ms",
    "outcome",
    "status",
    "method",
    "route",
    "error_code",
    "attempt",
    "queue",
    "job_kind",
    "points",
    "model",
    "input_tokens",
    "output_tokens",
    "op",
    "bytes",
)

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SECRET_NAME = r"[\w.-]*(?:token(?!s\b)|secret|passw(?:or)?d|api[_-]?key|private[_-]?key|fernet|cookie|session|authorization|credential)[\w.-]*"
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host (database URLs, broker URLs)
    (re.compile(r"(\b[a-zA-Z][\w+.-]*://[^:/@\s\"']*:)[^@\s\"']+(@)"), rf"\1{REDACTED}\2"),
    # name = value / name: value / "name": "value", where the name says secret. A header's value runs to the line end.
    (
        re.compile(r"(?i)\b((?:set-)?cookie|authorization)(\s*[:=]\s*)[^\r\n]+"),
        rf"\1\2{REDACTED}",
    ),
    (
        re.compile(rf"""(?i)(["']?{_SECRET_NAME}["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;&)}}\]"']+)"""),
        rf"\1{REDACTED}",
    ),
    # OAuth callback and similar query parameters
    (re.compile(r"(?i)([?&](?:code|state)=)[^&\s\"']+"), rf"\1{REDACTED}"),
    (re.compile(r"(?i)\bBearer\s+[\w.~+/=-]+"), f"Bearer {REDACTED}"),
    (re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*"), REDACTED),  # JWT
    (re.compile(r"(?<![\w-])[A-Za-z0-9_-]{43}=(?![\w=])"), REDACTED),  # Fernet key
    (re.compile(r"\b(?:sk|gsk|xox[bpa])-[A-Za-z0-9_-]{16,}"), REDACTED),  # API key prefixes
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),  # AWS access key ID
)


def redact(text: str) -> str:
    """``text`` with every secret-shaped value replaced by ``<redacted>``."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

_context: ContextVar[dict[str, str] | None] = ContextVar("khadbot_log_context", default=None)
# The innermost log_context_holder()'s dict. The variable is set once per block; add_log_ids() mutates the dict, which
# every copy of the context (thread pool calls, tasks) shares, so the change reaches the whole block.
_holder: ContextVar[dict[str, str] | None] = ContextVar("khadbot_log_context_holder", default=None)


def _checked(ids: dict[str, str | None]) -> dict[str, str]:
    """``ids`` without the None values; raises ``ValueError`` for a name outside ``CONTEXT_FIELDS``."""
    unknown = set(ids) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"Log context takes only {', '.join(CONTEXT_FIELDS)}; got {', '.join(sorted(unknown))}")
    return {k: v for k, v in ids.items() if v is not None}


@contextlib.contextmanager
def log_context(**ids: str | None) -> Iterator[None]:
    """Add IDs (``request_id``, ``job_id``, ``user_id``, ``report_code``, ``pull_key``) to the records logged inside."""
    token = _context.set({**(_context.get() or {}), **_checked(ids)})
    try:
        yield
    finally:
        _context.reset(token)


@contextlib.contextmanager
def log_context_holder() -> Iterator[None]:
    """
    Open an empty holder for IDs learnt inside the block: ``add_log_ids()`` fills it, and every record logged in the
    block after that carries them. An inner holder hides the outer one until it closes.
    """
    token = _holder.set({})
    try:
        yield
    finally:
        _holder.reset(token)


def add_log_ids(**ids: str | None) -> None:
    """
    Add IDs to the innermost ``log_context_holder()``, so the rest of its block carries them, from whichever thread or
    task calls this. Outside any holder it does nothing. Takes the same names as ``log_context()``.
    """
    checked = _checked(ids)
    holder = _holder.get()
    if holder is not None:
        holder.update(checked)


def new_request_id() -> str:
    """A fresh request ID: 16 random hex characters."""
    return uuid.uuid4().hex[:16]


def current_log_context() -> dict[str, str]:
    """
    A copy of the IDs of the enclosing ``log_context()`` blocks and the innermost holder, in ``CONTEXT_FIELDS`` order
    (empty outside any).
    """
    ids = {**(_holder.get() or {}), **(_context.get() or {})}
    return {name: ids[name] for name in CONTEXT_FIELDS if name in ids}


# ---------------------------------------------------------------------------
# Event fields
# ---------------------------------------------------------------------------

_JsonValue = str | int | float | bool


def _event_value(value: object) -> _JsonValue:
    """``value`` as JSON: numbers and booleans unchanged, anything else as redacted text."""
    if isinstance(value, bool | int | float):
        return value
    return redact(value if isinstance(value, str) else str(value))


def _event_fields(record: logging.LogRecord) -> dict[str, _JsonValue]:
    """The allowlisted ``extra=`` fields the record carries, in ``EVENT_FIELDS`` order."""
    attrs = record.__dict__
    return {name: _event_value(attrs[name]) for name in EVENT_FIELDS if attrs.get(name) is not None}


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """
    One JSON object per record: ``time``, ``level``, ``logger``, ``message``, then the process fields (``service``,
    ``version``, ``env``, each only when set), the context IDs, the event fields and ``exc``.
    """

    def __init__(self, process: dict[str, str] | None = None) -> None:
        super().__init__()
        self._process = {key: value for key, value in (process or {}).items() if value}

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, _JsonValue] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        entry.update(self._process)
        for key, value in current_log_context().items():
            entry[key] = redact(value)
        entry.update(_event_fields(record))
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Plain lines for a terminal, with the same redaction, then the context IDs and the event fields."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = redact(super().format(record))
        ids = " ".join(f"{k}={redact(v)}" for k, v in current_log_context().items())
        fields = " ".join(f"{k}={v}" for k, v in _event_fields(record).items())
        line = f"{line} [{ids}]" if ids else line
        return f"{line} {fields}" if fields else line


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_HANDLER_MARK = "_khadbot_handler"


def configure_logging(
    config: LoggingConfig | None = None, *, service: str | None = None, stream: IO[str] | None = None
) -> logging.Handler:
    """
    Send the root logger to ``stream`` (stdout by default) in the configured format and level, replacing any handler
    an earlier call installed. JSON lines carry ``service`` (the command, e.g. ``api``), ``version`` and ``env`` when
    each has a value. Returns the handler.
    """
    cfg = config or LoggingConfig()
    handler = logging.StreamHandler(stream or sys.stdout)
    process = {"service": service or "", "version": cfg.version, "env": cfg.env}
    handler.setFormatter(TextFormatter() if cfg.log_format == "text" else JsonFormatter(process))
    setattr(handler, _HANDLER_MARK, True)
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(getattr(logging, cfg.log_level, logging.INFO))
    return handler


__all__ = [
    "CONTEXT_FIELDS",
    "EVENT_FIELDS",
    "JsonFormatter",
    "TextFormatter",
    "add_log_ids",
    "configure_logging",
    "current_log_context",
    "log_context",
    "log_context_holder",
    "new_request_id",
    "redact",
]
