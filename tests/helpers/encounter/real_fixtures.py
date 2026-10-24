"""
tests/helpers/encounter/real_fixtures.py

The recorded WCL fixtures under tests/fixtures/encounter/wcl/<code>/ (every report directory except SYNTH01): which reports
are usable, which fights have pages, and an offline ingest of those fights. The logic lives in
``khadbot.encounter.ingest`` (``recorded.py``), which ``scripts/data/build_fixture_data.py`` shares.

The pages live in Git LFS. A checkout without ``git lfs pull`` (or with ``GIT_LFS_SKIP_SMUDGE=1``) has pointer files
instead, so such a report counts as absent and the tests that need it skip.
"""

from __future__ import annotations

from helpers.encounter.synth import FIXTURES
from khadbot.encounter.ingest import RECORDED_BUFF_IDS, ingest_recorded, recorded_fights, recorded_reports
from khadbot.encounter.models import IngestResult
from khadbot.encounter.store import EventStore

__all__ = ["RECORDED_BUFF_IDS", "REAL_PULLS", "REAL_REPORTS", "WCL_FIXTURES", "fixture_fights", "ingest_fixture"]

WCL_FIXTURES = FIXTURES / "wcl"

REAL_REPORTS: list[str] = recorded_reports(WCL_FIXTURES)


def fixture_fights(code: str) -> list[int]:
    """Fights with recorded pages. report.json lists every fight, but only these are in the fixture."""
    return recorded_fights(WCL_FIXTURES, code)


REAL_PULLS: list[str] = [f"{code}:{fight}" for code in REAL_REPORTS for fight in fixture_fights(code)]


async def ingest_fixture(code: str, store: EventStore, fights: list[int] | None = None) -> IngestResult:
    """Ingest a report's recorded fights (or ``fights``) offline into ``store`` (see ``ingest_recorded``)."""
    return await ingest_recorded(code, WCL_FIXTURES, store, fights)
