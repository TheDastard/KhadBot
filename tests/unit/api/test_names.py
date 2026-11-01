"""
tests/unit/api/test_names.py

The display-name lookup (``GET /v1/encounters/{id}/names``), the cooldown names (``GET /v1/cooldowns``) and the
player list (``GET /v1/reports/{code}/players``). Expected values come from the encounter files, the cooldown files
and the cached report metadata themselves.
"""

from __future__ import annotations

from typing import get_args

import orjson
import pytest
from fastapi.testclient import TestClient
from helpers.encounter.real_fixtures import WCL_FIXTURES

from khadbot.api import create_app
from khadbot.api.names import cooldowns_response, short_phase_name
from khadbot.api.schemas import PlayerClass
from khadbot.encounter.config_files import load_all_encounters, load_cooldowns, load_encounter_view

ULATEK, HEROIC = 3492, 4
NYMRISSA, MYTHIC = 3379, 5


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def _registries(ds):
    if ds.registries:
        return [ds.registries[c] for c in ("defensive", "offensive", "utility")]
    return [load_cooldowns(c) for c in ("defensive", "offensive", "utility")]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def _phases(enc) -> dict:
    return {x.id: {"name": x.name, "short_name": short_phase_name(x.name)} for x in enc.phases} if enc else {}


def test_names_for_each_stored_encounter_and_difficulty(dataset, client):
    for p in {(p.encounter_id, p.difficulty): p for p in dataset.store.pulls(dataset.code)}.values():
        if p.difficulty is None:
            continue
        body = client.get(f"/v1/encounters/{p.encounter_id}/names?difficulty={p.difficulty}").json()
        enc = load_encounter_view(p.encounter_id, p.difficulty, dataset.enc_root)
        assert (body["encounter_id"], body["difficulty"]) == (p.encounter_id, p.difficulty)
        assert body["encounter_name"] == (enc.name if enc else None)
        assert body["phases"] == _phases(enc)
        assert body["mechanics"] == ({x.id: x.name for x in enc.mechanics} if enc else {})
        assert body["damage_windows"] == ({x.id: x.name for x in enc.damage_windows} if enc else {})
        assert "cooldowns" not in body


def test_names_cover_every_id_the_digest_carries(dataset, client):
    spells = {int(k) for k in client.get("/v1/cooldowns").json()["cooldowns"]}
    for fight in dataset.fights:
        r = dataset.records(fight)
        names = client.get(f"/v1/encounters/{r.pull.encounter_id}/names?difficulty={r.pull.difficulty}").json()
        if r.enc is None:
            continue
        assert {m.mechanic_id for m in r.mechanics} <= set(names["mechanics"])
        assert {w.window_id for w in r.windows} <= set(names["damage_windows"])
        assert {s.phase_id for s in r.annotated.phases} <= set(names["phases"])
        assert {c.spell_id for c in r.cooldowns} <= spells
        assert {s for d in r.deaths for s in d.active_defensives} <= spells
        assert {s for w in r.windows for s in w.defensive_cooldowns_active} <= spells


def test_names_for_ulatek_heroic(real_dataset):
    body = real_dataset.client.get(f"/v1/encounters/{ULATEK}/names?difficulty={HEROIC}").json()
    enc = load_encounter_view(ULATEK, HEROIC)
    assert enc is not None
    assert (body["encounter_name"], body["difficulty_name"]) == (enc.name, "heroic")
    assert list(body["phases"]) == [x.id for x in enc.phases]  # in the file's order
    assert body["phases"] == _phases(enc)
    shorts = [p["short_name"] for p in body["phases"].values()]
    assert shorts == ["Stage One", "Stage Two", "Intermission", "Stage Three"]
    assert body["mechanics"] == {x.id: x.name for x in enc.mechanics}
    assert body["damage_windows"] == {x.id: x.name for x in enc.damage_windows}
    assert body["mechanics"] and body["damage_windows"]


def test_a_difficulty_without_an_encounter_file_is_an_empty_lookup(real_dataset):
    assert load_encounter_view(NYMRISSA, MYTHIC) is None
    response = real_dataset.client.get(f"/v1/encounters/{NYMRISSA}/names?difficulty={MYTHIC}")
    assert response.status_code == 200
    assert response.json() == {
        "encounter_id": NYMRISSA,
        "difficulty": MYTHIC,
        "difficulty_name": "mythic",
        "encounter_name": None,
        "phases": {},
        "mechanics": {},
        "damage_windows": {},
    }


