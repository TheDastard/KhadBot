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
from collections import Counter
from pathlib import Path

import orjson
import polars as pl
import pytest
from helpers.encounter.real_fixtures import REAL_PULLS, REAL_REPORTS, WCL_FIXTURES, ingest_fixture
from helpers.encounter.synth import GOLDEN

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


@pytest.mark.parametrize("pull_key", REAL_PULLS)
def test_death_rows_carry_the_killing_blow(real_store, pull_key):
    code, fight = pull_key.split(":")
    raw = [
        e
        for page in sorted((WCL_FIXTURES / code / fight / "Deaths").rglob("page_*.json"))
        for e in orjson.loads(page.read_bytes())["data"]
        if e["type"] == "death"
    ]
    assert raw and all(e["abilityGameID"] == 0 and e["sourceID"] == -1 for e in raw)  # WCL's placeholders
    deaths = real_store.events(pull_key, types=["death"]).filter(pl.col("data_type").cast(pl.Utf8) == "Deaths")
    expected = [(e["targetID"], e["killerID"], e["killingAbilityGameID"]) for e in raw]
    assert deaths.select("target_id", "source_id", "ability_id").rows() == expected


@pytest.mark.parametrize("pull_key", REAL_PULLS)
def test_no_death_names_the_placeholder_ability(real_store, registry, pull_key):
    digest = build_digest(pull_key, real_store, registry=registry)
    assert digest.deaths
    assert all(d.killing_ability_id not in (None, 0) for d in digest.deaths)
    assert "Unknown Ability (0)" not in render_digest(digest)


@pytest.mark.parametrize("pull_key", REAL_PULLS)
def test_no_player_ability_is_unregistered_or_unclassified(real_store, registry, pull_key):
    # Every friendly-source ability on the recorded pulls is in the friendly-damage registry, so none is left in
    # the unregistered list and none is unclassified.
    digest = build_digest(pull_key, real_store, registry=registry, unregistered_cap=100)
    events = real_store.events(pull_key, types=["damage"])
    player_ids = set(events.filter(pl.col("source_friendly") & pl.col("target_friendly"))["ability_id"].to_list())
    assert player_ids
    assert not player_ids & {u.ability_id for u in digest.unregistered}
    assert digest.friendly_damage.unclassified == []
    assert digest.friendly_damage.damage > 0
    if digest.encounter_name == "Ula'tek":
        assert digest.unregistered == [] and digest.coverage == "full"


def test_every_fixture_pull_has_both_progress_figures(real_store):
    for code in REAL_REPORTS:
        for pull in real_store.pulls(code):
            assert pull.boss_pct is not None and pull.boss_hp_pct is not None, pull.pull_key


# ===========================================================================
# Blame context on the recorded pulls
# ===========================================================================

# Per failure (one per cluster of the mechanic's hits): its blame state and the blamed player's distance, and how
# many casts of the mechanic the cluster covers. Every cast is its own cluster: a cast's hits land within 60 ms
# and its first hit within 139 ms of the cast, and recasts come 2.4 s or more apart.
BLAME_CASES = {
    ("H7z1fqDXjRTFnbtg:10", "unchecked_rage"): [("dead", None, 1)] * 8,
    ("H7z1fqDXjRTFnbtg:10", "rattler_slam"): [("no_holder", None, 1), ("no_holder", None, 1)],
    ("trMgApZCVYkv3jdq:18", "unchecked_rage"): [("beyond_reach", 30.9, 1), ("beyond_reach", 49.6, 1)],
    ("H7z1fqDXjRTFnbtg:20", "unchecked_rage"): [("beyond_reach", 43.6, 1)],
    ("QkVa6GHRK8yfrTbw:14", "rattler_slam"): [
        ("beyond_reach", 21.1, 1),
        ("beyond_reach", 51.9, 1),
        ("beyond_reach", 50.3, 1),
        ("beyond_reach", 50.7, 1),
        ("beyond_reach", 21.8, 1),
    ],
}
ABILITY = {"unchecked_rage": 1301007, "rattler_slam": 1299206}
REACH = {"unchecked_rage": 21.0, "rattler_slam": 18.0}


@pytest.fixture(scope="module")
def real_data(real_store):
    from khadbot.encounter.evidence import EncounterData

    return EncounterData(real_store)


@pytest.mark.parametrize(("pull_key", "mechanic_id"), list(BLAME_CASES))
def test_blame_states_on_recorded_pulls(real_store, real_data, pull_key, mechanic_id):
    if pull_key not in REAL_PULLS:
        pytest.skip(f"{pull_key} is not recorded")
    detail = real_data.resolve(f"ev:{pull_key}:mech:{mechanic_id}")
    casts = real_store.events(pull_key, types=["cast"], ability_ids=[ABILITY[mechanic_id]])["t_ms"].to_list()
    got = [
        (c["blame_state"], c.get("blame_dist_yd"), sum(c["t_ms"] - 1_000 <= t <= c["end_ms"] for t in casts))
        for c in detail["cluster_rows"]
    ]
    assert got == BLAME_CASES[(pull_key, mechanic_id)]
    expected = BLAME_CASES[(pull_key, mechanic_id)]
    assert detail["blame_states"] == dict(sorted(Counter(s for s, _, _ in expected).items()))
    judged = [c for c in detail["cluster_rows"] if c["blame_state"] != "no_holder"]
    assert all(c["blame_reach_yd"] == REACH[mechanic_id] for c in judged)


def test_other_recorded_pulls_have_no_unexpected_blame(real_data):
    for pull_key in REAL_PULLS:
        for m in real_data.records(pull_key).mechanics:
            if m.mechanic_id in ABILITY and m.failures:
                assert (pull_key, m.mechanic_id) in BLAME_CASES, (pull_key, m.mechanic_id, m.blame_states)


def test_digest_line_renders_the_blame_clause(real_data):
    if "QkVa6GHRK8yfrTbw:14" not in REAL_PULLS:
        pytest.skip("QkVa6GHRK8yfrTbw:14 is not recorded")
    text = render_digest(real_data.digest("QkVa6GHRK8yfrTbw:14"))
    line = next(x for x in text.splitlines() if "mech:rattler_slam]" in x)
    assert line.endswith("; blamed: 5 beyond reach (21–52 yd)")


def test_verify_checks_a_blamed_players_distance(real_data):
    from khadbot.encounter.analyst.verify import check_finding
    from khadbot.encounter.evidence import claim_numbers, number_supported, numeric_values
    from khadbot.encounter.models import Finding

    pull_key = "trMgApZCVYkv3jdq:18"
    if pull_key not in REAL_PULLS:
        pytest.skip(f"{pull_key} is not recorded")
    ref = f"ev:{pull_key}:mech:unchecked_rage"

    def finding(claim: str) -> Finding:
        return Finding(id="req00000-1", claim=claim, refs=[ref], confidence="high", scope="pull")

    claim = "Shinrael was 31 yards from Ula'tek when Unchecked Rage fired."
    assert check_finding(finding(claim), real_data) is None
    assert check_finding(finding(claim.replace("31", "41")), real_data) == "unsupported number 41"
    # Only the blame fields support the distance, so the check is not passing by coincidence.
    detail = real_data.resolve(ref)
    for row in detail["cluster_rows"]:
        for key in ("blame_dist_yd", "blame_reach_yd"):
            row.pop(key)
    detail.pop("blame_dist_yd")
    [n31] = claim_numbers(claim)
    assert not number_supported(n31, numeric_values(detail))
