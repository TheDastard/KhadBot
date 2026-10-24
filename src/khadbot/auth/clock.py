"""
auth/clock.py

The injectable clock the sign-in services read the time from, so tests can move it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The current time, timezone-aware UTC."""
    return datetime.now(UTC)


__all__ = ["Clock", "utc_now"]
