"""
tests/unit/encounter/test_real_fixtures.py

Golden snapshots for every recorded WCL fixture pull: the first 20 normalized events and the full digest, built
with the authored encounter files and cooldown registry under config/. The digest's ``produced_from`` carries the
pull's ``events_hash``, so any change to the normalized frame shows up there even past the first 20 rows.

Regenerate with ``uv run pytest tests/unit/encounter/test_real_fixtures.py --update-goldens`` after an intentional
change (including an edit to an encounter file or the registry), and review the diff. Skipped when the fixtures'
Git LFS pages are not pulled.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import orjson
import polars as pl
import pytest
from helpers.real_fixtures import REAL_PULLS, REAL_REPORTS, ingest_fixture
from helpers.synth import GOLDEN

from khadbot.encounter.config_files import load_registry
from khadbot.encounter.digest import build_digest, estimate_tokens, render_digest
from khadbot.encounter.store import ParquetEventStore

pytestmark = pytest.mark.skipif(not REAL_REPORTS, reason="real WCL fixtures not pulled from Git LFS")

# The digest targets 1.5k to 3k tokens; early resets fall below the floor, so only the ceiling is a hard limit.
MAX_DIGEST_TOKENS = 3_000


@pytest.fixture(scope="module")
def real_store(tmp_path_factory) -> ParquetEventStore:
    """Every recorded fixture pull, ingested offline once for the module."""
    store = ParquetEventStore(tmp_path_factory.mktemp("real") / "events")
    for code in REAL_REPORTS:
        result = asyncio.run(ingest_fixture(code, store))
        assert not result.failed, result.failed
    return store


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def _golden_path(kind: str, pull_key: str) -> Path:
    return GOLDEN / kind / f"{pull_key.replace(':', '_')}.json"


def _check(path: Path, actual, update: bool) -> None:
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(actual, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n")
    assert actual == orjson.loads(path.read_bytes())


# ===========================================================================
# Goldens
# ===========================================================================


@pytest.mark.parametrize("pull_key", REAL_PULLS)
def test_normalized_golden_first_rows(real_store, pull_key, update_goldens):
    rows = real_store.events(pull_key).head(20).with_columns(pl.col(pl.Categorical).cast(pl.Utf8))
    _check(_golden_path("normalized", pull_key), rows.to_dicts(), update_goldens)


@pytest.mark.parametrize("pull_key", REAL_PULLS)
def test_digest_golden(real_store, registry, pull_key, update_goldens):
    digest = build_digest(pull_key, real_store, registry=registry)
    _check(_golden_path("digests", pull_key), orjson.loads(digest.model_dump_json()), update_goldens)


# ===========================================================================
# Invariants across the real pulls
# ===========================================================================


@pytest.mark.parametrize("pull_key", REAL_PULLS)
def test_rendered_digest_fits_token_ceiling(real_store, registry, pull_key):
    text = render_digest(build_digest(pull_key, real_store, registry=registry))
    assert estimate_tokens(text) <= MAX_DIGEST_TOKENS


def test_every_fixture_pull_has_both_progress_figures(real_store):
    for code in REAL_REPORTS:
        for pull in real_store.pulls(code):
            assert pull.boss_pct is not None and pull.boss_hp_pct is not None, pull.pull_key
