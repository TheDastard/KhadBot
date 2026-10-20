"""
tests/unit/storage/test_data_dir_compat.py

Existing data directories keep working byte for byte through the blob store.

``tests/golden/storage/data_dir_manifest.json`` holds the SHA-256 of every file under ``events/`` and ``annotated/``
after ``helpers.storage.data_dir.build_fixture_data_dir`` runs over the recorded fixtures. It was first produced by
the path-based persistence classes, before the blob store existed, so matching it means the blob store writes the
same bytes they did, and a data directory they built is the one these tests read. Regenerate it with
``--update-goldens`` only after an intentional change to what a stage writes (a normalize fix, an annotation sidecar
field, ``ANNOTATION_VERSION``, a polars upgrade that changes parquet bytes) and say why in the commit. Skipped when
the fixtures' Git LFS pages are not pulled.
"""

from __future__ import annotations

import asyncio
import shutil

import orjson
import polars as pl
import pytest
from helpers.encounter.real_fixtures import REAL_PULLS, REAL_REPORTS, ingest_fixture
from helpers.encounter.synth import GOLDEN
from helpers.storage.data_dir import build_fixture_data_dir, manifest, stores
from polars.testing import assert_frame_equal

from khadbot.config import EncounterConfig
from khadbot.encounter.config_files import encfile_hash, load_encounter_view
from khadbot.encounter.store import split_pull_key

pytestmark = pytest.mark.skipif(not REAL_REPORTS, reason="real WCL fixtures not pulled from Git LFS")

MANIFEST = GOLDEN / "storage" / "data_dir_manifest.json"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The fixture data directory built through the blob store, with the digests built on the way."""
    root = tmp_path_factory.mktemp("data")
    digests = build_fixture_data_dir(root, form="blobs")
    return root, digests


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def test_blob_store_writes_the_bytes_the_path_based_store_wrote(built, update_goldens):
    root, _ = built
    actual = manifest(root)
    if update_goldens:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_bytes(orjson.dumps(actual, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n")
    expected = orjson.loads(MANIFEST.read_bytes())
    assert sorted(actual) == sorted(expected), "different files"
    assert {k for k in actual if actual[k] != expected[k]} == set()


def test_offline_reingest_over_the_directory_rewrites_identical_bytes(built, tmp_path):
    root, _ = built
    code = REAL_REPORTS[0]
    copy = tmp_path / "copy"
    shutil.copytree(root / "events", copy / "events")
    before = manifest(copy)
    # Without its pull records every pull is written again, over the files already there.
    (copy / "events" / code / "pulls.json").unlink()
    store, _ = stores(copy, "blobs")
    result = asyncio.run(ingest_fixture(code, store))
    assert result.pulls and not result.skipped and not result.failed
    assert manifest(copy) == before


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", ["path", "blobs"])
def test_events_positions_and_metadata_read_as_the_files_hold_them(built, form):
    root, _ = built
    store, _ = stores(root, form)
    assert store.reports() == REAL_REPORTS
    for code in REAL_REPORTS:
        records = orjson.loads((root / "events" / code / "pulls.json").read_bytes())
        assert [p.model_dump(mode="json") for p in store.pulls(code)] == [r["pull"] for r in records]
        actors = pl.read_parquet(root / "events" / code / "actors.parquet").to_dicts()
        assert [a.model_dump() for a in store.actors(code)] == actors
        abilities = orjson.loads((root / "events" / code / "abilities.json").read_bytes())
        assert store.abilities(code) == {int(k): v for k, v in abilities.items()}
    for key in REAL_PULLS:
        code, fight = split_pull_key(key)
        assert_frame_equal(store.events(key), pl.read_parquet(root / "events" / code / f"{fight}.parquet"))
        assert_frame_equal(
            store.positions(key), pl.read_parquet(root / "events" / code / "positions" / f"{fight}.parquet")
        )


@pytest.mark.parametrize("form", ["path", "blobs"])
def test_annotations_read_as_the_files_hold_them(built, form):
    root, _ = built
    store, cache = stores(root, form)
    cutoff = EncounterConfig().cutoff_fraction
    for key in REAL_PULLS:
        code, fight = split_pull_key(key)
        pull = next(p for p in store.pulls(code) if p.fight_id == fight)
        enc_hash = encfile_hash(load_encounter_view(pull.encounter_id, pull.difficulty))
        hit = cache.get(pull, enc_hash, store.events_hash(key) or "", cutoff, positions_hash=store.positions_hash(key))
        assert hit is not None, key
        base = root / "annotated" / code / f"{fight}__{enc_hash}"
        assert_frame_equal(hit.frame, pl.read_parquet(base.with_suffix(".parquet")))
        meta = orjson.loads(base.with_suffix(".json").read_bytes())
        assert (hit.cutoff_ms, [p.model_dump() for p in hit.phases]) == (meta["cutoff_ms"], meta["phases"])


def test_digests_match_the_goldens(built):
    _, digests = built
    for key, digest in digests.items():
        golden = GOLDEN / "digests" / f"{key.replace(':', '_')}.json"
        assert orjson.loads(digest.model_dump_json()) == orjson.loads(golden.read_bytes()), key
