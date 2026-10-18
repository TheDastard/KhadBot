"""
tests/unit/encounter/test_config_files.py

Cooldown registry, friendly-damage registry and encounter file loading, validation and hashing.
Every validation rule has a failing case.
"""

from __future__ import annotations

import copy

import pytest
import yaml
from helpers.encounter.synth import (
    SYNTH_DEFENSIVE_COOLDOWNS_FILE,
    SYNTH_ENCOUNTER_FILE,
    SYNTH_ENCOUNTER_ID,
    SYNTH_ENCOUNTER_RELPATH,
    SYNTH_FRIENDLY_DAMAGE,
    SYNTH_OFFENSIVE_COOLDOWNS_FILE,
    install_synth_encounter,
)

from khadbot.agent.agent_config import ConfigurationError
from khadbot.encounter.config_files import (
    DEFAULT_DEFENSIVE_COOLDOWNS_PATH,
    DEFAULT_FRIENDLY_DAMAGE_PATH,
    check_cooldown_ids,
    cooldowns_hash,
    encfile_hash,
    encounter_relpath,
    fetch_filter_inputs,
    friendly_damage_hash,
    load_all_encounters,
    load_cooldowns,
    load_encounter,
    load_encounter_file,
    load_encounter_view,
    load_friendly_damage,
    parse_cooldowns,
    parse_encounter,
    parse_friendly_damage,
    slugify,
)


@pytest.fixture
def enc_data() -> dict:
    return yaml.safe_load(SYNTH_ENCOUNTER_FILE.read_text(encoding="utf-8"))


@pytest.fixture
def reg_data() -> dict:
    return yaml.safe_load(SYNTH_DEFENSIVE_COOLDOWNS_FILE.read_text(encoding="utf-8"))


# ===========================================================================
# Registry
# ===========================================================================


class TestOffensiveRegistry:
    def test_default_file_loads(self):
        assert load_cooldowns("offensive").category == "offensive"

    def test_synth_file(self):
        reg = load_cooldowns("offensive", SYNTH_OFFENSIVE_COOLDOWNS_FILE)
        assert reg.aura_ids() == {902001: 902001, 902011: 902001, 902002: 902002}

    def test_wrong_category_is_rejected(self):
        with pytest.raises(ConfigurationError, match="is a defensive cooldown file, expected offensive"):
            load_cooldowns("offensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE)

    def test_default_files_share_no_ids(self):
        check_cooldown_ids(load_cooldowns("defensive"), load_cooldowns("offensive"))

    @pytest.mark.parametrize("spell", [900301, 900311])
    def test_id_in_both_files_is_rejected(self, spell):
        defensive = load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE)
        offensive = parse_cooldowns(
            {
                "category": "offensive",
                "cooldowns": [{"spell_id": spell, "name": "X", "class": "Mage", "target": "raid"}],
            },
            "offensive",
        )
        with pytest.raises(ConfigurationError, match=f"spell {spell} is in both"):
            check_cooldown_ids(defensive, offensive)


