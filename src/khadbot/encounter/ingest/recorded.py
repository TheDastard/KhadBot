"""
encounter/ingest/recorded.py

Offline ingest of recorded WCL raw pages: a directory laid out like the raw page cache
(``<root>/<code>/report.json`` and ``<root>/<code>/<fight>/...``) where only some fights have pages.

The recorded test fixtures under ``tests/fixtures/encounter/wcl/`` use this, and so does
``scripts/data/build_fixture_data.py``, which builds the data directory the dashboard is developed and tested against.
Their pages live in Git LFS: a checkout without ``git lfs pull`` has pointer files instead, so such a report
counts as absent.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path

from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.ingest.pipeline import ingest_report
from khadbot.encounter.models import IngestResult
from khadbot.encounter.store import EventStore

# Synthetic reports use their own encounter file and registry, so they are never part of a recorded set.
SYNTHETIC_REPORTS = ("SYNTH01",)

# The Buffs filter the recorded fixtures were made with: the cast IDs of the 17 cooldowns the registry held then
# (Buffs filter hash 3c12a331a193). The registry has since grown and gained aura IDs, so these pulls have Buffs rows
# for these spells only.
RECORDED_BUFF_IDS: list[int] = [
    633, 1022, 6940, 29166, 31821, 33206, 47788, 51052, 62618, 97462, 98008, 102342, 116841, 116849, 207399, 357170,
    374227,
]  # fmt: skip

_LFS_POINTER = b"version https://git-lfs"


def is_recorded(root: Path, code: str) -> bool:
    """True if ``<root>/<code>/report.json`` exists and holds real bytes, not a Git LFS pointer."""
    meta = Path(root) / code / "report.json"
    if not meta.is_file():
        return False
    with meta.open("rb") as fh:
        return not fh.read(len(_LFS_POINTER)).startswith(_LFS_POINTER)


def recorded_reports(root: Path, *, exclude: Collection[str] = SYNTHETIC_REPORTS) -> list[str]:
    """Report codes under ``root`` with real recorded metadata, sorted."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in exclude and is_recorded(root, p.name))


def recorded_fights(root: Path, code: str) -> list[int]:
    """Fights with recorded pages. report.json lists every fight, but only these have pages."""
    return sorted(int(p.name) for p in (Path(root) / code).iterdir() if p.is_dir() and p.name.isdigit())


async def ingest_recorded(
    code: str,
    root: Path,
    store: EventStore,
    fights: list[int] | None = None,
    *,
    defensive_spell_ids: Sequence[int] = RECORDED_BUFF_IDS,
) -> IngestResult:
    """
    Ingest a report's recorded fights (or ``fights``) offline into ``store``.

    Buffs use the filter the pages were recorded with (``RECORDED_BUFF_IDS`` by default), whatever the registry
    holds now. Enemy Casts are read unfiltered, which every recorded pull has, so editing an encounter file's
    ability IDs never leaves the recordings without that data type.
    """
    return await ingest_report(
        code,
        fights or recorded_fights(root, code),
        store=store,
        cache=RawPageCache(Path(root)),
        offline=True,
        defensive_spell_ids=defensive_spell_ids,
    )
