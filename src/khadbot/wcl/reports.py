"""
wcl/reports.py

Whether a WCL token can read a report: one small query for the report's start time, on the app token (public
reports) or a user's token (also the reports that user may see). The ``auth`` package caches the answers.

``report(code) { startTime }`` is the cheapest field ingest's metadata query already uses (``docs/wcl/report-data.md``,
"Report metadata"), so the probe asks for nothing unconfirmed.

Also here: ``parse_report_source``, which reads a report code or a warcraftlogs.com report URL (with its optional
``#fight=N``) the way the report lookup needs it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from khadbot.wcl.client import (
    WarcraftLogsAPIError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
    WarcraftLogsRateLimitError,
    WarcraftLogsUnauthorizedError,
)

# ---------------------------------------------------------------------------
# Report code or URL
# ---------------------------------------------------------------------------

_REPORT_CODE = re.compile(r"^[A-Za-z0-9]{1,40}$")
_WCL_HOST = re.compile(r"^([a-z0-9-]+\.)*warcraftlogs\.com$")
_REPORT_PATH = re.compile(r"^/reports/([A-Za-z0-9]{1,40})/?$")


@dataclass(frozen=True)
class ReportSource:
    """A report named by the user: its code, and the fight a URL's ``fight=N`` pointed at (None without one)."""

    code: str
    fight: int | None = None


def parse_report_source(source: str) -> ReportSource:
    """
    The report code and focus fight that ``source`` names.

    ``source`` is a report code or a warcraftlogs.com report URL. A positive ``fight=N`` in the URL's fragment or
    query is the focus fight; ``fight=last`` and anything else non-numeric names none.

    Raises:
        ValueError: ``source`` is neither a report code nor a warcraftlogs.com report URL.
    """
    text = source.strip()
    if _REPORT_CODE.match(text):
        return ReportSource(text)
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError("Not a Warcraft Logs report code or URL.") from None
    match = _REPORT_PATH.match(parts.path) if _WCL_HOST.match(host) else None
    if match is None:
        raise ValueError("Not a Warcraft Logs report code or URL.")
    fight: int | None = None
    for query in (parts.fragment, parts.query):
        value = (parse_qs(query).get("fight") or [""])[0]
        if value.isdigit() and int(value) >= 1:
            fight = int(value)
            break
    return ReportSource(match.group(1), fight)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

REPORT_READABLE_QUERY = """
query ReportReadable($code: String!) {
  reportData {
    report(code: $code) {
      startTime
    }
  }
}
"""


class ReportCheckUnavailableError(WarcraftLogsAPIError):
    """WCL could not answer (network, HTTP error, rate limit): readability is unknown, so nothing may be cached."""


async def report_readable(client: WarcraftLogsClient, code: str, *, user_token: str | None = None) -> bool:
    """
    Whether the token can read report ``code``: the app token when ``user_token`` is None, else the user's.

    A report WCL returns is readable. A null report, a private-report error or any other GraphQL error (WCL
    answers a missing report with an error) is not. Failing closed means an unexpected GraphQL error reads as
    "not readable"; callers cache that only briefly.

    Raises:
        WarcraftLogsUnauthorizedError: WCL rejected ``user_token`` (HTTP 401); refresh it and ask again.
        ReportCheckUnavailableError: A network or HTTP failure, or a rate limit: the answer is unknown.
    """
    try:
        data = await client.query(REPORT_READABLE_QUERY, {"code": code}, user_token=user_token)
    except WarcraftLogsUnauthorizedError:
        raise
    except WarcraftLogsPrivateReportError:
        return False
    except WarcraftLogsRateLimitError as exc:
        raise ReportCheckUnavailableError("WCL rate limit reached while checking report access.") from exc
    except WarcraftLogsAPIError as exc:
        if exc.errors:  # a GraphQL-level answer about this report
            return False
        raise ReportCheckUnavailableError("WCL could not be reached to check report access.") from exc
    report = ((data or {}).get("reportData") or {}).get("report")
    return isinstance(report, dict)


__all__ = [
    "REPORT_READABLE_QUERY",
    "ReportCheckUnavailableError",
    "ReportSource",
    "parse_report_source",
    "report_readable",
]
