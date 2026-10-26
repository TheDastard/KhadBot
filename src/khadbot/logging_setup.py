"""
logging_setup.py

Logging for the hosted service: one JSON object per line on stdout, carrying the job, user and report IDs of the work
that wrote it, and scrubbed of anything shaped like a secret.

- ``configure_logging()`` replaces the root handler. ``KHADBOT_LOG_FORMAT`` is ``json`` (default) or ``text``;
  ``KHADBOT_LOG_LEVEL`` is the level.
- ``log_context(job_id=..., user_id=..., report_code=...)`` attaches IDs to every record logged inside the block,
  including from tasks it starts. Only IDs are accepted: a value is a name or an identifier, never a credential.
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
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO

from khadbot.config import LoggingConfig

REDACTED = "<redacted>"
CONTEXT_FIELDS = ("job_id", "user_id", "report_code", "pull_key")

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


@contextlib.contextmanager
def log_context(**ids: str | None) -> Iterator[None]:
    """Add IDs (``job_id``, ``user_id``, ``report_code``, ``pull_key``) to the records logged inside the block."""
    unknown = set(ids) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"Log context takes only {', '.join(CONTEXT_FIELDS)}; got {', '.join(sorted(unknown))}")
    token = _context.set({**(_context.get() or {}), **{k: v for k, v in ids.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """One JSON object per record: ``time``, ``level``, ``logger``, ``message``, the context IDs and ``exc``."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, str] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for key, value in (_context.get() or {}).items():
            entry[key] = redact(value)
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Plain lines for a terminal, with the same redaction and the context IDs after the message."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = redact(super().format(record))
        ids = " ".join(f"{k}={redact(v)}" for k, v in (_context.get() or {}).items())
        return f"{line} [{ids}]" if ids else line


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_HANDLER_MARK = "_khadbot_handler"


def configure_logging(config: LoggingConfig | None = None, *, stream: IO[str] | None = None) -> logging.Handler:
    """
    Send the root logger to ``stream`` (stdout by default) in the configured format and level, replacing any handler
    an earlier call installed. Returns the handler.
    """
    cfg = config or LoggingConfig()
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(TextFormatter() if cfg.log_format == "text" else JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(getattr(logging, cfg.log_level, logging.INFO))
    return handler


__all__ = ["CONTEXT_FIELDS", "JsonFormatter", "TextFormatter", "configure_logging", "log_context", "redact"]
