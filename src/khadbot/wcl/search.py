"""
wcl/search.py

What guild and character search ask WCL besides report metadata: a guild's header (name, server, region, faction), a
guild's reports in a time range and a character's recent reports; the server list, which only
``scripts/data/build_server_list.py`` asks for, and ``load_server_list``, which reads the file it writes
(``config/registries/servers.yaml``); plus ``parse_guild_source``, which reads a warcraftlogs.com guild URL.

Every field here is checked against WCL's published schema (``docs/wcl/search.md``). Two are labelled
``[Database Points]``: ``reportData.reports`` (``GUILD_REPORTS_QUERY``) and ``Character.recentReports``
(``CHARACTER_QUERY``). The callers reserve database points for those two.

The queries are sent by the caller (the API, with its budget and token choice); this module only holds them and reads
their answers, so it needs no client of its own.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from khadbot.errors import ConfigurationError

# ---------------------------------------------------------------------------
# Guild URL or fields
# ---------------------------------------------------------------------------

_WCL_HOST = re.compile(r"^(www\.)?warcraftlogs\.com$")
# /guild/<region>/<server slug>/<name>, the format of a real guild URL
# (https://www.warcraftlogs.com/guild/us/skullcrusher/death%20and%20destruction). WCL's API schema doesn't describe
# site URLs, so nothing else (a numeric /guild/id/<n>, a sub-page) is accepted until one is seen.
_GUILD_PATH = re.compile(r"^/guild/([A-Za-z]{2,4})/([^/]+)/([^/]+)/?$")
_SLUG = re.compile(r"^\w[\w'-]{0,63}$")  # WCL's slugs keep accented letters: aggra-português
MAX_NAME_LENGTH = 64  # longer than any WoW guild name (24) or character name (12)


@dataclass(frozen=True)
class GuildSource:
    """A guild named by the user: WCL's region slug (``us``), server slug (``skullcrusher``) and the guild's name."""

    region: str
    server: str
    name: str


def normalize_region(region: str) -> str:
    """A region as WCL's region slug: ``US``, ``us`` and `` Us `` all read ``us``."""
    return region.strip().lower()


def clean_name(name: str) -> str:
    """
    A guild or character name as typed, with outer spaces dropped and inner runs of spaces made one.

    Raises:
        ValueError: The name is empty, too long, or holds a control character or a slash.
    """
    text = " ".join(name.split())
    if not text or len(text) > MAX_NAME_LENGTH or any(c in text for c in "/\\") or not text.isprintable():
        raise ValueError("Not a guild or character name.")
    return text


def guild_source(region: str, server: str, name: str) -> GuildSource:
    """
    A guild from its three fields, normalized: the region and server slug lower case, the name cleaned.

    Raises:
        ValueError: A field that can't be a region, server slug or name.
    """
    region = normalize_region(region)
    server = server.strip().lower()
    if not re.fullmatch(r"[a-z]{2,4}", region):
        raise ValueError("Not a region.")
    if not _SLUG.match(server):
        raise ValueError("Not a server slug.")
    return GuildSource(region, server, clean_name(name))


def parse_guild_source(url: str) -> GuildSource:
    """
    The guild a warcraftlogs.com guild URL names: ``https://www.warcraftlogs.com/guild/us/skullcrusher/death%20and%20destruction``
    reads region ``us``, server ``skullcrusher`` and name ``death and destruction`` (percent-decoded; WCL matches the
    name without regard to case, so it is kept as written).

    Raises:
        ValueError: ``url`` isn't a warcraftlogs.com guild URL of that form.
    """
    text = url.strip()
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError("Not a Warcraft Logs guild URL.") from None
    match = _GUILD_PATH.match(parts.path) if _WCL_HOST.match(host) else None
    if match is None:
        raise ValueError("Not a Warcraft Logs guild URL.")
    region, server, name = (unquote(g) for g in match.groups())
    return guild_source(region, server, name)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

REGIONS_OPERATION = "Regions"
SERVERS_OPERATION = "RegionServers"
GUILD_OPERATION = "GuildHeader"
GUILD_REPORTS_OPERATION = "GuildReports"
CHARACTER_OPERATION = "CharacterReports"
# Servers per page. WCL caps a query's complexity at 50,000; every region's servers at limit 5000 in one query come to
# 125,021, so the list is read one region at a time. A page's cost follows the limit asked, not the servers
# returned: limit 1000 cost 10 points for the US's 247 servers. 300 holds the US in one page for about 3.
SERVERS_PER_PAGE = 300
MAX_SERVER_PAGES = 10

