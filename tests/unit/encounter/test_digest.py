"""
tests/unit/encounter/test_digest.py

Pull digest: golden JSON per SYNTH01 pull, caps and ordering, coverage levels, friendly damage, every
anomaly code, rendering and its size estimate. Regenerate goldens with
``uv run pytest tests/unit/encounter/test_digest.py --update-goldens`` after an intentional change.
"""

from __future__ import annotations

import orjson
import polars as pl
import pytest
import yaml
from helpers.encounter.synth import (
    GOLDEN,
    SYNTH_ENCOUNTER_FILE,
    SYNTH_FRIENDLY_DAMAGE,
    SYNTH_UNCLASSIFIED_ID,
    add_friendly_damage,
    install_synth_encounter,
)

from khadbot.encounter import digest as digest_mod
from khadbot.encounter.config_files import (
    encfile_hash,
    friendly_damage_hash,
    load_encounter_file,
    load_friendly_damage,
    parse_friendly_damage,
    registry_hash,
)
from khadbot.encounter.digest import build_digest, estimate_tokens, render_digest
from khadbot.encounter.ingest.normalize import events_hash


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
        "positions_hash": synth_ingested.positions_hash("SYNTH01:1"),
        "encfile_hash": encfile_hash(load_encounter_file(SYNTH_ENCOUNTER_FILE)),
        "registry_hash": registry_hash(synth_registry),
        "friendly_damage_hash": friendly_damage_hash(load_friendly_damage()),
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
    assert d.phase_reached == "Synth Stage Two"  # WCL's name for the last phase transition
    assert d.produced_from["encfile_hash"] == "none"
    assert not any(a.startswith("unregistered_damage") for a in d.anomalies)


def test_coverage_partial(digest_of, tmp_path):
    root = write_enc(tmp_path, lambda d: d.update(ignored_abilities=[]))  # boss melee becomes unregistered
    d = digest_of(1, root=root)
    assert d.coverage == "partial"
    assert d.unregistered[0].ability_id == 900010
    assert "unregistered_damage:41.0" in d.anomalies


# ===========================================================================
# Friendly damage
# ===========================================================================

SYNTH_FRIENDLY = parse_friendly_damage(SYNTH_FRIENDLY_DAMAGE)


@pytest.fixture
def friendly_of(synth_ingested, digest_of):
    """Digest of SYNTH01 fight 1 with the friendly damage rows added, under the synthetic friendly registry."""
    add_friendly_damage(synth_ingested)

    def build(**kw):
        return digest_of(1, friendly=SYNTH_FRIENDLY, **kw)

    return build


def test_friendly_damage_is_not_unregistered(friendly_of):
    d = friendly_of()
    assert [u.ability_id for u in d.unregistered] == [900102]  # Fire Pool only; no friendly ability
    assert d.unregistered[0].share_pct == 9.8  # 90,000 of 923,000 damage taken (909,000 before)
    assert d.coverage == "full"


def test_friendly_damage_section(friendly_of):
    fd = friendly_of().friendly_damage
    assert (fd.damage, fd.share_pct) == (14_000, 1.5)
    assert fd.by_class == {"redistribution": 5_000, "self_inflicted": 5_000, "unclassified": 4_000}
    assert [(p.name, p.damage, [(x.ability_id, x.damage) for x in p.abilities]) for p in fd.self_inflicted] == [
        ("Betadps", 3_000, [(901001, 3_000)]),
        ("Alphadps", 2_000, [(901001, 2_000)]),
    ]
    assert [(u.ability_id, u.damage, u.share_pct) for u in fd.unclassified] == [(SYNTH_UNCLASSIFIED_ID, 4_000, 0.4)]


def test_death_window_self_damage(friendly_of):
    deaths = friendly_of().deaths
    assert [x.window_self_damage for x in deaths] == [2_000, 0, 0, 0]
    assert deaths[0].window_damage == 116_000  # the 114,000 before plus the 2,000 Self Burn


