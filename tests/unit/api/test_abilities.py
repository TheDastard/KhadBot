"""
tests/unit/api/test_abilities.py

``GET /v1/reports/{code}/abilities``: the abilities of the cached report metadata with WCL's icon names. Nothing here
calls anything outside the process.
"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest
from fastapi.testclient import TestClient

from khadbot.api import create_app
from khadbot.api.abilities import normalize_icon


def _client(tmp_path: Path, abilities: list[dict]) -> TestClient:
    raw = tmp_path / "raw" / "ABCD1234"
    raw.mkdir(parents=True)
    (raw / "report.json").write_bytes(orjson.dumps({"masterData": {"abilities": abilities}}))
    return TestClient(create_app(mode="local", data_dir=tmp_path))


def test_abilities_carry_normalised_icons_in_id_order(tmp_path):
    client = _client(
        tmp_path,
        [
            {"gameID": 2.0, "name": "Two", "type": 1, "icon": "Spell_Shadow_AntiMagicShell.jpg"},
            {"gameID": 1, "name": "One", "type": 1},  # recorded before the report query asked for icons
            {"gameID": None, "name": "Broken"},
            {"gameID": 3, "icon": "../escape"},
        ],
    )
    response = client.get("/v1/reports/ABCD1234/abilities")
    assert response.status_code == 200
    assert response.json() == {
        "code": "ABCD1234",
        "abilities": [
            {"id": 1, "name": "One", "icon": None},
            {"id": 2, "name": "Two", "icon": "spell_shadow_antimagicshell"},
            {"id": 3, "name": "ability-3", "icon": None},
        ],
    }


def test_a_report_without_abilities_lists_none(tmp_path):
    assert _client(tmp_path, []).get("/v1/reports/ABCD1234/abilities").json()["abilities"] == []


def test_abilities_of_an_unknown_report_is_404(tmp_path):
    client = TestClient(create_app(mode="local", data_dir=tmp_path))
    response = client.get("/v1/reports/NOPE0000/abilities")
    assert (response.status_code, response.json()["code"]) == (404, "report_not_found")


def test_abilities_of_every_recorded_report_match_its_metadata(dataset, client):
    for code in {p for p in [dataset.code]}:
        meta = orjson.loads((dataset.data_dir / "raw" / code / "report.json").read_bytes())
        expected = sorted({int(a["gameID"]) for a in meta["masterData"]["abilities"]})
        got = client.get(f"/v1/reports/{code}/abilities").json()["abilities"]
        assert [a["id"] for a in got] == expected


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Spell_Shadow_AntiMagicShell.JPG", "spell_shadow_antimagicshell"),
        ("inv-sword_01", "inv-sword_01"),
        ("", None),
        (None, None),
        ("a/b", None),
        ("a b", None),
        (5, None),
    ],
)
def test_icon_names(raw, name):
    assert normalize_icon(raw) == name
