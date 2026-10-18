"""
tests/unit/encounter/conftest.py

Shared fixtures for encounter unit tests: the synthetic SYNTH01 raw cache copied into tmp_path,
plus the filter inputs its pages were recorded with.
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

from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.store import ParquetEventStore


@pytest.fixture
def synth_cache(tmp_path) -> RawPageCache:
    shutil.copytree(FIXTURES / "wcl" / "SYNTH01", tmp_path / "raw" / "SYNTH01")
    return RawPageCache(tmp_path / "raw")


@pytest.fixture
def synth_store(tmp_path) -> ParquetEventStore:
    return ParquetEventStore(tmp_path / "events")


@pytest.fixture
def synth_ingest_kwargs(synth_cache, synth_store) -> dict:
    return {
        "cache": synth_cache,
        "store": synth_store,
        "offline": True,
        "defensive_spell_ids": SYNTH_DEFENSIVE_COOLDOWN_IDS,
        "offensive_spell_ids": SYNTH_OFFENSIVE_COOLDOWN_IDS,
        "enemy_cast_ids": SYNTH_ENEMY_CAST_IDS,
    }


@pytest.fixture
async def synth_ingested(synth_ingest_kwargs, synth_store) -> ParquetEventStore:
    """SYNTH01 ingested offline into ``synth_store``."""
    from khadbot.encounter.ingest import ingest_report

    result = await ingest_report("SYNTH01", **synth_ingest_kwargs)
    assert not result.failed
    return synth_store


@pytest.fixture
def synth_enc():
    from helpers.encounter.synth import SYNTH_ENCOUNTER_FILE

    from khadbot.encounter.config_files import load_encounter_file

    return load_encounter_file(SYNTH_ENCOUNTER_FILE)


@pytest.fixture
def synth_defensive():
    from helpers.encounter.synth import SYNTH_DEFENSIVE_COOLDOWNS_FILE

    from khadbot.encounter.config_files import load_cooldowns

    return load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE)


@pytest.fixture
def synth_offensive():
    from helpers.encounter.synth import SYNTH_OFFENSIVE_COOLDOWNS_FILE

    from khadbot.encounter.config_files import load_cooldowns

    return load_cooldowns("offensive", SYNTH_OFFENSIVE_COOLDOWNS_FILE)


@pytest.fixture
def synth_enc_root(tmp_path):
    """Encounter-file root holding only the synthetic encounter file, at its canonical path."""
    from helpers.encounter.synth import install_synth_encounter

    root = tmp_path / "synth_encounters"
    install_synth_encounter(root)
    return root


@pytest.fixture
def synth_data(synth_ingested, synth_enc_root, synth_defensive, synth_offensive):
    """EncounterData over ingested SYNTH01 with the synthetic encounter file and cooldown registries."""
    from khadbot.encounter.evidence import EncounterData

    return EncounterData(synth_ingested, enc_root=synth_enc_root, defensive=synth_defensive, offensive=synth_offensive)
