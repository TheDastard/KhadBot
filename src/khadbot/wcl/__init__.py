"""
wcl/__init__.py

The shared WarcraftLogs API client. Encounter ingest uses it; agent tools never call WCL directly.
"""

from __future__ import annotations

from khadbot.wcl.client import (
    GRAPHQL_CLIENT_URL,
    GRAPHQL_USER_URL,
    RATE_LIMIT_QUERY,
    TOKEN_EXPIRY_BUFFER_SECONDS,
    TOKEN_URL,
    WarcraftLogsAPIError,
    WarcraftLogsAuthError,
    WarcraftLogsClient,
    WarcraftLogsPrivateReportError,
    WarcraftLogsRateLimitError,
    WarcraftLogsUnauthorizedError,
)

__all__ = [
    "GRAPHQL_CLIENT_URL",
    "GRAPHQL_USER_URL",
    "RATE_LIMIT_QUERY",
    "TOKEN_EXPIRY_BUFFER_SECONDS",
    "TOKEN_URL",
    "WarcraftLogsAPIError",
    "WarcraftLogsAuthError",
    "WarcraftLogsClient",
    "WarcraftLogsPrivateReportError",
    "WarcraftLogsRateLimitError",
    "WarcraftLogsUnauthorizedError",
]
