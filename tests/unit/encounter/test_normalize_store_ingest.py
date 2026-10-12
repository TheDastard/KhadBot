"""
tests/unit/encounter/test_normalize_store_ingest.py

Normalizer, ParquetEventStore and ingest orchestration, all on the synthetic SYNTH01 fixture
(offline; no network). Golden snapshots live in tests/golden/normalized; regenerate them with
``uv run pytest tests/unit/encounter --update-goldens`` after an intentional schema change.
"""

from __future__ import annotations

import orjson
import polars as pl
import pytest
from helpers.synth import GOLDEN, SYNTH_ENEMY_CAST_IDS, SYNTH_REGISTRY_IDS

from khadbot.encounter.fetch import MissingFixtureError, specs_for
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.normalize import EVENT_SCHEMA, events_hash, normalize_pull, parse_abilities, parse_report_meta

CODE = "SYNTH01"


@pytest.fixture
def meta(synth_cache):
    pulls, actors = parse_report_meta(CODE, synth_cache.report_meta(CODE))
    return {p.fight_id: p for p in pulls}, actors


def frame_for(synth_cache, meta, fight: int) -> pl.DataFrame:
    pulls, actors = meta
    pull = pulls[fight]
    specs = specs_for(SYNTH_REGISTRY_IDS, SYNTH_ENEMY_CAST_IDS[90001])
    pages = {s.dir_name: synth_cache.pages(CODE, fight, s) for s in specs}
    return normalize_pull(pull, actors, pages)


# ===========================================================================
# parse_report_meta
# ===========================================================================


class TestParseReportMeta:
    def test_pulls(self, meta):
        pulls, _ = meta
        wipe, kill = pulls[1], pulls[2]
        assert (wipe.kill, kill.kill) == (False, True)
        assert wipe.pull_key == "SYNTH01:1"
        assert wipe.duration_ms == 60_000
        assert wipe.encounter_id == 90001
        assert wipe.boss_pct == 35.0
        assert wipe.boss_hp_pct == 41.0
        assert wipe.phase_transitions == [(1, 0), (2, 35_000)]
        assert wipe.friendly_player_ids == [1, 2, 3, 4, 5]

    def test_actor_kinds(self, meta):
        _, actors = meta
        kinds = {a.name: a.kind for a in actors}
        assert kinds["Tankyboi"] == "player"
        assert kinds["Fido"] == "pet"
        assert kinds["Synthetic Boss"] == "boss"
        assert kinds["Synthetic Add"] == "npc"
        assert next(a for a in actors if a.name == "Fido").owner_id == 3


# ===========================================================================
# normalize_pull
# ===========================================================================


