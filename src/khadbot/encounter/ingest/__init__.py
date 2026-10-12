"""
encounter/ingest/__init__.py

WarcraftLogs ingest: report metadata and paged events (``wcl_queries``, ``fetch``) are cached as raw pages,
normalized into one events frame per pull (``normalize``) and written to the event store (``pipeline``).
The rest of the code enters through ``ingest_report``.
"""

from __future__ import annotations

from khadbot.encounter.ingest.pipeline import default_store, ingest_report

__all__ = ["default_store", "ingest_report"]
