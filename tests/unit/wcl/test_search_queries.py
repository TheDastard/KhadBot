"""
tests/unit/wcl/test_search_queries.py

``wcl/search.py``: guild URLs and fields, and reading the search queries' answers; and WCL replay answering the search
queries from ``_search/`` recordings. The answers here are synthetic: built to WCL's documented schema, not recorded.
"""

from __future__ import annotations

import json

import httpx
import pytest
from helpers.wcl.replay import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

from khadbot.errors import ConfigurationError
from khadbot.wcl.client import WarcraftLogsClient
from khadbot.wcl.replay import (
    ReplayTransport,
    WclReplayRefusedError,
    search_character_path,
    search_guild_path,
    search_guild_reports_path,
)
from khadbot.wcl.search import (
    CHARACTER_QUERY,
    GUILD_QUERY,
    GUILD_REPORTS_QUERY,
    REGIONS_QUERY,
    SERVERS_QUERY,
    GuildSource,
    character_variables,
    clean_name,
    guild_reports_variables,
    guild_source,
    guild_variables,
    load_server_list,
    match_key,
    parse_character,
    parse_guild,
    parse_guild_reports,
    parse_guild_source,
    parse_regions,
    parse_server_page,
    render_server_list,
    servers_variables,
    with_servers,
)

# ---------------------------------------------------------------------------
# Guild URLs and fields
# ---------------------------------------------------------------------------


def test_the_users_guild_url_reads_region_server_and_a_decoded_name():
    url = "https://www.warcraftlogs.com/guild/us/skullcrusher/death%20and%20destruction"
    assert parse_guild_source(url) == GuildSource("us", "skullcrusher", "death and destruction")


@pytest.mark.parametrize(
    "url",
    [
        "warcraftlogs.com/guild/US/Skullcrusher/Death%20and%20Destruction/",
        "  https://warcraftlogs.com/guild/us/skullcrusher/Death and Destruction  ",
    ],
)
def test_guild_urls_without_scheme_case_or_encoding_still_read(url):
    source = parse_guild_source(url)
    assert (source.region, source.server, source.name.casefold()) == ("us", "skullcrusher", "death and destruction")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.warcraftlogs.com/reports/H7z1fqDXjRTFnbtg",
        "https://www.warcraftlogs.com/guild/id/12345",
        "https://www.warcraftlogs.com/guild/us/skullcrusher",
        "https://www.warcraftlogs.com/guild/us/skullcrusher/name/extra",
        "https://evil.example.com/guild/us/skullcrusher/name",
        "https://classic.warcraftlogs.com/guild/us/skullcrusher/name",
        "not a url",
    ],
)
def test_anything_else_is_not_a_guild_url(url):
    with pytest.raises(ValueError):
        parse_guild_source(url)


def test_fields_are_normalized():
    assert guild_source(" US ", "Skullcrusher", "  Death   and Destruction ") == GuildSource(
        "us", "skullcrusher", "Death and Destruction"
    )


@pytest.mark.parametrize(
    ("region", "server", "name"),
    [("usa-x", "skullcrusher", "x"), ("us", "skull crusher", "x"), ("us", "skullcrusher", "  "), ("us", "a", "a/b")],
)
def test_bad_fields_are_refused(region, server, name):
    with pytest.raises(ValueError):
        guild_source(region, server, name)


def test_a_name_has_a_length_limit():
    with pytest.raises(ValueError):
        clean_name("x" * 65)


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

REGIONS = {
    "worldData": {
        "regions": [
            {"id": 1, "compactName": "US", "name": "United States", "slug": "us"},
            {"id": 2, "compactName": "eu", "name": "Europe", "slug": "EU"},
            {"name": "no id"},
        ]
    }
}
PAGE = {
    "worldData": {
        "region": {
            "servers": {
                "has_more_pages": True,
                "data": [
                    {"id": 12, "name": "Stormrage", "slug": "stormrage"},
                    {"id": 11, "name": "Area 52", "slug": "area-52"},
                    {"id": 13, "name": ""},
                ],
            }
        }
    }
}


