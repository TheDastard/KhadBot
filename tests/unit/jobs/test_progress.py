"""
tests/unit/jobs/test_progress.py

The progress implementations: recording, line printing and dropping.
"""

from __future__ import annotations

from khadbot.jobs import LineProgress, NullProgress, RecordingProgress


def test_recording_keeps_events_in_order():
    p = RecordingProgress()
    p.report("a", None, 0.0)
    p.report("b", "X:1", 0.5)
    assert p.events == [("a", None, 0.0), ("b", "X:1", 0.5)]


def test_line_progress_prints_pull_events_in_the_cli_format():
    lines: list[str] = []
    p = LineProgress(lines.append)
    p.report("fetched Deaths", "ABC:3", 0.09)
    p.report("normalizing", "ABC:3", 1.0)
    assert lines == ["ABC:3  fetched Deaths  9%", "ABC:3  normalizing  100%"]


def test_line_progress_skips_job_level_events():
    lines: list[str] = []
    LineProgress(lines.append).report("ingest", None, 0.0)
    assert lines == []


def test_null_progress_accepts_anything():
    NullProgress().report("x", None, 1.0)
