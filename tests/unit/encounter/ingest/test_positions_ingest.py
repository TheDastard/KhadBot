"""
tests/unit/encounter/ingest/test_positions_ingest.py

The position table through normalize, the store and ingest: which unit a sample belongs to, duplicates,
the events frame left as it was, the store round trip and its hash, records written before positions were
kept, and positions on a recorded fixture pull (skipped when the Git LFS pages are not pulled).
"""

from __future__ import annotations

import asyncio

import orjson
import polars as pl
import pytest
from helpers.encounter.events import make_pull
from helpers.encounter.real_fixtures import REAL_REPORTS, WCL_FIXTURES, ingest_fixture
from helpers.encounter.synth import SYNTH_ENEMY_CAST_IDS, SYNTH_REGISTRY_IDS

from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.ingest.fetch import specs_for
from khadbot.encounter.ingest.normalize import (
    events_hash,
    normalize_pull,
    normalize_pull_with_positions,
    parse_report_meta,
)
from khadbot.encounter.store import (
    POSITION_SCHEMA,
    ParquetEventStore,
    empty_events,
    empty_positions,
    positions_hash,
)

CODE = "SYNTH01"
KEY = ["t_ms", "actor_id", "instance", "x", "y"]


def _row(t, typ, **kw):
    return {"timestamp": t, "type": typ, **kw}


# ===========================================================================
# normalize_pull_with_positions
# ===========================================================================


def test_samples_follow_resource_actor_and_drop_duplicates():
    pull = make_pull(start_ms=1_000)
    damage = _row(
        1_100, "damage", sourceID=11, sourceInstance=3, targetID=1, abilityGameID=5, amount=9,
        resourceActor=2, x=100, y=200, mapID=7,
    )  # fmt: skip
    pages = {
        "DamageTaken": [{"data": [damage, _row(1_200, "damage", sourceID=11, targetID=2, abilityGameID=5)]}],
        # The same heal sample twice (two page sets repeat each other), and an NPC sampled as the source.
        "Healing": [
            {"data": [_row(1_300, "heal", sourceID=2, targetID=1, resourceActor=2, x=150, y=250, mapID=7)] * 2}
        ],
        "Casts-Enemies": [
            {"data": [_row(1_400, "cast", sourceID=11, sourceInstance=4, targetID=1, resourceActor=1, x=-5, y=6)]}
        ],
        # A player's taunt from the Threat data: dropped from the events (Casts holds it), but still sampled.
        "Threat": [{"data": [_row(1_500, "cast", sourceID=1, targetID=11, resourceActor=1, x=160, y=260, mapID=7)]}],
        # A position with no resourceActor names no unit and is skipped.
        "Casts": [{"data": [_row(1_600, "cast", sourceID=3, abilityGameID=8, x=1, y=1, mapID=7)]}],
    }
    df, pos = normalize_pull_with_positions(pull, [], pages)

    assert pos.schema == pl.Schema(POSITION_SCHEMA)
    assert pos.rows() == [
        (100, 1, None, 100, 200, 7),
        (300, 1, None, 150, 250, 7),
        (500, 1, None, 160, 260, 7),
        (400, 11, 4, -5, 6, None),
    ]
    assert not pos.select(KEY).is_duplicated().any()
    assert df.equals(normalize_pull(pull, [], pages))
    assert 500 not in df["t_ms"].to_list()  # the Threat taunt stays out of the events


def test_events_frame_and_hash_unchanged_on_synth(synth_cache):
    pulls, actors = parse_report_meta(CODE, synth_cache.report_meta(CODE))
    specs = specs_for(SYNTH_REGISTRY_IDS, SYNTH_ENEMY_CAST_IDS[90001])
    for pull in pulls:
        pages = {s.dir_name: synth_cache.pages(CODE, pull.fight_id, s) for s in specs}
        df, pos = normalize_pull_with_positions(pull, actors, pages)
        plain = normalize_pull(pull, actors, pages)
        assert df.equals(plain)
        assert events_hash(df) == events_hash(plain)
        assert pos.is_empty()  # SYNTH01's pages carry no positions


# ===========================================================================
# Store
# ===========================================================================


def test_positions_hash_is_order_independent_and_content_sensitive():
    table = pl.DataFrame(
        {"t_ms": [1, 2], "actor_id": [1, 1], "instance": [None, None], "x": [0, 5], "y": [0, 5], "map_id": [7, 7]},
        schema=POSITION_SCHEMA,
    )
    assert positions_hash(table) == positions_hash(table.reverse())
    assert positions_hash(table) != positions_hash(table.with_columns(pl.col("x") + 1))
    assert positions_hash(table) != positions_hash(empty_positions())