def test_regions_keep_wcl_order_and_normalize_their_names():
    regions = parse_regions(REGIONS)
    assert [(r.id, r.slug, r.compact_name, r.servers) for r in regions] == [(1, "us", "US", ()), (2, "eu", "EU", ())]


def test_a_server_page_and_a_region_with_its_servers():
    servers, more = parse_server_page(PAGE)
    assert [s.slug for s in servers] == ["stormrage", "area-52"] and more is True
    region = with_servers(parse_regions(REGIONS)[0], servers + servers[:1], complete=False)
    assert [s.name for s in region.servers] == ["Area 52", "Stormrage"]
    assert region.complete is False
    assert parse_server_page({}) == ([], False)
    assert servers_variables(1, 2) == {"id": 1, "page": 2}


GUILD = {
    "guildData": {
        "guild": {
            "id": 777,
            "name": "Death and Destruction",
            "faction": {"id": 1, "name": "Horde"},
            "server": {"name": "Skullcrusher", "slug": "skullcrusher", "region": {"compactName": "US", "slug": "us"}},
        }
    }
}


def test_a_guild_is_read_with_its_faction_and_a_missing_one_is_none():
    guild = parse_guild(GUILD)
    assert guild is not None
    assert (guild.id, guild.name, guild.faction, guild.server_slug, guild.region) == (
        777,
        "Death and Destruction",
        "Horde",
        "skullcrusher",
        "US",
    )
    assert parse_guild({"guildData": {"guild": None}}) is None
    assert parse_guild({}) is None


def test_a_listing_is_newest_first_and_says_when_there_is_more():
    listing = parse_guild_reports(
        {
            "reportData": {
                "reports": {
                    "has_more_pages": True,
                    "data": [
                        {"code": "A", "title": "old", "startTime": 1, "visibility": "public"},
                        {"code": "B", "title": "new", "startTime": 5, "visibility": "private"},
                        {"title": "no code"},
                    ],
                }
            }
        }
    )
    assert [r.code for r in listing.reports] == ["B", "A"]
    assert listing.reports[0].visibility == "private"
    assert listing.more is True
    assert parse_guild_reports({}).reports == ()


def test_a_character_carries_its_recent_reports():
    character = parse_character(
        {
            "characterData": {
                "character": {
                    "id": 5,
                    "name": "Dastard",
                    "server": {"name": "Skullcrusher", "slug": "skullcrusher", "region": {"compactName": "US"}},
                    "recentReports": {"has_more_pages": False, "data": [{"code": "C", "startTime": 3}]},
                }
            }
        }
    )
    assert character is not None
    assert (character.name, character.region, [r.code for r in character.reports.reports]) == ("Dastard", "US", ["C"])
    assert parse_character({"characterData": {"character": None}}) is None


def test_queries_name_their_operations_and_take_their_variables():
    assert REGIONS_QUERY.lstrip().startswith("query Regions")
    assert SERVERS_QUERY.lstrip().startswith("query RegionServers(")
    assert GUILD_QUERY.lstrip().startswith("query GuildHeader(")
    assert GUILD_REPORTS_QUERY.lstrip().startswith("query GuildReports(")
    assert CHARACTER_QUERY.lstrip().startswith("query CharacterReports(")
    source = GuildSource("us", "skullcrusher", "Dastard")
    assert guild_variables(source) == {"name": "Dastard", "serverSlug": "skullcrusher", "serverRegion": "us"}
    assert character_variables(source, 15)["limit"] == 15
    assert guild_reports_variables(777, 1, 2, 15) == {"guildID": 777, "startTime": 1.0, "endTime": 2.0, "limit": 15}


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _write(root, relative, body):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**body, "khadbotPoints": 1}), encoding="utf-8")


@pytest.fixture
def replay_root(tmp_path):
    _write(tmp_path, search_guild_path("us", "skullcrusher", "Death and Destruction"), {"data": GUILD})
    _write(tmp_path, search_guild_reports_path(777), {"data": {"reportData": {"reports": {"data": []}}}})
    _write(
        tmp_path,
        search_character_path("us", "skullcrusher", "dastard"),
        {"data": {"characterData": {"character": None}}, "errors": [{"message": "synthetic miss"}]},
    )
    return tmp_path


