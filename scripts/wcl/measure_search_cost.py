"""
scripts/wcl/measure_search_cost.py

Measure what guild search's WCL requests cost: a guild header (``GuildHeader``), one guild report listing
(``GuildReports``, a ``[Database Points]`` field) and one report metadata query (``GetReportMeta``, the query the report
lookup and every listed night send).

For each, it reads ``rateLimitData`` right before and right after (application and database counters), and the query
itself also asks for ``rateLimitData { currentRequest }``, WCL's own breakdown of that request (total, application,
user and database points). Two reads back to back first give a read's own cost, which is taken out of the
before-and-after difference.

This script SPENDS WCL POINTS (about 15, a few of them database points). It prints the plan and waits for a typed
``yes`` unless ``--yes`` is given. It uses the app's own token (client credentials) and never prints a token. Needs
WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET (from the environment or the nearest ``.env``).

    uv run python scripts/wcl/measure_search_cost.py --guild "us/skullcrusher/death and destruction" --code <public report>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from dotenv import load_dotenv

COUNTERS_QUERY = """
query RateLimitCounters {
  rateLimitData {
    limitPerHour
    pointsSpentThisHour
    pointsResetIn
    databaseLimitPerHour
    databasePointsSpentThisHour
  }
}
"""

_CURRENT = "\n  rateLimitData {\n    currentRequest\n  }\n}\n"


def with_current_request(gql: str) -> str:
    """``gql`` with ``rateLimitData { currentRequest }`` added beside its top-level field."""
    body = gql.rstrip()
    assert body.endswith("}")
    return body[:-1].rstrip() + _CURRENT


async def counters(client: Any) -> dict[str, float]:
    data = await client.query(COUNTERS_QUERY)
    return {k: float(v) for k, v in (data.get("rateLimitData") or {}).items()}


async def measure(client: Any, label: str, gql: str, variables: dict[str, Any], read: dict[str, float]) -> dict:
    before = await counters(client)
    data = await client.query(with_current_request(gql), variables)
    after = await counters(client)
    result = {
        "query": label,
        "points": round(after["pointsSpentThisHour"] - before["pointsSpentThisHour"] - read["points"], 3),
        "database_points": round(
            after["databasePointsSpentThisHour"] - before["databasePointsSpentThisHour"] - read["database_points"], 3
        ),
        "current_request": (data.get("rateLimitData") or {}).get("currentRequest"),
    }
    print(json.dumps(result))
    return {"data": data, **result}


async def run(guild_text: str, code: str, days: int, limit: int) -> None:
    from khadbot.config import get_config
    from khadbot.encounter.ingest.wcl_queries import GET_REPORT_META
    from khadbot.wcl.client import WarcraftLogsClient
    from khadbot.wcl.search import (
        GUILD_QUERY,
        GUILD_REPORTS_QUERY,
        guild_reports_variables,
        guild_source,
        guild_variables,
        parse_guild,
        parse_guild_reports,
    )

    wcl = get_config().warcraftlogs
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret) as client:
        first = await counters(client)
        second = await counters(client)
        read = {
            "points": second["pointsSpentThisHour"] - first["pointsSpentThisHour"],
            "database_points": second["databasePointsSpentThisHour"] - first["databasePointsSpentThisHour"],
        }
        shown = {k: v for k, v in second.items()}
        print(json.dumps({"counters": shown, "read_cost": read}))
        source = guild_source(*guild_text.split("/", 2))
        header = await measure(client, "GuildHeader", GUILD_QUERY, guild_variables(source), read)
        guild = parse_guild(header["data"])
        if guild is None:
            sys.exit(f"No guild {source.name!r} on {source.region}/{source.server}.")
        end = datetime.now(UTC)
        variables = guild_reports_variables(
            guild.id, (end - timedelta(days=days)).timestamp() * 1000, end.timestamp() * 1000, limit
        )
        listing = await measure(client, "GuildReports", GUILD_REPORTS_QUERY, variables, read)
        print(json.dumps({"listed_reports": len(parse_guild_reports(listing["data"]).reports)}))
        await measure(client, "GetReportMeta", GET_REPORT_META, {"code": code}, read)
        print(json.dumps({"counters": await counters(client)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--guild", required=True, help="region/server-slug/name")
    parser.add_argument("--code", required=True, help="A public report for the metadata query")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--yes", action="store_true", help="Don't ask before spending points")
    args = parser.parse_args()
    load_dotenv()
    print("Measuring 3 queries with 8 rateLimitData reads: about 15 points, a few database points.")
    if not args.yes and input("Type yes to spend them: ").strip() != "yes":
        sys.exit("Nothing measured.")
    asyncio.run(run(args.guild, args.code, args.days, args.limit))


if __name__ == "__main__":
    main()