class TestStore:
    @pytest.fixture
    def stored(self, synth_cache, synth_store):
        pulls, _ = parse_report_meta(CODE, synth_cache.report_meta(CODE))
        pull = next(p for p in pulls if p.fight_id == 1)
        df = empty_events()
        table = pl.DataFrame(
            {"t_ms": [5], "actor_id": [1], "instance": [None], "x": [10], "y": [20], "map_id": [7]},
            schema=POSITION_SCHEMA,
        )
        synth_store.write_pull(pull, df, events_hash(df), [], table)
        return synth_store, pull, df, table

    def test_round_trip(self, stored):
        store, pull, _, table = stored
        assert store.positions(pull.pull_key).equals(table)
        assert store.positions_hash(pull.pull_key) == positions_hash(table)
        assert (store.root / CODE / "positions" / "1.parquet").exists()
        assert not list(store.root.rglob("*.tmp"))

    def test_rewrite_without_positions_removes_the_table(self, stored):
        store, pull, df, _ = stored
        store.write_pull(pull, df, events_hash(df))
        assert store.positions_hash(pull.pull_key) is None
        assert store.positions(pull.pull_key).equals(empty_positions())
        assert not (store.root / CODE / "positions" / "1.parquet").exists()

    def test_unknown_pull(self, stored):
        store, _, _, _ = stored
        assert store.positions_hash("SYNTH01:99") is None
        assert store.positions("SYNTH01:99").equals(empty_positions())


# ===========================================================================
# Ingest
# ===========================================================================


async def test_synth_ingest_stores_an_empty_table(synth_ingested):
    for key in ("SYNTH01:1", "SYNTH01:2"):
        assert synth_ingested.positions(key).is_empty()
        assert synth_ingested.positions_hash(key) == positions_hash(empty_positions())


async def test_record_without_positions_hash_loads_and_reingest_adds_the_table(synth_ingest_kwargs):
    store = synth_ingest_kwargs["store"]
    await ingest_report(CODE, **synth_ingest_kwargs)
    # A pulls.json record written before positions were kept: no positions_hash key, no table.
    path = store.root / CODE / "pulls.json"
    records = orjson.loads(path.read_bytes())
    for r in records:
        del r["positions_hash"]
    path.write_bytes(orjson.dumps(records))
    for fight in (1, 2):
        (store.root / CODE / "positions" / f"{fight}.parquet").unlink()

    assert [p.fight_id for p in store.pulls(CODE)] == [1, 2]
    assert store.positions_hash("SYNTH01:1") is None
    assert store.positions("SYNTH01:1").is_empty()
    ev_hash = store.events_hash("SYNTH01:1")

    again = await ingest_report(CODE, **synth_ingest_kwargs)
    assert again.pulls == ["SYNTH01:1", "SYNTH01:2"] and again.skipped == []
    assert store.positions_hash("SYNTH01:1") == positions_hash(empty_positions())
    assert (store.root / CODE / "positions" / "1.parquet").exists()
    assert store.events_hash("SYNTH01:1") == ev_hash

    third = await ingest_report(CODE, **synth_ingest_kwargs)
    assert third.pulls == [] and third.skipped == ["SYNTH01:1", "SYNTH01:2"]


# ===========================================================================
# A recorded pull
# ===========================================================================

REAL = "H7z1fqDXjRTFnbtg"
UNCHECKED_RAGE = 1301007


@pytest.mark.skipif(REAL not in REAL_REPORTS, reason="real WCL fixtures not pulled from Git LFS")
def test_recorded_pull_has_positions(tmp_path):
    store = ParquetEventStore(tmp_path / "events")
    result = asyncio.run(ingest_fixture(REAL, store))
    assert not result.failed
    key = f"{REAL}:10"
    pos = store.positions(key)

    assert pos.height == 2_409
    assert not pos.select(KEY).is_duplicated().any()
    assert pos.equals(pos.sort(["actor_id", "instance", "t_ms", "x", "y", "map_id"]))
    assert store.positions_hash(key) == positions_hash(pos)

    # An Unchecked Rage hit carries the hit player's own position, sampled at the hit's t_ms.
    pull = next(p for p in store.pulls(REAL) if p.fight_id == 10)
    rage = store.events(key, types=["damage"], ability_ids=[UNCHECKED_RAGE]).row(0, named=True)
    raw = next(
        e
        for page in sorted((WCL_FIXTURES / REAL / "10" / "DamageTaken").rglob("page_*.json"))
        for e in orjson.loads(page.read_bytes())["data"]
        if e.get("abilityGameID") == UNCHECKED_RAGE and e["timestamp"] - pull.start_ms == rage["t_ms"]
    )
    assert raw["resourceActor"] == 2 and raw["targetID"] == rage["target_id"]
    own = pos.filter((pl.col("actor_id") == rage["target_id"]) & (pl.col("t_ms") == rage["t_ms"]))
    assert own.select("x", "y", "map_id").rows() == [(raw["x"], raw["y"], raw["mapID"])]