REGIONS_QUERY = f"""
query {REGIONS_OPERATION} {{
  worldData {{
    regions {{
      id
      compactName
      name
      slug
    }}
  }}
}}
"""

SERVERS_QUERY = f"""
query {SERVERS_OPERATION}($id: Int!, $page: Int!) {{
  worldData {{
    region(id: $id) {{
      servers(limit: {SERVERS_PER_PAGE}, page: $page) {{
        has_more_pages
        data {{
          id
          name
          slug
        }}
      }}
    }}
  }}
}}
"""

GUILD_QUERY = f"""
query {GUILD_OPERATION}($name: String!, $serverSlug: String!, $serverRegion: String!) {{
  guildData {{
    guild(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {{
      id
      name
      faction {{
        id
        name
      }}
      server {{
        name
        slug
        region {{
          compactName
          slug
        }}
      }}
    }}
  }}
}}
"""


_LISTED_REPORT = """
        code
        title
        startTime
        endTime
        visibility
        zone {
          id
          name
        }"""

# [Database Points]: reportData.reports. By the guild's ID (from GUILD_QUERY), so a renamed guild still matches.
GUILD_REPORTS_QUERY = f"""
query {GUILD_REPORTS_OPERATION}($guildID: Int!, $startTime: Float!, $endTime: Float!, $limit: Int!) {{
  reportData {{
    reports(guildID: $guildID, startTime: $startTime, endTime: $endTime, limit: $limit, page: 1) {{
      has_more_pages
      data {{{_LISTED_REPORT}
      }}
    }}
  }}
}}
"""

# [Database Points]: Character.recentReports. The character itself is user-eligible.
CHARACTER_QUERY = f"""
query {CHARACTER_OPERATION}($name: String!, $serverSlug: String!, $serverRegion: String!, $limit: Int!) {{
  characterData {{
    character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {{
      id
      name
      server {{
        name
        slug
        region {{
          compactName
          slug
        }}
      }}
      recentReports(limit: $limit, page: 1) {{
        has_more_pages
        data {{{_LISTED_REPORT}
        }}
      }}
    }}
  }}
}}
"""


def guild_variables(source: GuildSource) -> dict[str, str]:
    return {"name": source.name, "serverSlug": source.server, "serverRegion": source.region}


def guild_reports_variables(guild_id: int, start_ms: float, end_ms: float, limit: int) -> dict[str, Any]:
    return {"guildID": guild_id, "startTime": float(start_ms), "endTime": float(end_ms), "limit": limit}


def character_variables(source: GuildSource, limit: int) -> dict[str, Any]:
    """The character query's variables; a character is named the way a guild is (region, server slug, name)."""
    return {"name": source.name, "serverSlug": source.server, "serverRegion": source.region, "limit": limit}


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Server:
    name: str
    slug: str


@dataclass(frozen=True)
class Region:
    """A region and its servers, sorted by name. ``complete`` is False when WCL had more servers than one page."""

    id: int
    slug: str  # "us", as guild and character lookups take it
    compact_name: str  # "US"
    name: str
    servers: tuple[Server, ...]
    complete: bool = True


@dataclass(frozen=True)
class Guild:
    id: int
    name: str
    faction: str | None  # WCL's localized faction name ("Horde"), None when it gave none
    server_name: str
    server_slug: str
    region: str  # compact name, "US"


@dataclass(frozen=True)
class ListedReport:
    """A report as a listing gives it: enough to order and filter, not its fights."""

    code: str
    title: str
    start_ms: float
    visibility: str | None  # "public", "private" or "unlisted"


@dataclass(frozen=True)
class Listing:
    """A page of reports, newest first; ``more`` when WCL had more than the page held."""

    reports: tuple[ListedReport, ...]
    more: bool


@dataclass(frozen=True)
class Character:
    id: int
    name: str
    server_name: str
    server_slug: str
    region: str  # compact name, "US"
    reports: Listing


