"""
tests/unit/encounter/test_digest.py

Pull digest: golden JSON per SYNTH01 pull, caps and ordering, coverage levels, every anomaly
code, rendering and its size estimate. Regenerate goldens with
``uv run pytest tests/unit/encounter/test_digest.py --update-goldens`` after an intentional change.
"""

from __future__ import annotations

import orjson
import polars as pl
import pytest
import yaml
from helpers.synth import GOLDEN, SYNTH_ENCOUNTER_FILE, install_synth_encounter

from khadbot.encounter import digest as digest_mod
from khadbot.encounter.config_files import encfile_hash, load_encounter_file, registry_hash
from khadbot.encounter.digest import build_digest, estimate_tokens, render_digest
from khadbot.encounter.normalize import events_hash


@pytest.fixture
def enc_root(tmp_path):
    root = tmp_path / "encounters"
    install_synth_encounter(root)
    return root


@pytest.fixture
def digest_of(synth_ingested, enc_root, synth_registry):
    def build(fight, root=None, **kw):
        return build_digest(f"SYNTH01:{fight}", synth_ingested, root or enc_root, synth_registry, **kw)

    return build


def write_enc(tmp_path, mutate):
    data = yaml.safe_load(SYNTH_ENCOUNTER_FILE.read_text(encoding="utf-8"))
    mutate(data)
    root = tmp_path / "enc_variant"
    install_synth_encounter(root, yaml.safe_dump(data))
    return root


def rewrite_pull(store, fight, transform, truncated=None):
    """Replace a stored pull's events with ``transform(events)``."""
    pull = next(p for p in store.pulls("SYNTH01") if p.fight_id == fight)
    df = transform(store.events(pull.pull_key))
    store.write_pull(pull, df, events_hash(df), truncated)


# ===========================================================================
# Goldens
# ===========================================================================


