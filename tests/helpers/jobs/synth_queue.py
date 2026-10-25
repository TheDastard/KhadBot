"""
tests/helpers/jobs/synth_queue.py

A ``JobContext`` over SYNTH01 for queue tests: its recorded raw pages copied under a directory, temp event, record and
annotation stores, the synthetic encounter file and registries, and the filters the pages were recorded with. WCL is
a stub that answers only the rate-limit read, because every page is cached.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from helpers.encounter.synth import (
    FIXTURES,
    SYNTH_DEFENSIVE_COOLDOWN_IDS,
    SYNTH_DEFENSIVE_COOLDOWNS_FILE,
    SYNTH_ENEMY_CAST_IDS,
    SYNTH_OFFENSIVE_COOLDOWN_IDS,
    SYNTH_OFFENSIVE_COOLDOWNS_FILE,
    SYNTH_UTILITY_COOLDOWNS_FILE,
    install_synth_encounter,
)
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.config_files import load_cooldowns
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.records_store import RecordsStore
from khadbot.encounter.store import ParquetEventStore
from khadbot.jobs import JobContext
from khadbot.storage import BlobStore, LocalBlobStore, join_key

PULLS = ["SYNTH01:1", "SYNTH01:2"]


class StubWclClient:
    """Answers the rate-limit read ingest makes around a run. Every page is cached, so nothing else is asked."""

    async def rate_limit(self, *, user_token: str | None = None) -> dict:
        return {"pointsSpentThisHour": 0.0}


@dataclass
class SynthEnv:
    context: JobContext
    raw: RawPageCache
    events: ParquetEventStore
    records: RecordsStore


def synth_env(root: Path, *, blobs: BlobStore | None = None, projector=None) -> SynthEnv:
    """Fixture stores under ``root`` (or in ``blobs``), with SYNTH01's raw pages in place."""
    blobs = blobs or LocalBlobStore(root / "blobs")
    raw = RawPageCache(blobs)
    shutil.copytree(FIXTURES / "wcl" / "SYNTH01", root / "raw-source" / "SYNTH01")
    source = LocalBlobStore(root / "raw-source")
    for key in source.list("SYNTH01/"):
        data = source.get(key)
        assert data is not None
        raw.blobs.put(join_key(raw.prefix, key), data)

    events = ParquetEventStore(blobs)
    records = RecordsStore(blobs)
    enc_root = root / "encounters"
    install_synth_encounter(enc_root)
    data = EncounterData(
        events,
        enc_root=enc_root,
        defensive=load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE),
        offensive=load_cooldowns("offensive", SYNTH_OFFENSIVE_COOLDOWNS_FILE),
        utility=load_cooldowns("utility", SYNTH_UTILITY_COOLDOWNS_FILE),
        cache=AnnotationCache(blobs),
        records_store=records,
    )
    filters = {
        "defensive_spell_ids": SYNTH_DEFENSIVE_COOLDOWN_IDS,
        "offensive_spell_ids": SYNTH_OFFENSIVE_COOLDOWN_IDS,
        "enemy_cast_ids": SYNTH_ENEMY_CAST_IDS,
    }
    context = JobContext(
        data=data, store=events, cache=raw, client=StubWclClient(), filters=lambda: filters, projector=projector
    )  # type: ignore[arg-type]
    return SynthEnv(context, raw, events, records)