def parse_listing(page: Any) -> Listing:
    """A ``ReportPagination`` object as a ``Listing``, newest first; entries without a code are skipped."""
    page = page if isinstance(page, dict) else {}
    reports = [
        ListedReport(
            code=str(r["code"]),
            title=str(r.get("title") or ""),
            start_ms=float(r.get("startTime") or 0),
            visibility=r.get("visibility") if isinstance(r.get("visibility"), str) else None,
        )
        for r in page.get("data") or []
        if isinstance(r, dict) and isinstance(r.get("code"), str) and r["code"]
    ]
    reports.sort(key=lambda r: (-r.start_ms, r.code))
    return Listing(tuple(reports), bool(page.get("has_more_pages")))


def parse_guild_reports(data: dict[str, Any]) -> Listing:
    """The reports of a ``GUILD_REPORTS_QUERY`` answer (its ``data``)."""
    return parse_listing((data.get("reportData") or {}).get("reports"))


def parse_character(data: dict[str, Any]) -> Character | None:
    """The character of a ``CHARACTER_QUERY`` answer (its ``data``), or None when WCL returned none."""
    raw = (data.get("characterData") or {}).get("character")
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), int):
        return None
    server = raw.get("server") or {}
    return Character(
        id=raw["id"],
        name=str(raw.get("name") or ""),
        server_name=str(server.get("name") or ""),
        server_slug=str(server.get("slug") or ""),
        region=str((server.get("region") or {}).get("compactName") or "").upper(),
        reports=parse_listing(raw.get("recentReports")),
    )


def parse_regions(data: dict[str, Any]) -> list[Region]:
    """The regions of a ``REGIONS_QUERY`` answer (its ``data``), in WCL's order, without servers yet."""
    regions: list[Region] = []
    for raw in ((data.get("worldData") or {}).get("regions")) or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), int) or not raw.get("slug"):
            continue
        regions.append(
            Region(
                id=raw["id"],
                slug=str(raw["slug"]).lower(),
                compact_name=str(raw.get("compactName") or raw["slug"]).upper(),
                name=str(raw.get("name") or ""),
                servers=(),
            )
        )
    return regions


def parse_server_page(data: dict[str, Any]) -> tuple[list[Server], bool]:
    """The servers of one ``SERVERS_QUERY`` page (its ``data``), and whether WCL has more pages."""
    page = (((data.get("worldData") or {}).get("region")) or {}).get("servers") or {}
    servers = [
        Server(str(s["name"]), str(s["slug"]))
        for s in page.get("data") or []
        if isinstance(s, dict) and s.get("name") and s.get("slug")
    ]
    return servers, bool(page.get("has_more_pages"))


def with_servers(region: Region, servers: list[Server], complete: bool) -> Region:
    """``region`` holding ``servers``, deduplicated and sorted by name."""
    ordered = sorted(set(servers), key=lambda s: (s.name.casefold(), s.slug))
    return Region(region.id, region.slug, region.compact_name, region.name, tuple(ordered), complete)


def servers_variables(region_id: int, page: int) -> dict[str, int]:
    return {"id": region_id, "page": page}


def parse_guild(data: dict[str, Any]) -> Guild | None:
    """The guild of a ``GUILD_QUERY`` answer (its ``data``), or None when WCL returned no guild."""
    raw = (data.get("guildData") or {}).get("guild")
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), int):
        return None
    server = raw.get("server") or {}
    faction = (raw.get("faction") or {}).get("name")
    return Guild(
        id=raw["id"],
        name=str(raw.get("name") or ""),
        faction=faction if isinstance(faction, str) and faction else None,
        server_name=str(server.get("name") or ""),
        server_slug=str(server.get("slug") or ""),
        region=str((server.get("region") or {}).get("compactName") or "").upper(),
    )


# ---------------------------------------------------------------------------
# The server list file
# ---------------------------------------------------------------------------

DEFAULT_SERVER_LIST_PATH = Path(__file__).resolve().parents[3] / "config" / "registries" / "servers.yaml"