def test_friendly_damage_alone_never_lowers_coverage(synth_ingested, digest_of):
    add_friendly_damage(synth_ingested, rows=[(16_000, 5, 1, SYNTH_UNCLASSIFIED_ID, 900_000)])
    d = digest_of(1, friendly=SYNTH_FRIENDLY)
    assert d.coverage == "full"
    assert d.friendly_damage.by_class == {"unclassified": 900_000}
    assert "unclassified_friendly_damage:49.8" in d.anomalies  # 900,000 of 1,809,000
    assert not any(a.startswith("unregistered_damage") for a in d.anomalies)


def test_unclassified_anomaly_needs_an_encounter_file(synth_ingested, digest_of, tmp_path):
    add_friendly_damage(synth_ingested, rows=[(16_000, 5, 1, SYNTH_UNCLASSIFIED_ID, 900_000)])
    d = digest_of(1, root=tmp_path / "empty", friendly=SYNTH_FRIENDLY)
    assert not any(a.startswith("unclassified_friendly") for a in d.anomalies)


def test_friendly_damage_unverified(friendly_of, digest_of):
    # Synth Link (901002) is unverified and dealt damage; Self Burn is verified.
    assert friendly_of().anomalies == ["cooldown_unverified:900302", "friendly_damage_unverified:901002"]
    assert digest_of(2, friendly=SYNTH_FRIENDLY).anomalies == []  # fight 2 has no friendly damage


def test_unregistered_cap_widens_the_unclassified_list(synth_ingested, digest_of):
    add_friendly_damage(synth_ingested, rows=[(16_000 + i, 5, 1, 901_010 + i, 1_000 + i) for i in range(7)])
    assert len(digest_of(1, friendly=SYNTH_FRIENDLY).friendly_damage.unclassified) == 5
    assert len(digest_of(1, friendly=SYNTH_FRIENDLY, unregistered_cap=20).friendly_damage.unclassified) == 7


def test_produced_from_tracks_the_friendly_registry(digest_of):
    d = digest_of(1, friendly=SYNTH_FRIENDLY)
    assert d.produced_from["friendly_damage_hash"] == friendly_damage_hash(SYNTH_FRIENDLY)
    assert d.produced_from["friendly_damage_hash"] != digest_of(1).produced_from["friendly_damage_hash"]


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


def test_phase_trigger_unmatched_when_wcl_saw_the_phase(digest_of, tmp_path):
    # Both pulls have WCL transitions into phase 2, but p2's trigger now names a spell the boss never casts.
    root = write_enc(tmp_path, lambda d: d["phases"][1]["start"].update(ability_id=999_999))
    for fight in (1, 2):
        d = digest_of(fight, root=root)
        assert "phase_trigger_unmatched:p2" in d.anomalies
        assert d.phase_reached == "p1"


def test_phase_trigger_unmatched_not_raised_for_a_phase_wcl_never_saw(digest_of, tmp_path):
    # A later phase neither pull reached by WCL's transitions; WCL lastPhase plays no part.
    root = write_enc(
        tmp_path,
        lambda d: d["phases"].append(
            {"id": "p3", "name": "P3", "difficulties": ["mythic"], "start": {"trigger": "time", "t_ms": 999_999}}
        ),
    )
    for fight in (1, 2):
        assert not any(a.startswith("phase_trigger") for a in digest_of(fight, root=root).anomalies)


def test_wcl_phase_trigger_unmatched(digest_of, tmp_path):
    # WCL phase 2 began at 35 s in both pulls; a p3 waiting for it after p2 (detected at 30 s or 35 s) can miss it.
    root = write_enc(
        tmp_path,
        lambda d: d["phases"].append(
            {"id": "p3", "name": "P3", "difficulties": ["mythic"], "start": {"trigger": "wcl_phase", "phase": 2}}
        ),
    )
    assert "phase_trigger_unmatched:p3" in digest_of(1, root=root).anomalies  # p2 at 35 s: same ms, not after
    assert digest_of(2, root=root).phase_reached == "p3"  # p2 at 30 s, WCL phase 2 at 35 s
    assert not any(a.startswith("phase_trigger") for a in digest_of(2, root=root).anomalies)


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