class TestRegistry:
    def test_default_registry_keeps_legacy_ids_still_in_game(self):
        from khadbot.tools.warcraftlogs.encounter import MAJOR_COOLDOWN_IDS

        reg = load_cooldowns("defensive")
        assert DEFAULT_DEFENSIVE_COOLDOWNS_PATH.exists()
        # Ancestral Protection Totem is gone from the game data; Tiger's Lust is movement utility.
        dropped = {207399, 116841}
        assert set(MAJOR_COOLDOWN_IDS) - dropped <= set(reg.spell_ids())
        assert not dropped & set(reg.spell_ids())
        # Ported names corrected from the game data.
        assert [reg.get(i).name for i in (357170, 374227, 97462)] == ["Time Dilation", "Zephyr", "Rallying Cry"]

    def test_default_registry_values_come_from_game_data(self):
        reg = load_cooldowns("defensive")
        assert len(reg.cooldowns) == 64
        # Offensive cooldowns the SimC heuristic misread as defensive stay out.
        assert not {19574, 107574, 191427} & set(reg.spell_ids())  # Bestial Wrath, Avatar, Havoc Metamorphosis
        assert all(c.cooldown_s is not None and c.cooldown_s >= 60 for c in reg.cooldowns)
        assert not any(c.verified for c in reg.cooldowns)
        # Instant heals have no duration; everything else lasts.
        no_duration = {c.name for c in reg.cooldowns if c.duration_s is None}
        assert no_duration == {"Exhilaration", "Holy Word: Sanctify", "Lay on Hands", "Restoral", "Revival"}
        keys = [(c.class_name, c.name, c.spell_id) for c in reg.cooldowns]
        assert keys == sorted(keys)

    def test_synth_registry(self):
        reg = load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE)
        assert reg.spell_ids() == [900301, 900302]
        assert reg.spell_ids(["raid"]) == [900301]
        assert reg.aura_ids() == {900301: 900301, 900311: 900301, 900302: 900302}
        assert reg.aura_ids(["external"]) == {900302: 900302}
        assert reg.get(900301).class_name == "Shaman"
        assert reg.get(1) is None

    def test_default_registry_aura_ids(self):
        auras = load_cooldowns("defensive").aura_ids(["raid"])
        assert auras[97463] == 97462  # Rallying Cry
        assert auras[145629] == 51052  # Anti-Magic Zone
        assert auras[98007] == 98008  # Spirit Link Totem
        assert auras[81782] == 62618  # Power Word: Barrier

    @pytest.mark.parametrize("aura", [900302, 900301, 900311])
    def test_aura_id_used_twice(self, reg_data, aura):
        # Another entry's cast ID, the entry's own cast ID, or another entry's aura ID.
        reg_data["cooldowns"][1]["aura_spell_ids"] = [aura]
        if aura == 900302:
            reg_data["cooldowns"][0]["aura_spell_ids"] = [aura]
        with pytest.raises(ConfigurationError, match=f"aura_spell_id {aura} .* already used"):
            parse_cooldowns(reg_data, "defensive")

    def test_duplicate_spell_id(self, reg_data):
        reg_data["cooldowns"].append(copy.deepcopy(reg_data["cooldowns"][0]))
        with pytest.raises(ConfigurationError, match="duplicate spell_id 900301"):
            parse_cooldowns(reg_data, "defensive")

    def test_bad_target(self, reg_data):
        reg_data["cooldowns"][0]["target"] = "tank"
        with pytest.raises(ConfigurationError):
            parse_cooldowns(reg_data, "defensive")

    def test_old_kind_key_is_rejected(self, reg_data):
        entry = reg_data["cooldowns"][0]
        entry["kind"] = entry.pop("target")
        with pytest.raises(ConfigurationError, match="kind"):
            parse_cooldowns(reg_data, "defensive")

    def test_category_is_required(self, reg_data):
        del reg_data["category"]
        with pytest.raises(ConfigurationError, match="category"):
            parse_cooldowns(reg_data, "defensive")

    def test_unknown_key(self, reg_data):
        reg_data["cooldowns"][0]["duration"] = 8
        with pytest.raises(ConfigurationError):
            parse_cooldowns(reg_data, "defensive")

    def test_yaml_error(self, tmp_path):
        bad = tmp_path / "cooldowns.yaml"
        bad.write_text("cooldowns: [\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="YAML parse error"):
            load_cooldowns("defensive", bad)

    def test_hash_changes_with_content(self, reg_data):
        a = parse_cooldowns(reg_data, "defensive")
        reg_data["cooldowns"][1]["cooldown_s"] = 120
        b = parse_cooldowns(reg_data, "defensive")
        assert cooldowns_hash(a) == cooldowns_hash(
            parse_cooldowns(yaml.safe_load(SYNTH_DEFENSIVE_COOLDOWNS_FILE.read_text()), "defensive")
        )
        assert cooldowns_hash(a) != cooldowns_hash(b)
        assert len(cooldowns_hash(a)) == 12


# ===========================================================================
# Friendly-damage registry
# ===========================================================================


class TestFriendlyDamageRegistry:
    def test_default_registry_loads(self):
        reg = load_friendly_damage()
        assert DEFAULT_FRIENDLY_DAMAGE_PATH.exists()
        assert reg.get(98021).class_name == "redistribution"  # Spirit Link
        assert reg.get(32409).class_name == "self_inflicted"  # Shadow Word: Death
        assert reg.get(1292299).class_name == "involuntary"  # Seriously Sharp Seashell, a trinket proc
        assert reg.get(1309786).class_name == "delayed_boss"  # Refraction
        assert [reg.get(i).origin for i in (361029, 1287955, 1292299)] == ["class", "borrowed_power", "item"]
        # Entries from the recorded nights are verified; those proposed from game data are not yet.
        from_data = {124255, 324393, 67481, 87023}  # Stagger x2, Roar of Sacrifice, Cauterize
        assert {e.ability_id for e in reg.abilities if not e.verified} == from_data
        assert [reg.get(i).class_name for i in sorted(from_data)] == [
            "redistribution", "delayed_boss", "delayed_boss", "delayed_boss"
        ]  # fmt: skip
        assert reg.get(1) is None

    def test_synth_registry(self):
        reg = parse_friendly_damage(SYNTH_FRIENDLY_DAMAGE)
        assert reg.ability_ids() == [901001, 901002]
        assert reg.ability_ids(["self_inflicted"]) == [901001]
        assert reg.ability_ids(["delayed_boss"]) == []
        assert not reg.get(901002).verified

    def test_verified_defaults_to_false(self):
        reg = parse_friendly_damage({"abilities": [{"ability_id": 1, "name": "X", "class": "redistribution"}]})
        assert reg.abilities[0].verified is False
        assert reg.abilities[0].origin is None

    def test_duplicate_ability_id(self):
        data = copy.deepcopy(SYNTH_FRIENDLY_DAMAGE)
        data["abilities"].append({**data["abilities"][0], "class": "delayed_boss"})
        with pytest.raises(ConfigurationError, match="duplicate ability_id 901001"):
            parse_friendly_damage(data)

    @pytest.mark.parametrize(
        "change",
        [
            {"class": "boss"},  # not a class
            {"class": None},
            {"origin": "season"},  # not an origin
            {"kind": "self_inflicted"},  # unknown key
        ],
    )
    def test_invalid_entry(self, change):
        data = copy.deepcopy(SYNTH_FRIENDLY_DAMAGE)
        data["abilities"][0].update(change)
        with pytest.raises(ConfigurationError, match="friendly-damage registry"):
            parse_friendly_damage(data)

    def test_missing_class(self):
        data = copy.deepcopy(SYNTH_FRIENDLY_DAMAGE)
        del data["abilities"][0]["class"]
        with pytest.raises(ConfigurationError):
            parse_friendly_damage(data)

    def test_yaml_error(self, tmp_path):
        bad = tmp_path / "friendly_damage.yaml"
        bad.write_text("abilities: [\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="YAML parse error"):
            load_friendly_damage(bad)

    def test_hash_changes_with_content(self):
        a = parse_friendly_damage(SYNTH_FRIENDLY_DAMAGE)
        data = copy.deepcopy(SYNTH_FRIENDLY_DAMAGE)
        data["abilities"][1]["verified"] = True
        assert friendly_damage_hash(a) == friendly_damage_hash(parse_friendly_damage(SYNTH_FRIENDLY_DAMAGE))
        assert friendly_damage_hash(a) != friendly_damage_hash(parse_friendly_damage(data))
        assert len(friendly_damage_hash(a)) == 12

    def test_no_encounter_file_ignores_a_registry_ability(self):
        # Player damage is classified in one place: the registry, not encounter files' ignored_abilities.
        reg_ids = set(load_friendly_damage().ability_ids())
        for enc in load_all_encounters().values():
            assert not reg_ids & {a.ability_id for a in enc.ignored_abilities}, enc.name


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
            (
                lambda d: d["mechanics"][0].update(
                    failure={"rule": "per_cluster", "blame": {"by": "aggro_holder", "npc_game_id": 1, "reach_yd": 0}}
                ),
                "greater than 0",
            ),
            (
                lambda d: d["mechanics"][0].update(
                    failure={"rule": "per_cluster", "blame": {"by": "cast_target", "ability_id": 1, "reach_yd": -3}}
                ),
                "greater than 0",
            ),
            (lambda d: d["mechanics"][0].update(failure={"rule": "hit_amount_gt"}), "requires 'value'"),
            (lambda d: d["mechanics"][0].update(failure={"rule": "hits_on_non_tank"}), "requires 'ms'"),
            (lambda d: d["mechanics"][0].update(failure={"rule": "debuff_stacks_gt"}), "requires 'value'"),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "debuff_stacks_gt", "value": -1}),
                "'value' >= 0",
            ),
            (lambda d: d["mechanics"][0].update(failure={"rule": "hit_while_aura"}), "requires 'aura_ids'"),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "hit_while_aura", "aura_ids": []}),
                "aura_ids",
            ),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "any_hit", "aura_ids": [1]}),
                "'aura_ids' is required",
            ),
            (lambda d: d["mechanics"][0].update(failure={"rule": "aura_removed_by"}), "requires 'ability_ids'"),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "aura_removed_by", "ability_ids": []}),
                "ability_ids",
            ),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "any_hit", "ability_ids": [1]}),
                "'ability_ids' is required",
            ),
            (
                lambda d: d["mechanics"][0].update(
                    failure={"rule": "aura_removed_by", "ability_ids": [1], "followed_by_ids": []}
                ),
                "followed_by_ids",
            ),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "any_hit", "followed_by_ids": [1]}),
                "'followed_by_ids' and 'on_death' are allowed",
            ),
            (
                lambda d: d["mechanics"][0].update(failure={"rule": "any_hit", "on_death": True}),
                "'followed_by_ids' and 'on_death' are allowed",
            ),
            (
                lambda d: d["mechanics"][0].update(
                    failure={
                        "rule": "aura_removed_by",
                        "ability_ids": [1],
                        "blame": {"by": "cast_target", "ability_id": 1},
                    }
                ),
                "'blame' is required",
            ),
            (
                lambda d: d["mechanics"][0].update(
                    failure={"rule": "debuff_stacks_gt", "value": 2, "blame": {"by": "cast_target", "ability_id": 1}}
                ),
                "'blame' is required",
            ),
            (
                lambda d: d["mechanics"][0].update(
                    failure={
                        "rule": "hit_while_aura",
                        "aura_ids": [1],
                        "blame": {"by": "cast_target", "ability_id": 1},
                    }
                ),
                "'blame' is required",
            ),
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

    def test_blame_reach_is_optional_on_both_blame_kinds(self, enc_data):
        enc_data["mechanics"][0]["failure"] = {"rule": "per_cluster", "blame": {"by": "cast_target", "ability_id": 1}}
        assert parse_encounter(enc_data).mechanics[0].failure.blame.reach_yd is None
        for blame in ({"by": "cast_target", "ability_id": 1}, {"by": "aggro_holder", "npc_game_id": 1}):
            enc_data["mechanics"][0]["failure"] = {"rule": "per_cluster", "blame": {**blame, "reach_yd": 17.5}}
            assert parse_encounter(enc_data).mechanics[0].failure.blame.reach_yd == 17.5

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
        ids, enemy = fetch_filter_inputs(
            load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE), {enc.encounter_id: enc}
        )
        assert ids == [900301, 900311, 900302]
        assert enemy == {SYNTH_ENCOUNTER_ID: [900101, 900201, 900401, 900501, 900601]}
        assert fetch_filter_inputs(None, {}) == (None, {})
