"""
tests/unit/encounter/conftest.py

Shared fixtures for encounter unit tests: the synthetic SYNTH01 raw cache copied into tmp_path,
plus the filter inputs its pages were recorded with.
"""

from __future__ import annotations

import shutil

import pytest
from helpers.synth import FIXTURES, SYNTH_ENEMY_CAST_IDS, SYNTH_REGISTRY_IDS

from khadbot.encounter.fetch import RawPageCache
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
        "registry_spell_ids": SYNTH_REGISTRY_IDS,
        "enemy_cast_ids": SYNTH_ENEMY_CAST_IDS,
    }