def test_render_friendly_damage(friendly_of):
    text = render_digest(friendly_of())
    assert (
        "SELF DAMAGE [ev:SYNTH01:1:pull] friendly 14,000 (1.5%): redistribution 5,000, self inflicted 5,000,"
        " unclassified 4,000" in text
    )
    assert "- Betadps 3,000: 901001 3,000\n- Alphadps 2,000: 901001 2,000" in text
    assert "last 10s: taken 116,000, healed 14,000, self-inflicted 2,000;" in text
    assert "UNCLASSIFIED FRIENDLY DAMAGE\n- 901003 unknown: 4,000 (0.4%)" in text
    assert text.index("SELF DAMAGE") < text.index("UNREGISTERED DAMAGE") < text.index("UNCLASSIFIED FRIENDLY")


def test_render_without_friendly_damage(digest_of):
    text = render_digest(digest_of(2))
    assert "SELF DAMAGE" not in text and "UNCLASSIFIED FRIENDLY" not in text and "self-inflicted" not in text


def test_render_size_estimate(digest_of):
    # The 1.5k-3k token target applies to the real wipe-heavy fixture once recorded; SYNTH01 is small.
    for fight in (1, 2):
        tokens = estimate_tokens(render_digest(digest_of(fight)))
        assert 100 < tokens < 1_500
    assert estimate_tokens("x" * 400) == 100


def test_render_without_encounter_file(digest_of, tmp_path):
    text = render_digest(digest_of(1, root=tmp_path / "empty"))
    assert "reached Synth Stage Two" in text and "coverage none" in text
    assert "MECHANICS" not in text and "DAMAGE WINDOWS" not in text


# ===========================================================================
# Blame context
# ===========================================================================


def _stat(**kw):
    from khadbot.encounter.models import MechanicStat

    base = dict(
        ref="ev:SYNTH01:1:mech:m",
        mechanic_id="m",
        clusters=4,
        hits=40,
        failures=4,
        failures_by_player={"Tankyboi": 3},
        damage_taken=1_000,
        deaths_in_window=0,
    )
    return MechanicStat(**{**base, **kw})


def test_blame_clause_lists_states_in_order_with_distance_ranges():
    stat = _stat(
        blame_states={"no_holder": 1, "dead": 1, "beyond_reach": 2, "in_reach": 1},
        blame_dist_yd={"beyond_reach": (30.9, 55.5), "in_reach": (12.0, 12.0)},
    )
    assert digest_mod._blame_clause(stat) == "blamed: 2 beyond reach (31–56 yd), 1 in reach (12 yd), 1 dead, 1 none"
    assert digest_mod._blame_clause(
        _stat(blame_states={"no_position": 1, "alive": 2}, blame_dist_yd={"alive": (5.0, 9.4)})
    ) == ("blamed: 2 alive (5–9 yd), 1 no position")


def test_blame_fields_are_left_out_when_empty_and_old_records_load():
    from khadbot.encounter.models import MechanicCluster

    plain = _stat()
    assert "blame_states" not in plain.model_dump() and "blame_dist_yd" not in orjson.loads(plain.model_dump_json())
    assert _stat(**orjson.loads(plain.model_dump_json())) == plain  # a record stored before the fields existed
    cluster = MechanicCluster(
        cluster=1, t_ms=0, end_ms=10, phase_id=None, hits=2, players=2, damage=5, failures=1, failed_players=[]
    )
    assert set(cluster.model_dump()) == {
        "cluster", "t_ms", "end_ms", "phase_id", "hits", "players", "damage", "failures", "failed_players"
    }  # fmt: skip
    judged = cluster.model_copy(update={"blamed": "Tankyboi", "blame_state": "dead"}).model_dump()
    assert (judged["blamed"], judged["blame_state"]) == ("Tankyboi", "dead") and "blame_dist_yd" not in judged


def test_synth_mechanics_carry_no_blame_context(digest_of):
    assert all(not m.blame_states and not m.blame_dist_yd for m in digest_of(1).mechanics)
    assert "blamed:" not in render_digest(digest_of(1))
