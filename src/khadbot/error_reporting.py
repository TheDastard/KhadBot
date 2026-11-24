"""
error_reporting.py

Error reporting for the hosted API and worker through the Sentry SDK (Sentry or GlitchTip), off unless ``SENTRY_DSN``
is set.

- ``init_error_reporting(service)`` starts the SDK for one process. With no DSN it does nothing and never imports
  ``sentry_sdk``, so the SDK costs nothing where it is not used.
- The SDK collects no user details, cookies, request headers, request bodies or stack-frame variables. Every event and
  breadcrumb passes through ``redact()`` (``logging_setup``) before it leaves the process, the same last line of
  defence the log lines have.
- Each event is tagged with ``service`` and the IDs of the enclosing ``log_context()`` (``request_id``, ``job_id``,
  ``user_id``, ``report_code``, ``pull_key``), so an error links to its log lines. ``release`` is ``KHADBOT_VERSION``
  and ``environment`` is ``KHADBOT_ENV``.
- A record logged at ERROR or above becomes an event; INFO and above become breadcrumbs on the next one. Log lines
  are not sent to the tracker as logs: they belong to the log pipeline.
- ``report_exception(exc)`` sends an exception the code answers itself, which the SDK's own hooks never see (the API's
  request log middleware turns an unhandled exception into a 500 rather than letting it reach Starlette).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from khadbot.config import ErrorReportingConfig, LoggingConfig
from khadbot.errors import missing_group_message
from khadbot.logging_setup import current_log_context, redact

UNSET_ENVIRONMENT = "unset"  # the SDK would otherwise call an unnamed deployment "production"
_REQUEST_DROP = ("cookies", "data", "headers", "env")

# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------


def _redact_all(value: Any) -> Any:
    """``value`` with every string inside it, at any depth, passed through ``redact()``."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact_all(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_all(item) for item in value]
    return value


def scrub_event(event: dict[str, Any], service: str) -> dict[str, Any]:
    """
    The event to send: request cookies, headers and body dropped, every string redacted (message, ``logentry``,
    exception values, breadcrumbs, URLs), and tagged with ``service`` and the current log context's IDs.
    """
    request = event.get("request")
    if isinstance(request, dict):
        for key in _REQUEST_DROP:
            request.pop(key, None)
        if isinstance(request.get("query_string"), str):  # redact() knows query parameters by their leading ? or &
            request["query_string"] = redact("?" + request["query_string"])[1:]
    logentry = event.get("logentry")
    if isinstance(logentry, dict) and "formatted" in logentry:
        logentry.pop("params", None)  # the formatted message carries them, and only it is redacted as a whole
    tags = dict(event.get("tags") or {})
    tags["service"] = service
    tags.update(current_log_context())
    event["tags"] = tags
    scrubbed: dict[str, Any] = _redact_all(event)
    return scrubbed


def scrub_breadcrumb(crumb: dict[str, Any]) -> dict[str, Any]:
    """The breadcrumb (or log record) with every string redacted."""
    scrubbed: dict[str, Any] = _redact_all(crumb)
    return scrubbed


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def init_error_reporting(service: str, config: ErrorReportingConfig | None = None, *, transport: Any = None) -> bool:
    """
    Start reporting errors for ``service`` (``api`` or ``worker``) when ``SENTRY_DSN`` is set. Returns whether it
    started. ``transport`` replaces the SDK's HTTP transport (tests).

    Raises:
        ImportError: A DSN is set but the ``hosted`` group, which holds ``sentry-sdk``, is not installed.
    """
    cfg = config or ErrorReportingConfig()
    if not cfg.enabled:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError as exc:
        raise ImportError(missing_group_message("hosted", "Error reporting (SENTRY_DSN)") + f"\n({exc})") from exc

    process = LoggingConfig()
    # A plain dict: the SDK types its hooks with TypedDicts, and the scrubbers work on any event-shaped dict.
    options: dict[str, Any] = {
        "dsn": cfg.dsn,
        "release": process.version or None,
        "environment": process.env or UNSET_ENVIRONMENT,
        "traces_sample_rate": cfg.traces_sample_rate,
        # Nothing about the caller or the request beyond its route and URL; scrub_event redacts the query string.
        "data_collection": {
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "off"}},
            "http_bodies": [],
            "graphql": {"document": False, "variables": False},
            "gen_ai": {"inputs": False, "outputs": False},
            "mcp": {"inputs": False, "outputs": False},
            "database_query_data": False,
            "stack_frame_variables": False,
        },
        # A record logged at ERROR becomes an event (the worker logs a failed job rather than raising it), lower ones
        # breadcrumbs on the next event. Log lines go to the log pipeline, not to the tracker as Sentry logs.
        "integrations": [LoggingIntegration(level=None, event_level=logging.ERROR, breadcrumb_level=logging.INFO)],
        "before_send": lambda event, hint: scrub_event(event, service),
        "before_breadcrumb": lambda crumb, hint: scrub_breadcrumb(crumb),
        "before_send_log": lambda record, hint: scrub_breadcrumb(record),
    }
    if transport is not None:
        options["transport"] = transport
    sentry_sdk.init(**options)
    return True


def report_exception(exc: BaseException) -> None:
    """Send ``exc`` as an event when ``init_error_reporting`` started the SDK; otherwise do nothing and import nothing."""
    sdk = sys.modules.get("sentry_sdk")
    if sdk is not None and sdk.get_client().is_active():
        sdk.capture_exception(exc)


__all__ = ["init_error_reporting", "report_exception", "scrub_breadcrumb", "scrub_event"]
