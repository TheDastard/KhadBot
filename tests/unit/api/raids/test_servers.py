"""
tests/unit/api/raids/test_servers.py

``GET /v1/servers`` on the hosted app: the list comes from ``config/registries/servers.yaml`` with no WCL request,
whole and unfiltered, cached by the browser and revalidated with an ETag of the file; a malformed file stops the app;
a search naming a server not in it is 404 without a WCL request; and the file is what the recorded server pages give.
"""

from __future__ import annotations

import pytest
from helpers.api.search import Env, guild
from helpers.wcl.replay import WCL_FIXTURES, guard_network

from khadbot.wcl.search import DEFAULT_SERVER_LIST_PATH, load_server_list


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = guard_network(monkeypatch)
    yield
    assert attempts == []


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


def test_servers_come_from_the_file_with_no_wcl_request(env):
    response = env.browser().get("/v1/servers")
    assert response.status_code == 200
    assert env.transport.operations == [] and env.asks == []
    body = response.json()
    assert [(r["slug"], r["name"]) for r in body["regions"]] == [("us", "US"), ("eu", "EU")]
    entry = next(s for s in body["regions"][1]["servers"] if s["slug"] == "aggra-português")
    assert entry == {"name": "Aggra (Português)", "slug": "aggra-português", "region": "eu", "match": "aggraportugues"}


def test_the_response_holds_every_server_in_the_file_and_takes_no_filter(env):
    filed = {(r.slug, s.slug) for r in load_server_list() for s in r.servers}
    for params in ({}, {"q": "storm"}, {"search": "storm", "region": "us"}):
        body = env.browser().get("/v1/servers", params=params).json()
        served = {(r["slug"], s["slug"]) for r in body["regions"] for s in r["servers"]}
        assert served == filed
    assert len(filed) > 500


def test_the_list_is_cached_by_the_browser_and_revalidated_with_its_etag(env):
    client = env.browser()
    first = client.get("/v1/servers")
    etag = first.headers["ETag"]
    assert "max-age=86400" in first.headers["Cache-Control"]
    again = client.get("/v1/servers", headers={"If-None-Match": etag})
    assert (again.status_code, again.content) == (304, b"")
    assert client.get("/v1/servers", headers={"If-None-Match": '"other"'}).status_code == 200


def test_the_etag_follows_the_file(tmp_path):
    from khadbot.api.raids.servers import server_list_response

    copy = tmp_path / "servers.yaml"
    copy.write_bytes(DEFAULT_SERVER_LIST_PATH.read_bytes())
    _, _, before = server_list_response(copy)
    copy.write_text(copy.read_text(encoding="utf-8").replace('"Area 52"', '"Area Fifty-Two"'), encoding="utf-8")
    _, _, after = server_list_response(copy)
    assert before != after


def test_a_malformed_server_list_fails_at_start_up(tmp_path, monkeypatch):
    import khadbot.api.raids.servers as servers_module
    from khadbot.errors import ConfigurationError

    bad = tmp_path / "servers.yaml"
    bad.write_text("regions: []\n", encoding="utf-8")
    monkeypatch.setattr(servers_module, "DEFAULT_SERVER_LIST_PATH", bad)
    monkeypatch.setattr(servers_module.server_list_response, "__defaults__", (bad,))
    with pytest.raises(ConfigurationError):
        Env(tmp_path)


@pytest.mark.parametrize(
    "params",
    [{**{"region": "us", "server": "nowhere", "name": "X"}}, {"region": "kr", "server": "azshara", "name": "X"}],
)
def test_an_unknown_server_is_404_without_a_wcl_request(env, params):
    client = env.browser()
    for response in (guild(client, **params), client.get("/v1/search/character", params=params)):
        assert (response.status_code, response.json()["code"]) == (404, "server_unknown")
    assert env.transport.operations == [] and env.asks == []


def test_the_server_list_file_is_the_recordings():
    from helpers.scripts import load_script

    script = load_script("data", "build_server_list")
    recording = WCL_FIXTURES / "_search"
    regions = script.from_recording(recording, ("us", "eu"))
    assert tuple(regions) == load_server_list()
