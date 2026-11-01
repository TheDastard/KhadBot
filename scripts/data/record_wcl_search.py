"""
scripts/data/record_wcl_search.py

Record WCL's answers to the search queries (``wcl/search.py``) so WCL replay (``wcl/replay.py``) answers guild search,
character search and the server list offline: the server list, a guild's header and its report listing for the last
``--days`` days, a character with its recent reports, and the report metadata of every listed report the fixtures
don't already hold.

Each search answer is stored as WCL's whole GraphQL body (``data`` and any ``errors``), so a miss replays exactly as WCL
gave it, plus ``khadbotRecordedAt`` and ``khadbotPoints`` (what the query cost, measured with ``rateLimitData``).
Report metadata goes to ``<code>/report.json``, as ingest caches it.

Layout, under ``--out`` (default the replay fixtures, ``tests/fixtures/encounter/wcl``):

    _search/regions.json
    _search/servers/<region id>/page_NNN.json
    _search/guilds/<region>/<server slug>/<name, lower case, percent-encoded>.json
    _search/guild_reports/<guild id>.json
    _search/characters/<region>/<server slug>/<name, lower case, percent-encoded>.json
    <code>/report.json                      (a listed report the fixtures lacked)

**Private reports.** A listing on the app token shows public reports only, but a report's visibility is printed for
every listed code, and any that is not ``public`` is named again at the end: don't commit those until the user has
approved them.

This script SPENDS WCL POINTS, database points included: the two listings are ``[Database Points]`` fields. Per query
it spends the query plus two ``rateLimitData`` reads to measure it, and two reads once at the start. It prints the plan
and waits for a typed ``yes`` unless ``--yes`` is given. It uses the app's own token (client credentials) and never
prints a token. Needs WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET (from the environment or the nearest
``.env``).

    uv run python scripts/data/record_wcl_search.py --servers \
        --guild "us/skullcrusher/death and destruction" --character us/skullcrusher/dastard
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from khadbot.config import SearchConfig, get_config
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.ingest.pipeline import _report_meta
from khadbot.wcl.client import GRAPHQL_CLIENT_URL, WarcraftLogsAPIError, WarcraftLogsClient
from khadbot.wcl.replay import (
    SEARCH_DIR,
    search_character_path,
    search_guild_path,
    search_guild_reports_path,
)
from khadbot.wcl.search import (
    CHARACTER_QUERY,
    GUILD_QUERY,
    GUILD_REPORTS_QUERY,
    MAX_SERVER_PAGES,
    REGIONS_QUERY,
    SERVERS_QUERY,
    ListedReport,
    character_variables,
    guild_reports_variables,
    guild_source,
    guild_variables,
    parse_character,
    parse_guild,
    parse_guild_reports,
    parse_regions,
    parse_server_page,
    servers_variables,
)

DEFAULT_OUT = Path("tests/fixtures/encounter/wcl")


def search_regions_path() -> Path:
    """The region list's recording; ``scripts/data/build_server_list.py`` reads it (replay doesn't serve it)."""
    return Path(SEARCH_DIR, "regions.json")


def search_servers_path(region_id: int, page: int) -> Path:
    """One page of a region's servers; ``scripts/data/build_server_list.py`` reads it."""
    return Path(SEARCH_DIR, "servers", str(int(region_id)), f"page_{int(page):03d}.json")