class TestNormalize:
    def test_schema(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        assert list(df.schema.items()) == list(EVENT_SCHEMA.items())

    def test_t_ms_is_fight_relative_and_sorted(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        assert df["t_ms"].min() >= 0
        assert df["t_ms"].max() <= 60_000
        assert df["t_ms"].is_sorted()

    def test_seq_is_unique_and_ordered(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        assert df["seq"].to_list() == list(range(df.height))

    def test_all_data_types_present(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        assert set(df["data_type"].cast(pl.Utf8)) == {
            "Deaths",
            "DamageTaken",
            "Casts",
            "Buffs",
            "Debuffs",
            "Interrupts",
            "Dispels",
            "Healing",
            "Threat",
        }

    def test_threat_keeps_only_npc_swings_and_taunt_debuffs(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        threat = df.filter(pl.col("data_type").cast(pl.Utf8) == "Threat")
        kinds = set(threat.select(pl.col("type").cast(pl.Utf8), "source_friendly").rows())
        # NPC swings, taunt debuffs and the add's death; no player cast or player death.
        assert kinds == {("cast", False), ("applydebuff", True), ("death", False)}
        assert threat.filter(pl.col("type").cast(pl.Utf8) == "death")["target_id"].to_list() == [11]
        assert df.filter((pl.col("type").cast(pl.Utf8) == "death") & pl.col("target_friendly")).height == 4
        taunt_casts = df.filter((pl.col("ability_id") == 900703) & (pl.col("type").cast(pl.Utf8) == "cast"))
        assert taunt_casts["data_type"].cast(pl.Utf8).to_list() == ["Casts"]

    def test_two_page_data_type_is_fully_loaded(self, synth_cache, meta):
        pages = synth_cache.pages(CODE, 1, specs_for()[1])
        assert len(pages) == 2
        df = frame_for(synth_cache, meta, 1)
        assert df.filter(pl.col("data_type") == "DamageTaken").height == sum(len(p["data"]) for p in pages)

    def test_friendly_flags_including_pets(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        pet_cast = df.filter((pl.col("source_id") == 6) & (pl.col("type") == "cast"))
        assert pet_cast.height == 1
        assert pet_cast["source_friendly"][0] is True
        boss_hits = df.filter((pl.col("source_id") == 10) & (pl.col("type") == "damage"))
        assert not boss_hits["source_friendly"].any()
        assert boss_hits["target_friendly"].all()

    def test_enemy_casts_are_not_friendly_and_keep_base_data_type(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        enemy = df.filter((pl.col("source_id") == 10) & (pl.col("type") == "begincast"))
        assert enemy.height > 0
        assert set(enemy["data_type"].cast(pl.Utf8)) == {"Casts"}
        assert not enemy["source_friendly"].any()

    def test_hp_pct_from_resources(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        deaths = df.filter((pl.col("type") == "death") & pl.col("target_friendly"))
        assert deaths.height == 4  # 8 s, 23 s, 41 s (cutoff), 50 s; the add's death is not a player's
        dmg = df.filter(pl.col("type") == "damage")
        assert dmg["hp_pct"].null_count() == 0
        assert dmg["hp_pct"].min() == 0.0  # lethal hits
        assert dmg["hp_pct"].max() <= 100.0
        assert df.filter(pl.col("type") == "cast")["hp_pct"].null_count() > 0

    def test_one_shot_overkill_is_kept(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        shot = df.filter((pl.col("type") == "damage") & (pl.col("t_ms") == 8_000) & (pl.col("overkill") > 0))
        assert shot.height == 1
        assert shot["unmitigated"][0] == 140_000

    def test_extra_ability_id_on_interrupts(self, synth_cache, meta):
        df = frame_for(synth_cache, meta, 1)
        kick = df.filter(pl.col("type") == "interrupt")
        assert kick["extra_ability_id"].to_list() == [900401]

    def test_empty_input_gives_empty_schema_frame(self, meta):
        pulls, actors = meta
        df = normalize_pull(pulls[1], actors, {"Deaths": [{"data": [], "nextPageTimestamp": None}]})
        assert df.height == 0
        assert list(df.schema.items()) == list(EVENT_SCHEMA.items())

    def test_unknown_wcl_fields_are_ignored(self, meta):
        pulls, actors = meta
        page = {"data": [{"timestamp": pulls[1].start_ms + 5, "type": "damage", "sourceID": 10, "mystery": [1, 2]}]}
        df = normalize_pull(pulls[1], actors, {"DamageTaken": [page]})
        assert df.height == 1

    def test_hash_is_stable_across_runs(self, synth_cache, meta):
        assert events_hash(frame_for(synth_cache, meta, 1)) == events_hash(frame_for(synth_cache, meta, 1))

    def test_hash_differs_between_pulls_and_on_change(self, synth_cache, meta):
        a, b = frame_for(synth_cache, meta, 1), frame_for(synth_cache, meta, 2)
        assert events_hash(a) != events_hash(b)
        assert events_hash(a) != events_hash(a.with_columns(pl.col("amount") + 1))

    @pytest.mark.parametrize("fight", [1, 2])
    def test_golden_first_rows(self, synth_cache, meta, fight, update_goldens):
        rows = frame_for(synth_cache, meta, fight).head(20).with_columns(pl.col(pl.Categorical).cast(pl.Utf8))
        actual = rows.to_dicts()
        path = GOLDEN / "normalized" / f"SYNTH01_{fight}.json"
        if update_goldens:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(orjson.dumps(actual, option=orjson.OPT_INDENT_2))
        assert actual == orjson.loads(path.read_bytes())


# ===========================================================================
# ParquetEventStore
# ===========================================================================


class TestStore:
    @pytest.fixture
    def loaded(self, synth_cache, synth_store, meta):
        pulls, actors = meta
        df = frame_for(synth_cache, meta, 1)
        synth_store.write_pull(pulls[1], df, events_hash(df), ["Healing"])
        synth_store.write_actors(CODE, actors)
        return synth_store, pulls[1], df

    def test_round_trip(self, loaded):
        store, pull, df = loaded
        got = store.events(pull.pull_key)
        assert got.equals(df)
        assert store.pulls(CODE) == [pull]
        assert store.events_hash(pull.pull_key) == events_hash(df)
        assert store.truncated(pull.pull_key) == ["Healing"]

    def test_actors_round_trip(self, loaded, meta):
        store, _, _ = loaded
        assert store.actors(CODE) == meta[1]

    def test_abilities_round_trip(self, loaded, synth_cache):
        store, _, _ = loaded
        assert store.abilities(CODE) == {}
        abilities = parse_abilities(synth_cache.report_meta(CODE))
        assert abilities[900101] == "Synth Swirl"
        store.write_abilities(CODE, abilities)
        assert store.abilities(CODE) == abilities

    async def test_ingest_writes_abilities(self, synth_ingested):
        assert synth_ingested.abilities(CODE)[900201] == "Synth Cataclysm"

    def test_unknown_pull(self, loaded):
        store, _, _ = loaded
        assert store.events_hash("SYNTH01:99") is None
        assert store.truncated("SYNTH01:99") == []
        assert store.events("SYNTH01:99").height == 0
        assert store.pulls("NOPE") == []
        assert store.actors("NOPE") == []

    def test_filter_types(self, loaded):
        store, pull, df = loaded
        got = store.events(pull.pull_key, types=["death", "interrupt"])
        assert set(got["type"].cast(pl.Utf8)) == {"death", "interrupt"}
        assert got.height == df.filter(pl.col("type").cast(pl.Utf8).is_in(["death", "interrupt"])).height

    def test_filter_actor_matches_source_or_target(self, loaded):
        store, pull, _ = loaded
        got = store.events(pull.pull_key, actor_id=3)
        assert got.height > 0
        assert ((got["source_id"] == 3) | (got["target_id"] == 3)).all()

    def test_filter_ability_and_window(self, loaded):
        store, pull, _ = loaded
        got = store.events(pull.pull_key, ability_ids=[900201], t_from=19_000, t_to=21_000)
        assert got.height > 0
        assert set(got["ability_id"]) == {900201}
        assert got["t_ms"].min() >= 19_000
        assert got["t_ms"].max() <= 21_000

    def test_filter_combination(self, loaded):
        store, pull, _ = loaded
        got = store.events(pull.pull_key, types=["damage"], actor_id=3, ability_ids=[900101], t_to=10_000)
        assert got.height > 0
        assert set(got["type"].cast(pl.Utf8)) == {"damage"}

    def test_rewrite_replaces_record_and_leaves_no_temp_files(self, loaded):
        store, pull, df = loaded
        store.write_pull(pull, df, "newhash", [])
        assert store.events_hash(pull.pull_key) == "newhash"
        assert len(store.pulls(CODE)) == 1
        assert not list(store.root.rglob("*.tmp"))


# ===========================================================================
# ingest_report
# ===========================================================================


class TestIngest:
    async def test_offline_end_to_end(self, synth_ingest_kwargs):
        result = await ingest_report(CODE, **synth_ingest_kwargs)
        assert result.pulls == ["SYNTH01:1", "SYNTH01:2"]
        assert result.skipped == [] and result.failed == {}
        store = synth_ingest_kwargs["store"]
        assert len(store.pulls(CODE)) == 2
        assert store.events("SYNTH01:2").height > 0
        assert len(store.actors(CODE)) == 8

    async def test_second_run_is_a_noop(self, synth_ingest_kwargs):
        await ingest_report(CODE, **synth_ingest_kwargs)
        store = synth_ingest_kwargs["store"]
        before = (store.root / CODE / "1.parquet").stat().st_mtime_ns
        again = await ingest_report(CODE, **synth_ingest_kwargs)
        assert again.pulls == [] and again.skipped == ["SYNTH01:1", "SYNTH01:2"]
        assert (store.root / CODE / "1.parquet").stat().st_mtime_ns == before

    async def test_fight_selection_and_unknown_fight(self, synth_ingest_kwargs):
        result = await ingest_report(CODE, [2, 9], **synth_ingest_kwargs)
        assert result.pulls == ["SYNTH01:2"]
        assert result.failed == {"SYNTH01:9": "fight not found in report"}

    async def test_partial_failure_continues(self, synth_ingest_kwargs, synth_cache):
        import shutil

        spec = next(s for s in specs_for(SYNTH_REGISTRY_IDS, SYNTH_ENEMY_CAST_IDS[90001]) if s.dir_name == "Healing")
        shutil.rmtree(synth_cache._dir(CODE, 1, spec))  # fight 1 loses a data type
        result = await ingest_report(CODE, **synth_ingest_kwargs)
        assert result.pulls == ["SYNTH01:2"]
        assert list(result.failed) == ["SYNTH01:1"]
        assert "Healing" in result.failed["SYNTH01:1"]

    async def test_offline_without_cached_report_raises(self, synth_store, tmp_path):
        from khadbot.encounter.fetch import RawPageCache

        with pytest.raises(MissingFixtureError):
            await ingest_report("NOPE", store=synth_store, cache=RawPageCache(tmp_path / "empty"), offline=True)

    async def test_progress_reports_each_pull(self, synth_ingest_kwargs):
        seen = []
        await ingest_report(CODE, [1], progress=lambda k, s, p: seen.append((k, s)), **synth_ingest_kwargs)
        assert ("SYNTH01:1", "normalizing") in seen
        assert all(k == "SYNTH01:1" for k, _ in seen)


def test_instances_are_kept():
    from helpers.events import make_pull

    from khadbot.encounter.normalize import normalize_pull

    pull = make_pull()
    page = {
        "data": [
            {
                "timestamp": pull.start_ms + 10,
                "type": "damage",
                "sourceID": 11,
                "sourceInstance": 3,
                "targetID": 1,
                "abilityGameID": 1,
                "amount": 5,
            },
            {
                "timestamp": pull.start_ms + 20,
                "type": "cast",
                "sourceID": 1,
                "targetID": 11,
                "targetInstance": 4,
                "abilityGameID": 7,
            },
        ]
    }
    df = normalize_pull(pull, [], {"DamageTaken": [page]})
    assert df.select("source_instance", "target_instance").rows() == [(3, None), (None, 4)]
