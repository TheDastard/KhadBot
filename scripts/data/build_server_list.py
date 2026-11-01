"""
scripts/data/build_server_list.py

Rebuild ``config/registries/servers.yaml``, the US and EU server list ``GET /v1/servers`` serves and the searches check
names against, from WCL's ``worldData.regions`` and each region's ``servers``.

By default it reads a recording (``scripts/data/record_wcl_search.py --servers`` writes one under
``tests/fixtures/encounter/wcl/_search/``) and spends nothing. ``--live`` asks WCL instead: one request for the regions
and one per page of servers (about 3 points a page of 300, so about 7 for US and EU), on the app's token, after a typed
``yes`` unless ``--yes`` is given. It never prints a token. Needs WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET
for ``--live`` (from the environment or the nearest ``.env``).

    uv run python scripts/data/build_server_list.py                 # from the committed recording
    uv run python scripts/data/build_server_list.py --live          # from WCL, asks first
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from khadbot.wcl.search import (
    DEFAULT_SERVER_LIST_PATH,
    MAX_SERVER_PAGES,
    REGIONS_QUERY,
    SERVERS_QUERY,
    Region,
    Server,
    load_server_list,
    parse_regions,
    parse_server_page,
    render_server_list,
    servers_variables,
    with_servers,
)

DEFAULT_RECORDING = Path("tests/fixtures/encounter/wcl/_search")
DEFAULT_REGIONS = ("us", "eu")


def _data(path: Path) -> dict[str, Any]:
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("errors"):
        sys.exit(f"{path} holds a WCL error, not an answer: {body['errors'][0].get('message')}")
    return body.get("data") or {}


def from_recording(recording: Path, wanted: tuple[str, ...]) -> list[Region]:
    """The ``wanted`` regions with their servers, from ``<recording>/regions.json`` and ``servers/<id>/page_NNN.json``."""
    regions = {r.slug: r for r in parse_regions(_data(recording / "regions.json"))}
    out = []
    for slug in wanted:
        if slug not in regions:
            sys.exit(f"The recording has no region {slug!r}.")
        region = regions[slug]
        pages = sorted((recording / "servers" / str(region.id)).glob("page_*.json"))
        if not pages:
            sys.exit(f"The recording has no servers for {slug} (region {region.id}).")
        servers: list[Server] = []
        more = False
        for page in pages:
            found, more = parse_server_page(_data(page))
            servers.extend(found)
        if more:
            sys.exit(f"The recording of {slug}'s servers stops before WCL's last page.")
        out.append(with_servers(region, servers, complete=True))
    return out


async def from_wcl(wanted: tuple[str, ...]) -> list[Region]:
    """The ``wanted`` regions with their servers, asked of WCL on the app's token."""
    from khadbot.config import get_config
    from khadbot.wcl.client import WarcraftLogsClient

    wcl = get_config().warcraftlogs
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret) as client:
        regions = {r.slug: r for r in parse_regions(await client.query(REGIONS_QUERY))}
        out = []
        for slug in wanted:
            if slug not in regions:
                sys.exit(f"WCL lists no region {slug!r}.")
            region = regions[slug]
            servers: list[Server] = []
            page, more = 0, True
            while more and page < MAX_SERVER_PAGES:
                page += 1
                found, more = parse_server_page(await client.query(SERVERS_QUERY, servers_variables(region.id, page)))
                servers.extend(found)
            if more:
                sys.exit(f"WCL has more than {MAX_SERVER_PAGES} pages of servers in {slug}.")
            out.append(with_servers(region, servers, complete=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recording", type=Path, default=DEFAULT_RECORDING, help="A search recording's _search dir")
    parser.add_argument("--live", action="store_true", help="Ask WCL instead of reading a recording (spends points)")
    parser.add_argument("--regions", default=",".join(DEFAULT_REGIONS), help="Region slugs, in order (default us,eu)")
    parser.add_argument("--out", type=Path, default=DEFAULT_SERVER_LIST_PATH)
    parser.add_argument("--yes", action="store_true", help="With --live, don't ask before spending points")
    args = parser.parse_args()
    wanted = tuple(r.strip().lower() for r in args.regions.split(",") if r.strip())
    if args.live:
        from dotenv import load_dotenv

        load_dotenv()
        print(f"Asking WCL for the regions and every page of servers in {', '.join(wanted)}: about 7 points.")
        if not args.yes and input("Type yes to spend them: ").strip() != "yes":
            sys.exit("Nothing asked.")
        regions = asyncio.run(from_wcl(wanted))
        source = f"WCL, {datetime.now(UTC).date().isoformat()}"
    else:
        regions = from_recording(args.recording, wanted)
        recorded = json.loads((args.recording / "regions.json").read_text(encoding="utf-8")).get("khadbotRecordedAt")
        source = f"the search recording of {str(recorded)[:10]}"
    args.out.write_text(render_server_list(regions, source), encoding="utf-8")
    load_server_list(args.out)  # what the API will load: fail here, not at start-up
    print(f"wrote {args.out}: " + ", ".join(f"{r.slug} {len(r.servers)} servers" for r in regions))


if __name__ == "__main__":
    main()
