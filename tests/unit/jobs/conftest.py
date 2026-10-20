"""
tests/unit/jobs/conftest.py

Fixtures for the job-function tests: SYNTH01's recorded raw pages copied into tmp_path, a ``JobContext`` that
ingests them offline into a temp event store, and an ``EncounterData`` over that store with a records store and an
annotation cache in the same temp directory.
"""

from __future__ import annotations

import shutil

import pytest
from helpers.encounter.synth import (
    FIXTURES,
    SYNTH_DEFENSIVE_COOLDOWN_IDS,
    SYNTH_ENEMY_CAST_IDS,
    SYNTH_OFFENSIVE_COOLDOWN_IDS,
)

from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.config_files import load_cooldowns
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import SqliteFindingsStore
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.records_store import RecordsStore
from khadbot.encounter.store import ParquetEventStore
from khadbot.jobs import JobContext
from khadbot.storage import LocalBlobStore


@pytest.fixture
def raw_cache(tmp_path) -> RawPageCache:
    shutil.copytree(FIXTURES / "wcl" / "SYNTH01", tmp_path / "raw" / "SYNTH01")
    return RawPageCache(tmp_path / "raw")


@pytest.fixture
def event_store(tmp_path) -> ParquetEventStore:
    return ParquetEventStore(tmp_path / "events")


@pytest.fixture
def filters() -> dict:
    return {
        "defensive_spell_ids": SYNTH_DEFENSIVE_COOLDOWN_IDS,
        "offensive_spell_ids": SYNTH_OFFENSIVE_COOLDOWN_IDS,
        "enemy_cast_ids": SYNTH_ENEMY_CAST_IDS,
    }


@pytest.fixture
def ingest_ctx(raw_cache, event_store, filters) -> JobContext:
    """A context that ingests SYNTH01 into the temp store; the filters match how its pages were recorded."""
    return JobContext(store=event_store, cache=raw_cache, filters=lambda: filters)


@pytest.fixture
async def ingested(ingest_ctx, event_store) -> ParquetEventStore:
    from khadbot.jobs import IngestReport, run_ingest_report

    result = await run_ingest_report(IngestReport(code="SYNTH01", offline=True), ingest_ctx)
    assert not result.failed
    return event_store


@pytest.fixture
def blobs(tmp_path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
def make_data(ingested, blobs, tmp_path):
    """Builds an ``EncounterData`` over the ingested store with the synthetic encounter file and registries."""
    from helpers.encounter.synth import (
        SYNTH_DEFENSIVE_COOLDOWNS_FILE,
        SYNTH_OFFENSIVE_COOLDOWNS_FILE,
        SYNTH_UTILITY_COOLDOWNS_FILE,
        install_synth_encounter,
    )

    root = tmp_path / "synth_encounters"
    install_synth_encounter(root)

    def make() -> EncounterData:
        return EncounterData(
            ingested,
            enc_root=root,
            defensive=load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE),
            offensive=load_cooldowns("offensive", SYNTH_OFFENSIVE_COOLDOWNS_FILE),
            utility=load_cooldowns("utility", SYNTH_UTILITY_COOLDOWNS_FILE),
            cache=AnnotationCache(blobs),
            records_store=RecordsStore(blobs),
        )

    return make


@pytest.fixture
def findings(tmp_path) -> SqliteFindingsStore:
    return SqliteFindingsStore(tmp_path / "findings.sqlite")
