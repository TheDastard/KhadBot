"""
encounter/ingest/__init__.py

WarcraftLogs ingest: report metadata and paged events (``wcl_queries``, ``fetch``) are cached as raw pages,
normalized into one events frame per pull (``normalize``) and written to the event store (``pipeline``).
The rest of the code enters through ``ingest_report``, and through ``ingest_recorded`` for
offline ingest of recorded pages (test fixtures, the dashboard's fixture data directory).
"""

from __future__ import annotations

from khadbot.encounter.ingest.pipeline import default_store, ingest_report
from khadbot.encounter.ingest.recorded import (
    RECORDED_BUFF_IDS,
    ingest_recorded,
    recorded_fights,
    recorded_reports,
)

__all__ = [
    "RECORDED_BUFF_IDS",
    "default_store",
    "ingest_recorded",
    "ingest_report",
    "recorded_fights",
    "recorded_reports",
]