def match_key(text: str) -> str:
    """
    ``text`` folded for matching: lower case, accents removed, only letters and digits ("Aggra (Português)" reads
    "aggraportugues").
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def load_server_list(path: Path | str = DEFAULT_SERVER_LIST_PATH) -> tuple[Region, ...]:
    """
    The regions and servers in ``config/registries/servers.yaml`` (``scripts/data/build_server_list.py`` writes it),
    in file order, each region's servers sorted by name.

    Raises:
        ConfigurationError: The file is missing or malformed: not YAML, no regions, a region or server without a
            slug or name, or a slug used twice.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"{path}: cannot read the server list: {exc}") from None
    entries = raw.get("regions") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ConfigurationError(f"{path}: needs a non-empty 'regions' list")
    regions: list[Region] = []
    for i, entry in enumerate(entries):
        where = f"{path}: regions[{i}]"
        if not isinstance(entry, dict):
            raise ConfigurationError(f"{where} is not a mapping")
        region_id, slug, compact = entry.get("id"), entry.get("slug"), entry.get("compact_name")
        if not isinstance(region_id, int) or not isinstance(slug, str) or not re.fullmatch(r"[a-z]{2,4}", slug):
            raise ConfigurationError(f"{where} needs an integer id and a lower-case region slug")
        if not isinstance(compact, str) or not compact:
            raise ConfigurationError(f"{where} needs a compact_name")
        servers: list[Server] = []
        seen: set[str] = set()
        for j, server in enumerate(entry.get("servers") or []):
            name = server.get("name") if isinstance(server, dict) else None
            server_slug = server.get("slug") if isinstance(server, dict) else None
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(server_slug, str)
                or not _SLUG.match(server_slug)
            ):
                raise ConfigurationError(f"{where}.servers[{j}] needs a name and a server slug")
            if server_slug in seen:
                raise ConfigurationError(f"{where}: server slug {server_slug!r} appears twice")
            seen.add(server_slug)
            servers.append(Server(name, server_slug))
        if not servers:
            raise ConfigurationError(f"{where} lists no servers")
        if any(r.slug == slug for r in regions):
            raise ConfigurationError(f"{where}: region {slug!r} appears twice")
        region = Region(region_id, slug, compact, str(entry.get("name") or ""), ())
        regions.append(with_servers(region, servers, complete=True))
    return tuple(regions)


def render_server_list(regions: list[Region] | tuple[Region, ...], source: str) -> str:
    """The server list file for ``regions``, with a header naming where it came from (``source``)."""
    lines = [
        "# Servers a guild or character search can name, by region (GET /v1/servers).",
        "#",
        "# Generated by scripts/data/build_server_list.py from WCL's worldData.regions and each region's servers;",
        f"# source: {source}. Don't edit by hand: rebuild it. The API loads it at start-up and fails on a malformed file.",
        "regions:",
    ]
    for r in regions:
        lines += [
            f"  - id: {r.id}",
            f"    slug: {r.slug}",
            f"    compact_name: {r.compact_name}",
            f"    name: {_yaml_scalar(r.name)}",
            "    servers:",
        ]
        lines += [f"      - {{name: {_yaml_scalar(s.name)}, slug: {_yaml_scalar(s.slug)}}}" for s in r.servers]
    return "\n".join(lines) + "\n"


def _yaml_scalar(text: str) -> str:
    """``text`` as a YAML flow scalar: double-quoted JSON-style, which YAML reads back exactly."""
    return json.dumps(text, ensure_ascii=False)


__all__ = [
    "DEFAULT_SERVER_LIST_PATH",
    "CHARACTER_OPERATION",
    "CHARACTER_QUERY",
    "GUILD_OPERATION",
    "GUILD_QUERY",
    "GUILD_REPORTS_OPERATION",
    "GUILD_REPORTS_QUERY",
    "MAX_SERVER_PAGES",
    "REGIONS_OPERATION",
    "REGIONS_QUERY",
    "SERVERS_OPERATION",
    "SERVERS_QUERY",
    "Character",
    "Guild",
    "GuildSource",
    "ListedReport",
    "Listing",
    "Region",
    "Server",
    "character_variables",
    "clean_name",
    "guild_source",
    "guild_reports_variables",
    "guild_variables",
    "load_server_list",
    "match_key",
    "normalize_region",
    "parse_character",
    "parse_guild",
    "parse_guild_reports",
    "parse_guild_source",
    "parse_listing",
    "parse_regions",
    "parse_server_page",
    "render_server_list",
    "servers_variables",
    "with_servers",
]