@pytest.mark.parametrize("fight", [1, 2])
def test_golden(digest_of, update_goldens, fight):
    actual = orjson.loads(digest_of(fight).model_dump_json())
    path = GOLDEN / "digests" / f"SYNTH01_{fight}.json"
    if update_goldens:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(actual, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n")
    assert actual == orjson.loads(path.read_bytes())


# ===========================================================================
# Content, ordering and caps
# ===========================================================================


def test_wipe_summary(digest_of, synth_ingested, synth_registry):
    d = digest_of(1)
    assert (d.outcome, d.duration_ms, d.phase_reached, d.boss_pct, d.boss_hp_pct, d.cutoff_ms) == (
        "wipe",
        60_000,
        "p2",
        35.0,
        41.0,
        41_000,
    )
    assert [x.name for x in d.deaths] == ["Alphadps", "Gammadps", "Betadps", "Tankyboi"]
    assert [m.mechanic_id for m in d.mechanics] == ["swirl", "shadow_bolt", "curse"]
    assert [(u.ability_id, u.share_pct) for u in d.unregistered] == [(900102, 9.9)]
    assert d.produced_from == {
        "events_hash": synth_ingested.events_hash("SYNTH01:1"),
        "encfile_hash": encfile_hash(load_encounter_file(SYNTH_ENCOUNTER_FILE)),
        "registry_hash": registry_hash(synth_registry),
    }


def test_inactive_mechanics_are_dropped(digest_of):
    assert [m.mechanic_id for m in digest_of(2).mechanics] == ["swirl"]


def test_caps(digest_of, monkeypatch):
    monkeypatch.setattr(digest_mod, "DEATH_CAP", 2)
    monkeypatch.setattr(digest_mod, "MECHANIC_CAP", 1)
    monkeypatch.setattr(digest_mod, "COOLDOWN_CAP", 1)
    d = digest_of(1, unregistered_cap=0)
    assert [x.t_ms for x in d.deaths] == [8_000, 23_000]
    assert [m.mechanic_id for m in d.mechanics] == ["swirl"]
    assert len(d.cooldowns) == 1
    assert d.unregistered == []


def test_deaths_before_cutoff_come_first(digest_of, monkeypatch):
    # With a 0.2 fraction the cutoff is the first death, so later deaths are all "after".
    d = digest_of(1, cutoff_fraction=0.2)
    assert d.cutoff_ms == 8_000
    assert [x.after_cutoff for x in d.deaths] == [False, True, True, True]


def test_unknown_pull(digest_of):
    with pytest.raises(KeyError, match="SYNTH01:9"):
        digest_of(9)


# ===========================================================================
# Coverage
# ===========================================================================


def test_coverage_full(digest_of):
    assert digest_of(1).coverage == "full"


def test_coverage_none_without_encounter_file(digest_of, tmp_path):
    d = digest_of(1, root=tmp_path / "empty")
    assert d.coverage == "none"
    assert d.mechanics == [] and d.coverage_windows == []
    assert d.phase_reached is None
    assert d.produced_from["encfile_hash"] == "none"
    assert not any(a.startswith("unregistered_damage") for a in d.anomalies)


def test_coverage_partial(digest_of, tmp_path):
    root = write_enc(tmp_path, lambda d: d.update(ignored_abilities=[]))  # boss melee becomes unregistered
    d = digest_of(1, root=root)
    assert d.coverage == "partial"
    assert d.unregistered[0].ability_id == 900010
    assert "unregistered_damage:41.0" in d.anomalies


# ===========================================================================
# Anomalies
# ===========================================================================


def test_cooldown_unverified(digest_of):
    assert digest_of(1).anomalies == ["cooldown_unverified:900302"]
    assert digest_of(2).anomalies == []


def test_truncated(digest_of, synth_ingested):
    rewrite_pull(synth_ingested, 2, lambda df: df, truncated=["Casts", "DamageTaken"])
    assert digest_of(2).anomalies[:2] == ["truncated:Casts", "truncated:DamageTaken"]


def test_missing_data_type(digest_of, synth_ingested):
    rewrite_pull(synth_ingested, 2, lambda df: df.filter(pl.col("data_type").cast(pl.Utf8) != "Healing"))
    d = digest_of(2)
    assert "missing_data_type:Healing" in d.anomalies
    assert d.deaths[0].window_healing is None


def test_no_hp_data(digest_of, synth_ingested):
    rewrite_pull(synth_ingested, 2, lambda df: df.with_columns(pl.lit(None, dtype=pl.Float32).alias("hp_pct")))
    d = digest_of(2)
    assert "no_hp_data" in d.anomalies
    assert d.deaths[0].time_to_die_ms is None


def test_phase_trigger_unmatched_only_when_expected(digest_of, tmp_path):
    root = write_enc(
        tmp_path,
        lambda d: d["phases"].append(
            {"id": "p3", "name": "P3", "difficulties": ["mythic"], "start": {"trigger": "time", "t_ms": 999_999}}
        ),
    )
    # The kill must have gone through every phase; the wipe (WCL last_phase 2, both reached) may not have.
    assert "phase_trigger_unmatched:p3" in digest_of(2, root=root).anomalies
    assert not any(a.startswith("phase_trigger") for a in digest_of(1, root=root).anomalies)


# ===========================================================================
# Rendering
# ===========================================================================


def test_render(digest_of):
    text = render_digest(digest_of(1))
    lines = text.splitlines()
    assert (
        lines[0]
        == "PULL SYNTH01:1 Synthetic Boss | wipe | 60.0s | reached p2 | fight 35.0% | boss HP 41.0% | cutoff 41.0s"
        " | coverage full"
    )
    assert lines[1] == "ref ev:SYNTH01:1:pull"
    assert (
        "- [ev:SYNTH01:1:death:1] 8.0s Alphadps (p1); killed by Synth Swirl (900101), overkill 40,000; ttd 0.1s" in text
    )
    assert "50.0s Tankyboi (p2) AFTER CUTOFF" in text
    assert "[ev:SYNTH01:1:win:cataclysm:2] cataclysm #2 at 40.0s: NOT covered (active: none)" in text
    assert "- 900102 Synth Fire Pool: 90,000 (9.9%)" in text
    assert lines[-1] == "ANOMALIES cooldown_unverified:900302"
    # Every record line carries a ref.
    records = [ln for ln in lines if ln.startswith("- [")]
    assert all("[ev:SYNTH01:1:" in ln for ln in records)


def test_render_size_estimate(digest_of):
    # The 1.5k-3k token target applies to the real wipe-heavy fixture once recorded; SYNTH01 is small.
    for fight in (1, 2):
        tokens = estimate_tokens(render_digest(digest_of(fight)))
        assert 100 < tokens < 1_500
    assert estimate_tokens("x" * 400) == 100


def test_render_without_encounter_file(digest_of, tmp_path):
    text = render_digest(digest_of(1, root=tmp_path / "empty"))
    assert "phase unknown" in text and "coverage none" in text
    assert "MECHANICS" not in text and "DAMAGE WINDOWS" not in text
