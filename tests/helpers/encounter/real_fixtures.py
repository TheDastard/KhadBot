"""
tests/helpers/encounter/real_fixtures.py

The recorded WCL fixtures under tests/fixtures/encounter/wcl/<code>/ (every report directory except SYNTH01): which reports
are usable, which fights have pages, and an offline ingest of those fights.

The pages live in Git LFS. A checkout without ``git lfs pull`` (or with ``GIT_LFS_SKIP_SMUDGE=1``) has pointer files
instead, so such a report counts as absent and the tests that need it skip.
"""

from __future__ import annotations

from helpers.encounter.synth import FIXTURES
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

# The Buffs filter the fixtures were recorded with: the cast IDs of the 17 cooldowns the registry held then (Buffs
# filter hash 3c12a331a193). The registry has since grown and gained aura IDs, so these pulls have Buffs rows for
# these spells only.
RECORDED_BUFF_IDS: list[int] = [
    633, 1022, 6940, 29166, 31821, 33206, 47788, 51052, 62618, 97462, 98008, 102342, 116841, 116849, 207399, 357170,
    374227,
]  # fmt: skip


async def ingest_fixture(code: str, store: EventStore, fights: list[int] | None = None) -> IngestResult:
    """
    Ingest a report's recorded fights (or ``fights``) offline into ``store``.

    Buffs use the filter they were recorded with (``RECORDED_BUFF_IDS``), whatever the registry holds now. Enemy
    Casts are read unfiltered, which every fixture pull has, so editing an encounter file's ability IDs never leaves
    the fixtures without that data type.
    """
    return await ingest_report(
        code,
        fights or fixture_fights(code),
        store=store,
        cache=RawPageCache(WCL_FIXTURES),
        offline=True,
        registry_spell_ids=RECORDED_BUFF_IDS,
    )
