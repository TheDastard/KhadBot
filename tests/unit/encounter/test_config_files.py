"""
tests/unit/encounter/test_config_files.py

Cooldown registry and encounter file loading, validation and hashing.
Every validation rule has a failing case.
"""

from __future__ import annotations

import copy

import pytest
import yaml
from helpers.encounter.synth import (
    SYNTH_ENCOUNTER_FILE,
    SYNTH_ENCOUNTER_ID,
    SYNTH_ENCOUNTER_RELPATH,
    SYNTH_REGISTRY_FILE,
    install_synth_encounter,
)

from khadbot.agent.agent_config import ConfigurationError
from khadbot.encounter.config_files import (
    DEFAULT_REGISTRY_PATH,
    encfile_hash,
    encounter_relpath,
    fetch_filter_inputs,
    load_all_encounters,
    load_encounter,
    load_encounter_file,
    load_encounter_view,
    load_registry,
    parse_encounter,
    parse_registry,
    registry_hash,
    slugify,
)


@pytest.fixture
def enc_data() -> dict:
    return yaml.safe_load(SYNTH_ENCOUNTER_FILE.read_text(encoding="utf-8"))


@pytest.fixture
def reg_data() -> dict:
    return yaml.safe_load(SYNTH_REGISTRY_FILE.read_text(encoding="utf-8"))


# ===========================================================================
# Registry
# ===========================================================================


