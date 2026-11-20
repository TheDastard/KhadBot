"""
tests/unit/api/test_boss_art.py

The boss art registry loader: the shipped file loads with the ten Venomous Abyss bosses, and a malformed file (not YAML,
unknown field, bad ID, a portrait name with a path character, a repeated encounter ID) raises ``ConfigurationError``.
"""

from __future__ import annotations

import pytest

from khadbot.api.boss_art import DEFAULT_BOSS_ART_PATH, load_boss_art, portrait_key
from khadbot.errors import ConfigurationError


def _write(tmp_path, text: str):
    path = tmp_path / "boss_art.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _entry(**over) -> str:
    fields = {"encounter_id": 1, "name": "Boss", "portrait": '"Brute"', "verified": "false", **over}
    return "  - {" + ", ".join(f"{k}: {v}" for k, v in fields.items()) + "}\n"


def test_the_shipped_registry_loads():
    registry = load_boss_art(DEFAULT_BOSS_ART_PATH)
    assert len(registry) == 10
    assert registry[3492].portrait == "Ulatek" and registry[3492].kill_achievement_id == 63476
    assert registry[3513].portrait == "Kithix" and registry[3379].journal_encounter_id == 2849
    assert all(b.verified for b in registry.values())


def test_portrait_key():
    assert portrait_key("Ulatek") == "art/portraits/Ulatek.png"


def test_a_minimal_entry_loads_with_nulls(tmp_path):
    registry = load_boss_art(_write(tmp_path, "bosses:\n" + _entry(portrait="null")))
    assert registry[1].portrait is None and registry[1].kill_achievement_id is None


@pytest.mark.parametrize(
    "text",
    [
        "bosses: [",
        "{}",
        "bosses:\n" + _entry(colour="red"),
        "bosses:\n" + _entry(encounter_id=0),
        "bosses:\n" + _entry(verified="maybe"),
        "bosses:\n" + _entry() + _entry(name="Other"),
        "bosses:\n" + _entry(portrait='"../x"'),
        "bosses:\n" + _entry(portrait='"a\\b"'),
        "bosses:\n" + _entry(portrait='"a:b"'),
        "bosses:\n" + _entry(portrait='".hidden"'),
    ],
)
def test_malformed_files_fail_at_load(tmp_path, text):
    with pytest.raises(ConfigurationError):
        load_boss_art(_write(tmp_path, text))


def test_a_missing_file_fails(tmp_path):
    with pytest.raises(ConfigurationError):
        load_boss_art(tmp_path / "nope.yaml")
