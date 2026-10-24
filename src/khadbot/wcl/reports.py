"""
wcl/reports.py

Whether a WCL token can read a report: one small query for the report's start time, on the app token (public
reports) or a user's token (also the reports that user may see). The ``auth`` package caches the answers.

``report(code) { startTime }`` is the cheapest field ingest's metadata query already uses (``docs/wcl-api.md``,
"Report metadata"), so the probe asks for nothing unconfirmed.
"""

from __future__ import annotations

from khadbot.wcl.client import (
    WarcraftLogsAPIError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
    WarcraftLogsRateLimitError,
    WarcraftLogsUnauthorizedError,
)

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


__all__ = ["REPORT_READABLE_QUERY", "ReportCheckUnavailableError", "report_readable"]