class TestRegistry:
    def test_default_registry_ports_legacy_ids(self):
        from khadbot.tools.warcraftlogs.encounter import MAJOR_COOLDOWN_IDS

        reg = load_registry()
        assert DEFAULT_REGISTRY_PATH.exists()
        assert {c.spell_id for c in reg.cooldowns} == set(MAJOR_COOLDOWN_IDS)
        assert len(reg.cooldowns) == 17
        assert all(c.cooldown_s is None and c.duration_s is None and not c.verified for c in reg.cooldowns)

    def test_synth_registry(self):
        reg = load_registry(SYNTH_REGISTRY_FILE)
        assert reg.spell_ids() == [900301, 900302]
        assert reg.spell_ids(["raid"]) == [900301]
        assert reg.get(900301).class_name == "Shaman"
        assert reg.get(1) is None

    def test_duplicate_spell_id(self, reg_data):
        reg_data["cooldowns"].append(copy.deepcopy(reg_data["cooldowns"][0]))
        with pytest.raises(ConfigurationError, match="duplicate spell_id 900301"):
            parse_registry(reg_data)

    def test_bad_kind(self, reg_data):
        reg_data["cooldowns"][0]["kind"] = "tank"
        with pytest.raises(ConfigurationError):
            parse_registry(reg_data)

    def test_unknown_key(self, reg_data):
        reg_data["cooldowns"][0]["duration"] = 8
        with pytest.raises(ConfigurationError):
            parse_registry(reg_data)

    def test_yaml_error(self, tmp_path):
        bad = tmp_path / "cooldowns.yaml"
        bad.write_text("cooldowns: [\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="YAML parse error"):
            load_registry(bad)

    def test_hash_changes_with_content(self, reg_data):
        a = parse_registry(reg_data)
        reg_data["cooldowns"][1]["cooldown_s"] = 120
        b = parse_registry(reg_data)
        assert registry_hash(a) == registry_hash(parse_registry(yaml.safe_load(SYNTH_REGISTRY_FILE.read_text())))
        assert registry_hash(a) != registry_hash(b)
        assert len(registry_hash(a)) == 12


# ===========================================================================
# Encounter file
# ===========================================================================


class TestEncounterFile:
    def test_synth_loads(self):
        enc = load_encounter_file(SYNTH_ENCOUNTER_FILE)
        assert enc.encounter_id == SYNTH_ENCOUNTER_ID
        assert [p.id for p in enc.phases] == ["p1", "p2"]
        assert enc.ability_ids() == [900101, 900201, 900401, 900501, 900601]
        assert 900010 in enc.registered_ability_ids()
        assert 900102 not in enc.registered_ability_ids()

    def test_load_by_id(self, tmp_path):
        path = install_synth_encounter(tmp_path)
        assert path.relative_to(tmp_path).as_posix() == SYNTH_ENCOUNTER_RELPATH
        assert load_encounter(SYNTH_ENCOUNTER_ID, tmp_path).name == "Synthetic Boss"
        assert load_encounter(12345, tmp_path) is None
        assert set(load_all_encounters(tmp_path)) == {SYNTH_ENCOUNTER_ID}
        assert load_all_encounters(tmp_path / "missing") == {}

    def test_relpath_from_zone_and_name(self, enc_data):
        enc_data.update(name="Ula'tek", zone={"id": 53, "name": "The Venomous Abyss"})
        assert encounter_relpath(parse_encounter(enc_data)) == "53-the_venomous_abyss/90001-ulatek.yaml"
        assert slugify("Nek'zali the Soulcoiler") == "nekzali_the_soulcoiler"
        assert slugify("Ula’tek") == "ulatek"

    @pytest.mark.parametrize(
        "relpath",
        [
            "9000-synthetic_citadel/90001-wrong_name.yaml",
            "9001-synthetic_citadel/90001-synthetic_boss.yaml",
            "90001-synthetic_boss.yaml",
            "9000-synthetic_citadel/extra/90001-synthetic_boss.yaml",
        ],
    )
    def test_misplaced_file_raises(self, tmp_path, relpath):
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(SYNTH_ENCOUNTER_FILE.read_bytes())
        with pytest.raises(ConfigurationError, match="must live at"):
            load_all_encounters(tmp_path)

    def test_misplaced_file_raises_on_lookup(self, tmp_path):
        path = tmp_path / "9000-synthetic_citadel" / "90001-old_name.yaml"
        path.parent.mkdir(parents=True)
        path.write_bytes(SYNTH_ENCOUNTER_FILE.read_bytes())
        with pytest.raises(ConfigurationError, match="must live at"):
            load_encounter(SYNTH_ENCOUNTER_ID, tmp_path)

    def test_two_files_for_one_encounter_raise(self, tmp_path):
        install_synth_encounter(tmp_path)
        (tmp_path / "9000-synthetic_citadel" / "90001-copy.yaml").write_bytes(SYNTH_ENCOUNTER_FILE.read_bytes())
        with pytest.raises(ConfigurationError, match="several files"):
            load_encounter(SYNTH_ENCOUNTER_ID, tmp_path)

    def test_default_root_files_are_well_formed(self):
        encounters = load_all_encounters()  # raises on an invalid or misplaced file
        assert 3492 in encounters
        assert encounters[3492].difficulties() == ["heroic"]

    @pytest.mark.parametrize(
        ("mutate", "match"),
        [
            (lambda d: d["phases"].append(dict(d["phases"][1])), "duplicate phase"),
            (lambda d: d["mechanics"].append({**d["mechanics"][0], "ability_ids": [1]}), "duplicate mechanic"),
            (lambda d: d["damage_windows"].append(dict(d["damage_windows"][0])), "duplicate damage window"),
            (lambda d: d["mechanics"][1]["ability_ids"].append(900101), "ability 900101 is in mechanics"),
            (lambda d: d["phases"].reverse(), "must use trigger: pull"),
            (lambda d: d["phases"][1].update(start={"trigger": "pull"}), "only the first phase"),
            (lambda d: d["phases"][1].update(start={"trigger": "explode"}), "trigger"),
            (lambda d: d["phases"][1].update(start={"trigger": "boss_cast"}), "requires 'ability_id'"),
            (lambda d: d["phases"][1].update(start={"trigger": "boss_aura_applied"}), "requires 'ability_id'"),
            (lambda d: d["phases"][1].update(start={"trigger": "boss_hp_below"}), "requires 'pct'"),
            (lambda d: d["phases"][1].update(start={"trigger": "time"}), "requires 't_ms'"),
            (lambda d: d["phases"][1].update(start={"trigger": "wcl_phase"}), "requires 'phase'"),
            (lambda d: d["mechanics"][0].update(failure={"rule": "sometimes"}), "rule"),
            (lambda d: d["mechanics"][0].update(failure={"rule": "hits_per_cluster_gt"}), "requires 'value'"),
            (lambda d: d["mechanics"][0].update(failure={"rule": "soak_count_lt"}), "requires 'value'"),
            (lambda d: d["mechanics"][0].update(failure={"rule": "debuff_duration_gt"}), "requires 'ms'"),
            (lambda d: d["mechanics"][0].update(kind="fun"), "kind"),
            (lambda d: d["mechanics"][0].update(severity="extreme"), "severity"),
            (lambda d: d["mechanics"][0].update(ability_ids=[]), "ability_ids"),
            (lambda d: d.update(phases=[]), "phases"),
            (lambda d: d.update(bosses=[]), "bosses"),
            (lambda d: d.pop("zone"), "zone"),
            (lambda d: d["mechanics"][0].update(failure={"rule": "per_cluster"}), "'blame' is required"),
            (
                lambda d: d["mechanics"][0].update(
                    failure={"rule": "any_hit", "blame": {"by": "cast_target", "ability_id": 1}}
                ),
                "'blame' is required",
            ),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "per_cluster", "blame": {"by": "cast_target"}}),
                "requires 'ability_id'",
            ),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "per_cluster", "blame": {"by": "aggro_holder"}}),
                "requires 'npc_game_id'",
            ),
            (
                lambda d: d["mechanics"][0].update(
                    failure={"rule": "per_cluster", "blame": {"by": "aggro_holder", "npc_game_id": 1, "ms": 0}}
                ),
                "greater than 0",
            ),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "per_cluster", "blame": {"by": "tank"}}),
                "by",
            ),
            (lambda d: d["mechanics"][0].update(failure={"rule": "hit_amount_gt"}), "requires 'value'"),
            (lambda d: d["mechanics"][0].update(failure={"rule": "hits_on_non_tank"}), "requires 'ms'"),
            (lambda d: d["phases"][0].pop("difficulties"), "difficulties"),
            (lambda d: d["mechanics"][0].update(difficulties=[]), "difficulties"),
            (lambda d: d["mechanics"][0].update(difficulties=["mythic", "mythic"]), "duplicate difficulty"),
            (lambda d: d["mechanics"][0].update(difficulties=["hard"]), "difficulties"),
            (lambda d: d["mechanics"][0].update(difficulties=["heroic"]), "heroic: entries apply to it but no phase"),
            (lambda d: d.update(ignore_ability_ids=[1]), "ignore_ability_ids"),
        ],
    )
    def test_validation_failures(self, enc_data, mutate, match):
        mutate(enc_data)
        with pytest.raises(ConfigurationError, match=match):
            parse_encounter(enc_data)

    def test_same_id_on_disjoint_difficulties(self, enc_data):
        enc_data["phases"] += [{**p, "difficulties": ["heroic"]} for p in enc_data["phases"]]
        enc_data["mechanics"].append({**enc_data["mechanics"][0], "difficulties": ["heroic"], "severity": "high"})
        enc = parse_encounter(enc_data)
        assert enc.difficulties() == ["heroic", "mythic"]
        assert [m.severity for m in enc.for_difficulty("heroic").mechanics if m.id == "swirl"] == ["high"]
        assert [m.severity for m in enc.for_difficulty(5).mechanics if m.id == "swirl"] == ["medium"]

    def test_same_id_on_overlapping_difficulties_raises(self, enc_data):
        enc_data["phases"] = [{**p, "difficulties": ["heroic", "mythic"]} for p in enc_data["phases"]]
        enc_data["mechanics"].append({**enc_data["mechanics"][0], "difficulties": ["heroic", "mythic"]})
        with pytest.raises(ConfigurationError, match="duplicate mechanic id"):
            parse_encounter(enc_data)

    def test_ability_shared_across_disjoint_difficulties(self, enc_data):
        enc_data["phases"] += [{**p, "difficulties": ["heroic"]} for p in enc_data["phases"]]
        enc_data["mechanics"].append({**enc_data["mechanics"][0], "id": "swirl_hc", "difficulties": ["heroic"]})
        parse_encounter(enc_data)  # 900101 is in one mechanic per difficulty

    def test_for_difficulty_filters_entries(self, enc_data):
        enc_data["phases"] = [{**p, "difficulties": ["heroic", "mythic"]} for p in enc_data["phases"]]
        enc_data["mechanics"][0]["difficulties"] = ["heroic"]
        enc = parse_encounter(enc_data)
        heroic = enc.for_difficulty(4)
        assert [m.id for m in heroic.mechanics] == ["swirl"]
        assert heroic.damage_windows == [] and heroic.ignored_abilities == []
        assert all(e.difficulties == ["heroic"] for e in [*heroic.phases, *heroic.mechanics])
        assert [m.id for m in enc.for_difficulty("mythic").mechanics] == ["shadow_bolt", "curse"]

    @pytest.mark.parametrize("difficulty", [3, 4, 2, None, "normal"])
    def test_for_difficulty_uncovered_is_none(self, difficulty):
        assert load_encounter_file(SYNTH_ENCOUNTER_FILE).for_difficulty(difficulty) is None

    def test_load_view(self, tmp_path):
        install_synth_encounter(tmp_path)
        assert load_encounter_view(SYNTH_ENCOUNTER_ID, 5, tmp_path).name == "Synthetic Boss"
        assert load_encounter_view(SYNTH_ENCOUNTER_ID, 4, tmp_path) is None
        assert load_encounter_view(SYNTH_ENCOUNTER_ID, None, tmp_path) is None
        assert load_encounter_view(12345, 5, tmp_path) is None

    def test_view_hash_ignores_other_difficulties(self, enc_data):
        enc_data["phases"] = [{**p, "difficulties": ["heroic", "mythic"]} for p in enc_data["phases"]]
        before = parse_encounter(enc_data)
        extra = {**enc_data["mechanics"][0], "id": "new", "ability_ids": [7], "difficulties": ["heroic"]}
        enc_data["mechanics"].append(extra)
        after = parse_encounter(enc_data)
        assert encfile_hash(after.for_difficulty(5)) == encfile_hash(before.for_difficulty(5))
        assert encfile_hash(after.for_difficulty(4)) != encfile_hash(before.for_difficulty(4))

    def test_hash(self, enc_data):
        a = parse_encounter(enc_data)
        assert encfile_hash(a) == encfile_hash(load_encounter_file(SYNTH_ENCOUNTER_FILE))
        enc_data["mechanics"][0]["cluster_window_ms"] = 1500
        assert encfile_hash(parse_encounter(enc_data)) != encfile_hash(a)
        assert encfile_hash(None) == "none"

    def test_fetch_filter_inputs(self):
        enc = load_encounter_file(SYNTH_ENCOUNTER_FILE)
        ids, enemy = fetch_filter_inputs(load_registry(SYNTH_REGISTRY_FILE), {enc.encounter_id: enc})
        assert ids == [900301, 900302]
        assert enemy == {SYNTH_ENCOUNTER_ID: [900101, 900201, 900401, 900501, 900601]}
        assert fetch_filter_inputs(None, {}) == (None, {})
