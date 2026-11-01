"""
tests/unit/wcl/test_report_source.py

``parse_report_source``: a report code or a warcraftlogs.com report URL, and the fight a URL points at.
"""

from __future__ import annotations

import pytest

from khadbot.wcl.reports import ReportSource, parse_report_source


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("H7z1fqDXjRTFnbtg", ReportSource("H7z1fqDXjRTFnbtg")),
        ("  abc123  ", ReportSource("abc123")),
        ("https://www.warcraftlogs.com/reports/abc123", ReportSource("abc123")),
        ("warcraftlogs.com/reports/abc123/", ReportSource("abc123")),
        ("https://www.warcraftlogs.com/reports/abc123#fight=7", ReportSource("abc123", 7)),
        ("https://www.warcraftlogs.com/reports/abc123?fight=7&type=damage-done", ReportSource("abc123", 7)),
        ("https://fr.warcraftlogs.com/reports/abc123#fight=last", ReportSource("abc123")),
        ("https://www.warcraftlogs.com/reports/abc123#fight=0", ReportSource("abc123")),
    ],
)
def test_sources_that_parse(text, expected):
    assert parse_report_source(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "not a code!", "https://example.com/reports/abc", "https://warcraftlogs.com.evil.io/reports/abc",
     "https://www.warcraftlogs.com/guild/id/1"],
)  # fmt: skip
def test_sources_that_do_not(text):
    with pytest.raises(ValueError):
        parse_report_source(text)
