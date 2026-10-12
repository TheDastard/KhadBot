"""
tests/helpers/real_fixtures.py

The recorded WCL fixtures under tests/fixtures/wcl/<code>/ (every report directory except SYNTH01): which reports
are usable, which fights have pages, and an offline ingest of those fights.

The pages live in Git LFS. A checkout without ``git lfs pull`` (or with ``GIT_LFS_SKIP_SMUDGE=1``) has pointer files
instead, so such a report counts as absent and the tests that need it skip.
"""

from __future__ import annotations

from helpers.synth import FIXTURES
from khadbot.encounter.config_files import load_registry
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.models import IngestResult
from khadbot.encounter.store import EventStore

WCL_FIXTURES = FIXTURES / "wcl"
_LFS_POINTER = b"version https://git-lfs"


def _is_recorded(code: str) -> bool:
    meta = WCL_FIXTURES / code / "report.json"
    with meta.open("rb") as fh:
        return not fh.read(len(_LFS_POINTER)).startswith(_LFS_POINTER)


REAL_REPORTS: list[str] = sorted(
    p.name for p in WCL_FIXTURES.iterdir() if p.is_dir() and p.name != "SYNTH01" and _is_recorded(p.name)
)


def fixture_fights(code: str) -> list[int]:
    """Fights with recorded pages. report.json lists every fight, but only these are in the fixture."""
    return sorted(int(p.name) for p in (WCL_FIXTURES / code).iterdir() if p.is_dir() and p.name.isdigit())


REAL_PULLS: list[str] = [f"{code}:{fight}" for code in REAL_REPORTS for fight in fixture_fights(code)]


async def ingest_fixture(code: str, store: EventStore) -> IngestResult:
    """
    Ingest a report's recorded fights offline into ``store``.

    Buffs use the cooldown-registry filter they were recorded with. Enemy Casts are read unfiltered, which every
    fixture pull has, so editing an encounter file's ability IDs never leaves the fixtures without that data type.
    """
    return await ingest_report(
        code,
        fixture_fights(code),
        store=store,
        cache=RawPageCache(WCL_FIXTURES),
        offline=True,
        registry_spell_ids=load_registry().spell_ids(),
    )
