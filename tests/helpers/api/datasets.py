"""
tests/helpers/api/datasets.py

Datasets the API tests run against, each served by ``create_app`` through FastAPI's TestClient:

- ``synth``: the synthetic SYNTH01 report with its own encounter file and cooldown registries. Always available.
- ``real``: the recorded fixture reports, ingested offline into a data directory the way
  ``scripts/data/build_fixture_data.py`` builds one. Needs the Git LFS pages.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from helpers.encounter.real_fixtures import REAL_REPORTS, WCL_FIXTURES, ingest_fixture
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
from khadbot.api import create_app
from khadbot.api.views import ApiContext
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.config_files import CooldownRegistry, load_cooldowns
from khadbot.encounter.digest import PullRecords, build_digest, build_pull_records
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import SqliteFindingsStore
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.models import PullDigest
from khadbot.encounter.store import ParquetEventStore

# A recorded report with two encounter-and-difficulty groups (Heroic Ula'tek and Mythic Nymrissa).
MULTI_GROUP_REPORT = "H7z1fqDXjRTFnbtg"


@dataclass
class Dataset:
    name: str
    client: TestClient
    ctx: ApiContext
    code: str  # a report to exercise every endpoint on
    store: ParquetEventStore
    enc_root: Path | None
    registries: dict[str, CooldownRegistry]  # defensive / offensive / utility; empty means the config defaults
    cache: AnnotationCache
    data_dir: Path

    @property
    def fights(self) -> list[int]:
        return [p.fight_id for p in self.store.pulls(self.code)]

    def records(self, fight: int, code: str | None = None) -> PullRecords:
        """Uncapped records built independently of the app's ``EncounterData``."""
        key = f"{code or self.code}:{fight}"
        return build_pull_records(key, self.store, self.enc_root, cache=self.cache, **self.registries)

    def digest(self, fight: int) -> PullDigest:
        return build_digest(f"{self.code}:{fight}", self.store, self.enc_root, cache=self.cache, **self.registries)

    def encounter_data(self) -> EncounterData:
        """A fresh ``EncounterData`` over the same store and files as the app's."""
        return EncounterData(self.store, enc_root=self.enc_root, cache=self.cache, **self.registries)


def build_synth(root: Path) -> Dataset:
    shutil.copytree(FIXTURES / "wcl" / "SYNTH01", root / "raw" / "SYNTH01")
    store = ParquetEventStore(root / "events")
    result = asyncio.run(
        ingest_report(
            "SYNTH01",
            store=store,
            cache=RawPageCache(root / "raw"),
            offline=True,
            defensive_spell_ids=SYNTH_DEFENSIVE_COOLDOWN_IDS,
            offensive_spell_ids=SYNTH_OFFENSIVE_COOLDOWN_IDS,
            enemy_cast_ids=SYNTH_ENEMY_CAST_IDS,
        )
    )
    assert not result.failed, result.failed
    enc_root = root / "encounters"
    install_synth_encounter(enc_root)
    registries = {
        "defensive": load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE),
        "offensive": load_cooldowns("offensive", SYNTH_OFFENSIVE_COOLDOWNS_FILE),
        "utility": load_cooldowns("utility", SYNTH_UTILITY_COOLDOWNS_FILE),
    }
    cache = AnnotationCache(root / "annotated")
    data = EncounterData(store, enc_root=enc_root, cache=cache, **registries)
    app = create_app(data, SqliteFindingsStore(root / "khadbot.sqlite"), data_dir=root)
    return Dataset("synth", TestClient(app), app.state.ctx, "SYNTH01", store, enc_root, registries, cache, root)


def build_real(root: Path) -> Dataset:
    store = ParquetEventStore(root / "events")
    for code in REAL_REPORTS:
        (root / "raw" / code).mkdir(parents=True)
        shutil.copyfile(WCL_FIXTURES / code / "report.json", root / "raw" / code / "report.json")
        result = asyncio.run(ingest_fixture(code, store))
        assert not result.failed, result.failed
    app = create_app(data_dir=root)
    ctx: ApiContext = app.state.ctx
    return Dataset("real", TestClient(app), ctx, MULTI_GROUP_REPORT, store, None, {}, ctx.data.cache, root)