def test_unknown_encounter_and_difficulty_are_empty_lookups(client):
    for url in ("/v1/encounters/1/names?difficulty=4", f"/v1/encounters/{ULATEK}/names?difficulty=99"):
        body = client.get(url).json()
        assert (body["encounter_name"], body["phases"], body["mechanics"]) == (None, {}, {})


def test_names_need_a_difficulty(client):
    _assert_error(client.get(f"/v1/encounters/{ULATEK}/names"), 422, "bad_request")
    _assert_error(client.get(f"/v1/encounters/{ULATEK}/names?difficulty=heroic"), 422, "bad_request")


# ---------------------------------------------------------------------------
# Short phase names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("Stage Three: Ula'tek's Ascension", "Stage Three"),
        ("Intermission: The Shattering", "Intermission"),
        ("Stage One", "Stage One"),  # no ": "
        ("Phase 2:Burn", "Phase 2:Burn"),  # a colon without the space is not a separator
        ("A: B: C", "A"),  # the first ": " only
        (": Untitled", ": Untitled"),  # nothing before it: keep the whole name
    ],
)
def test_short_phase_name(name, short):
    assert short_phase_name(name) == short


def test_every_configured_phase_has_a_short_name():
    encounters = load_all_encounters()
    assert encounters
    for enc in encounters.values():
        for phase in enc.phases:
            short = short_phase_name(phase.name)
            assert short and short.strip() == short
            assert ": " not in short
            # The whole name, or the part before its first ": ".
            assert short == phase.name or phase.name.startswith(short + ": ")


# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------


def test_cooldown_names_come_from_every_cooldown_file(dataset, client):
    got = client.get("/v1/cooldowns").json()["cooldowns"]
    expected = {}
    for registry in _registries(dataset):
        for e in registry.cooldowns:
            expected.setdefault(
                str(e.spell_id), {"name": e.name, "category": registry.category, "verified": e.verified}
            )
    assert got == expected


def test_cooldowns_of_the_config_files_cover_every_category(tmp_path):
    client = TestClient(create_app(mode="local", data_dir=tmp_path))
    got = client.get("/v1/cooldowns").json()["cooldowns"]
    assert {c["category"] for c in got.values()} == {"defensive", "offensive", "utility"}


def test_cooldowns_are_cached_and_revalidated_with_an_etag(client):
    first = client.get("/v1/cooldowns")
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert first.headers["cache-control"] == "public, max-age=86400"
    assert client.get("/v1/cooldowns").headers["etag"] == etag  # stable across requests

    not_modified = client.get("/v1/cooldowns", headers={"If-None-Match": f'"stale", {etag}'})
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert not_modified.headers["etag"] == etag

    changed = client.get("/v1/cooldowns", headers={"If-None-Match": '"stale"'})
    assert changed.status_code == 200
    assert changed.json() == first.json()


def test_the_cooldown_etag_follows_the_files_content(synth_dataset):
    registries = tuple(_registries(synth_dataset))
    body, etag = cooldowns_response(registries)
    first = registries[0]
    edited = first.model_copy(update={"cooldowns": [first.cooldowns[0].model_copy(update={"name": "Renamed"})]})
    body2, etag2 = cooldowns_response((edited, *registries[1:]))
    assert etag2 != etag and body2 != body
    assert cooldowns_response(registries) == (body, etag)


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


def _meta(path) -> dict:
    return orjson.loads(path.read_bytes())


def _fought(meta: dict) -> list[dict]:
    """The metadata's player actors that are in some fight's ``friendlyPlayers``."""
    in_fights = {i for f in meta["fights"] or [] for i in f.get("friendlyPlayers") or []}
    return [a for a in meta["masterData"]["actors"] if a["type"] == "Player" and a["id"] in in_fights]


def test_players_of_every_stored_report(dataset, client):
    classes = set(get_args(PlayerClass))
    for code in dataset.store.reports():
        body = client.get(f"/v1/reports/{code}/players").json()
        actors = _fought(_meta(dataset.data_dir / "raw" / code / "report.json"))
        assert body["code"] == code
        assert [p["id"] for p in body["players"]] == sorted(a["id"] for a in actors)
        by_id = {a["id"]: a for a in actors}
        for p in body["players"]:
            actor = by_id[p["id"]]
            assert p["name"] == actor["name"]
            # Every class the recordings carry is a known one, except WCL's own "Unknown".
            assert actor["subType"] in classes | {"Unknown"}
            assert p["class_name"] == (actor["subType"] if actor["subType"] in classes else None)