async def _ask(root, gql, variables=None):
    async with WarcraftLogsClient(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, transport=ReplayTransport(root)) as client:
        return await client.query(gql, variables)


async def test_replay_answers_the_recorded_searches_without_khadbot_keys(replay_root):
    source = GuildSource("us", "skullcrusher", "DEATH AND DESTRUCTION")  # any case finds the recording
    data = await _ask(replay_root, GUILD_QUERY, guild_variables(source))
    assert data == GUILD
    listing = await _ask(replay_root, GUILD_REPORTS_QUERY, guild_reports_variables(777, 0, 1, 15))
    assert parse_guild_reports(listing).reports == ()


async def test_replay_gives_a_recorded_error_as_wcl_gave_it(replay_root):
    from khadbot.wcl.client import WarcraftLogsAPIError

    with pytest.raises(WarcraftLogsAPIError) as caught:
        await _ask(replay_root, CHARACTER_QUERY, character_variables(GuildSource("us", "skullcrusher", "Dastard"), 5))
    assert caught.value.errors == [{"message": "synthetic miss"}]


async def test_replay_refuses_what_nobody_recorded(replay_root):
    with pytest.raises(WclReplayRefusedError, match="no recording of the guild"):
        await _ask(replay_root, GUILD_QUERY, guild_variables(GuildSource("eu", "draenor", "Nobody")))
    with pytest.raises(WclReplayRefusedError, match="guild 778's reports"):
        await _ask(replay_root, GUILD_REPORTS_QUERY, guild_reports_variables(778, 0, 1, 15))


def test_recordings_live_beside_the_reports_but_are_never_a_report():
    assert search_guild_path("US", "Skullcrusher", "Death and Destruction").as_posix() == (
        "_search/guilds/us/skullcrusher/death%20and%20destruction.json"
    )
    assert search_guild_reports_path(777).as_posix() == "_search/guild_reports/777.json"


def test_httpx_is_the_transport_layer():
    assert issubclass(ReplayTransport, httpx.AsyncBaseTransport)


# ---------------------------------------------------------------------------
# The server list file
# ---------------------------------------------------------------------------


def test_the_committed_server_list_loads_us_then_eu():
    regions = load_server_list()
    assert [r.slug for r in regions] == ["us", "eu"]
    assert any(s.slug == "skullcrusher" for s in regions[0].servers)
    assert any(s.slug == "aggra-português" for s in regions[1].servers)


def test_a_rendered_list_reads_back_exactly(tmp_path):
    regions = load_server_list()
    path = tmp_path / "servers.yaml"
    path.write_text(render_server_list(regions, "a test"), encoding="utf-8")
    assert load_server_list(path) == regions


@pytest.mark.parametrize(
    "text",
    [
        "not: [yaml",
        "regions: []",
        "regions:\n  - {id: 1, slug: US, compact_name: US, servers: [{name: A, slug: a}]}",
        "regions:\n  - {id: 1, slug: us, compact_name: US, servers: []}",
        "regions:\n  - {id: 1, slug: us, compact_name: US, servers: [{name: A, slug: a}, {name: B, slug: a}]}",
        "regions:\n  - {id: 1, slug: us, compact_name: US, servers: [{name: A, slug: 'a b'}]}",
    ],
)
def test_a_malformed_list_fails_at_load(tmp_path, text):
    path = tmp_path / "servers.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_server_list(path)


def test_a_missing_list_fails_at_load(tmp_path):
    with pytest.raises(ConfigurationError):
        load_server_list(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    ("name", "key"), [("Aggra (Português)", "aggraportugues"), ("Area 52", "area52"), ("Ahn'Qiraj", "ahnqiraj")]
)
def test_match_keys_drop_case_accents_and_punctuation(name, key):
    assert match_key(name) == key
