"""
encounter/ingest/__init__.py

WarcraftLogs ingest: report metadata and paged events (``wcl_queries``, ``fetch``) are cached as raw pages with a
fetch manifest per pull (``pipeline.fetch_report``), then normalized into one events frame per pull (``normalize``)
and written to the event store (``process``). The ingest job enters through ``fetch_report`` and the process job
through ``process_pull_events``; ``ingest_report`` does both in one call, and ``ingest_recorded`` uses it for offline
ingest of recorded pages (test fixtures, the dashboard's fixture data directory).
"""

from __future__ import annotations

from khadbot.encounter.ingest.normalize import NORMALIZE_VERSION
from khadbot.encounter.ingest.pipeline import FetchedReport, fetch_report, ingest_report
from khadbot.encounter.ingest.process import (
    EventsOutcome,
    PullNotFetchedError,
    normalize_pull_into_store,
    process_pull_events,
)
from khadbot.encounter.ingest.recorded import (
    RECORDED_BUFF_IDS,
    ingest_recorded,
    recorded_fights,
    recorded_reports,
)

__all__ = [
    "NORMALIZE_VERSION",
    "RECORDED_BUFF_IDS",
    "EventsOutcome",
    "FetchedReport",
    "PullNotFetchedError",
    "fetch_report",
    "ingest_recorded",
    "ingest_report",
    "normalize_pull_into_store",
    "process_pull_events",
    "recorded_fights",
    "recorded_reports",
]
