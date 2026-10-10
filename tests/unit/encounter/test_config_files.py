"""
tests/unit/encounter/test_config_files.py

Cooldown registry and encounter file loading, validation and hashing (plan 6.2).
Every validation rule has a failing case.
"""

from __future__ import annotations

import copy
import shutil

import pytest
import yaml
from helpers.synth import SYNTH_ENCOUNTER_FILE, SYNTH_ENCOUNTER_ID, SYNTH_REGISTRY_FILE

from khadbot.agent.agent_config import ConfigurationError
from khadbot.encounter.config_files import (
    DEFAULT_REGISTRY_PATH,
    encfile_hash,
    fetch_filter_inputs,
    load_all_encounters,
    load_encounter,
    load_encounter_file,
    load_registry,
    parse_encounter,
    parse_registry,
    registry_hash,
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
        shutil.copy(SYNTH_ENCOUNTER_FILE, tmp_path / f"{SYNTH_ENCOUNTER_ID}.yaml")
        assert load_encounter(SYNTH_ENCOUNTER_ID, tmp_path).name == "Synthetic Boss"
        assert load_encounter(12345, tmp_path) is None
        assert set(load_all_encounters(tmp_path)) == {SYNTH_ENCOUNTER_ID}
        assert load_all_encounters(tmp_path / "missing") == {}

    def test_load_by_id_mismatch(self, tmp_path):
        shutil.copy(SYNTH_ENCOUNTER_FILE, tmp_path / "123.yaml")
        with pytest.raises(ConfigurationError, match="expected 123"):
            load_encounter(123, tmp_path)

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
        ],
    )
    def test_validation_failures(self, enc_data, mutate, match):
        mutate(enc_data)
        with pytest.raises(ConfigurationError, match=match):
            parse_encounter(enc_data)

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