FIXTURE_REPORTS = sorted(p.parent.name for p in WCL_FIXTURES.glob("*/report.json"))


@pytest.mark.parametrize("code", FIXTURE_REPORTS)
def test_players_of_every_fixture_report_are_those_in_a_fight(tmp_path, code):
    raw = (WCL_FIXTURES / code / "report.json").read_bytes()
    if raw.startswith(b"version https://git-lfs"):
        pytest.skip("real WCL fixtures not pulled from Git LFS")
    meta = orjson.loads(raw)
    (tmp_path / "raw" / code).mkdir(parents=True)
    (tmp_path / "raw" / code / "report.json").write_bytes(raw)
    players = TestClient(create_app(mode="local", data_dir=tmp_path)).get(f"/v1/reports/{code}/players").json()
    in_fights = {i for f in meta["fights"] or [] for i in f.get("friendlyPlayers") or []}
    listed = {a["id"] for a in meta["masterData"]["actors"] if a["type"] == "Player"}
    assert [p["id"] for p in players["players"]] == sorted(listed & in_fights)
    assert all(p["class_name"] is not None for p in players["players"])  # everyone who fought has a class


def test_bystanders_are_left_out(real_dataset):
    # H7z1fqDXjRTFnbtg lists 276 player actors; 253 of them, all of class "Unknown", never took part in a fight.
    code = "H7z1fqDXjRTFnbtg"
    meta = _meta(WCL_FIXTURES / code / "report.json")
    listed = [a for a in meta["masterData"]["actors"] if a["type"] == "Player"]
    fought = _fought(meta)
    assert (len(listed), len(fought)) == (276, 23)
    assert {a["subType"] for a in listed if a not in fought} == {"Unknown"}
    body = real_dataset.client.get(f"/v1/reports/{code}/players").json()
    assert [p["id"] for p in body["players"]] == sorted(a["id"] for a in fought)


def _write_meta(root, code: str, meta: dict) -> TestClient:
    (root / "raw" / code).mkdir(parents=True)
    (root / "raw" / code / "report.json").write_bytes(orjson.dumps(meta))
    return TestClient(create_app(mode="local", data_dir=root))


def test_a_player_in_no_fight_is_left_out(tmp_path):
    actors = [
        {"id": 1, "name": "Fighter", "type": "Player", "subType": "Mage"},
        {"id": 2, "name": "Bystander", "type": "Player", "subType": "Unknown"},
        {"id": 3, "name": "Pet", "type": "Pet", "subType": "Pet"},
    ]
    meta = {"masterData": {"actors": actors}, "fights": [{"id": 1, "friendlyPlayers": [1, 3]}, {"id": 2}]}
    body = _write_meta(tmp_path, "CODE", meta).get("/v1/reports/CODE/players").json()
    assert body["players"] == [{"id": 1, "name": "Fighter", "class_name": "Mage"}]


def test_a_report_without_fights_has_no_players(tmp_path):
    meta = {"masterData": {"actors": [{"id": 1, "name": "A", "type": "Player", "subType": "Mage"}]}, "fights": None}
    body = _write_meta(tmp_path, "CODE", meta).get("/v1/reports/CODE/players").json()
    assert body == {"code": "CODE", "players": []}


def test_players_cover_everyone_a_pull_names(dataset, client):
    for code in dataset.store.reports():
        players = {p["id"]: p for p in client.get(f"/v1/reports/{code}/players").json()["players"]}
        for pull in dataset.store.pulls(code):
            assert set(pull.friendly_player_ids) <= set(players)
            assert all(players[i]["class_name"] is not None for i in pull.friendly_player_ids)


def test_players_without_cached_metadata(tmp_path):
    client = TestClient(create_app(mode="local", data_dir=tmp_path))
    _assert_error(client.get("/v1/reports/NOPE/players"), 404, "report_not_found")
    _assert_error(client.get("/v1/reports/bad*code/players"), 404, "report_not_found")


@pytest.mark.parametrize("payload", [b"not json", b"[1, 2]"])
def test_players_with_unreadable_metadata(tmp_path, payload):
    (tmp_path / "raw" / "BROKEN").mkdir(parents=True)
    (tmp_path / "raw" / "BROKEN" / "report.json").write_bytes(payload)
    client = TestClient(create_app(mode="local", data_dir=tmp_path))
    _assert_error(client.get("/v1/reports/BROKEN/players"), 404, "report_not_found")