class RecordingTransport(httpx.AsyncBaseTransport):
    """The network transport, keeping the last GraphQL response body so it can be stored whole."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.last_body: dict[str, Any] | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if str(request.url.copy_with(query=None)) != GRAPHQL_CLIENT_URL:
            return response
        raw = await response.aread()
        try:
            self.last_body = json.loads(raw)
        except ValueError:
            self.last_body = None
        # ``raw`` is already decoded: drop the headers that would make httpx decode it again.
        headers = [
            (k, v) for k, v in response.headers.items() if k.lower() not in ("content-encoding", "content-length")
        ]
        return httpx.Response(response.status_code, headers=headers, content=raw)

    async def aclose(self) -> None:
        await self._inner.aclose()


class Recorder:
    def __init__(self, client: WarcraftLogsClient, transport: RecordingTransport, out: Path) -> None:
        self.client, self.transport, self.out = client, transport, out
        self.read_cost = 0.0
        self.total = 0.0
        self.database_queries = 0
        self.listed: dict[str, ListedReport] = {}
        self.max_points = float("inf")

    async def spent(self) -> float:
        return float((await self.client.rate_limit())["pointsSpentThisHour"])

    async def start(self) -> None:
        first = await self.spent()
        self.read_cost = await self.spent() - first
        self.total += 2 * self.read_cost
        print(f"a rateLimitData read costs {self.read_cost:g} points")

    async def measured(self, run: Any) -> float:
        before = await self.spent()
        await run()
        points = round(await self.spent() - before - self.read_cost, 3)
        self.total += points + 2 * self.read_cost
        return points

    async def answer(self, relative: Path, gql: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Send one search query, store WCL's whole body at ``relative`` and return its ``data``."""
        self.check_spend(3 * self.read_cost + 5)
        self.transport.last_body = None
        answer: dict[str, Any] = {}

        async def run() -> None:
            try:
                await self.client.query(gql, variables)
            except WarcraftLogsAPIError as exc:  # a GraphQL error is an answer too: recorded as WCL gave it
                if self.transport.last_body is None or not exc.errors:
                    raise
            assert self.transport.last_body is not None
            answer.update(self.transport.last_body)  # before the measuring read replaces it

        points = await self.measured(run)
        body = answer
        path = self.out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = {"khadbotRecordedAt": datetime.now(UTC).isoformat(timespec="seconds"), "khadbotPoints": points}
        path.write_text(json.dumps({**body, **stamp}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  wrote {relative.as_posix()} ({points:g} points)")
        return body.get("data") or {}

    def check_spend(self, next_points: float) -> None:
        """Stop before a request that could take this run past ``max_points``."""
        if self.total + next_points > self.max_points:
            sys.exit(f"Stopping: {self.total:g} points spent, and the next request could pass {self.max_points:g}.")

    async def metadata(self, reports: list[ListedReport]) -> None:
        """Record the metadata of ``reports`` the fixtures lack, measured as one batch (two reads in all)."""
        cache = RawPageCache(self.out)
        missing = []
        for report in reports:
            if cache.report_meta(report.code) is not None:
                print(f"  {report.code} ({report.visibility}): metadata already recorded")
            else:
                missing.append(report)
        if not missing:
            return

        async def run() -> None:
            for report in missing:
                self.check_spend(self.read_cost + 4)  # a raid's metadata measured 2.01 points
                await _report_meta(self.client, cache, report.code, offline=False)
                print(f"  {report.code} ({report.visibility}): wrote {report.code}/report.json")

        points = await self.measured(run)
        print(f"  metadata of {len(missing)} reports: {points:g} points, {points / len(missing):.2f} each")


async def record(
    out: Path,
    servers: bool,
    guilds: list[str],
    characters: list[str],
    days: int,
    limit: int,
    max_points: float,
    only: set[str],
) -> None:
    wcl = get_config().warcraftlogs
    transport = RecordingTransport()
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret, transport=transport) as client:
        rec = Recorder(client, transport, out)
        rec.max_points = max_points
        await rec.start()
        if servers:
            recorded = out / search_regions_path()
            if recorded.is_file():  # the region list is recorded already: only the server pages are asked
                regions = parse_regions(json.loads(recorded.read_text(encoding="utf-8")).get("data") or {})
            else:
                regions = parse_regions(await rec.answer(search_regions_path(), REGIONS_QUERY, {}))
            for region in (r for r in regions if r.slug in only):
                page, more = 0, True
                while more and page < MAX_SERVER_PAGES:
                    page += 1
                    data = await rec.answer(
                        search_servers_path(region.id, page), SERVERS_QUERY, servers_variables(region.id, page)
                    )
                    found, more = parse_server_page(data)
                    print(f"  {region.slug} page {page}: {len(found)} servers")
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        for text in guilds:
            source = guild_source(*text.split("/", 2))
            data = await rec.answer(
                search_guild_path(source.region, source.server, source.name), GUILD_QUERY, guild_variables(source)
            )
            guild = parse_guild(data)
            if guild is None:
                print(f"  no guild {source.name!r} on {source.region}/{source.server}")
                continue
            variables = guild_reports_variables(guild.id, start.timestamp() * 1000, end.timestamp() * 1000, limit)
            listing = parse_guild_reports(
                await rec.answer(search_guild_reports_path(guild.id), GUILD_REPORTS_QUERY, variables)
            )
            rec.database_queries += 1
            rec.listed.update((r.code, r) for r in listing.reports)
            await rec.metadata(list(listing.reports))
        for text in characters:
            source = guild_source(*text.split("/", 2))
            relative = search_character_path(source.region, source.server, source.name)
            character = parse_character(await rec.answer(relative, CHARACTER_QUERY, character_variables(source, limit)))
            rec.database_queries += 1
            if character is None:
                print(f"  no character {source.name!r} on {source.region}/{source.server}")
                continue
            rec.listed.update((r.code, r) for r in character.reports.reports)
            await rec.metadata(list(character.reports.reports))
    print(f"spent about {rec.total:g} points in all, reads included; {rec.database_queries} database-point queries")
    hidden = sorted(code for code, r in rec.listed.items() if r.visibility != "public")
    if hidden:
        print("NOT PUBLIC, don't commit without the user's approval: " + ", ".join(hidden))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--servers", action="store_true", help="Record the server list")
    parser.add_argument("--guild", action="append", default=[], help="region/server-slug/name; may be repeated")
    parser.add_argument("--character", action="append", default=[], help="region/server-slug/name; may be repeated")
    parser.add_argument("--days", type=int, default=None, help="A guild listing's window (default the search's)")
    parser.add_argument("--limit", type=int, default=None, help="Reports per listing (default the search's cap)")
    parser.add_argument("--regions", default="us,eu", help="Regions whose servers to record (default us,eu)")
    parser.add_argument("--max-points", type=float, default=130.0, help="Stop before this run could spend more")
    parser.add_argument("--yes", action="store_true", help="Don't ask before spending points")
    args = parser.parse_args()
    if not (args.servers or args.guild or args.character):
        parser.error("nothing to record: give --servers, --guild and/or --character")
    load_dotenv()
    search = SearchConfig()
    days = args.days or search.guild_window_days
    limit = args.limit or search.max_reports
    listings = len(args.guild) + len(args.character)
    queries = int(args.servers) + 2 * len(args.guild) + len(args.character)
    print(
        f"Recording {queries} search queries ({listings} of them database-point listings) and the metadata of up to "
        f"{limit} reports per listing into {args.out}: about {2 + 3 * queries} points plus 1 to 3 per new report."
    )
    if not args.yes and input("Type yes to spend them: ").strip() != "yes":
        sys.exit("Nothing recorded.")
    only = {r.strip().lower() for r in args.regions.split(",") if r.strip()}
    asyncio.run(record(args.out, args.servers, args.guild, args.character, days, limit, args.max_points, only))


if __name__ == "__main__":
    main()
