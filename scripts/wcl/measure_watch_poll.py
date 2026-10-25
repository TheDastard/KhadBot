"""
scripts/wcl/measure_watch_poll.py

Measure what one poll of a live report watch costs in WCL points: the report metadata query that
``jobs/watch.py`` sends every few minutes (``GET_REPORT_META``, the same query ingest uses).

Per poll it reads ``rateLimitData`` before and after the metadata query. A read costs points itself, so first it reads
twice back to back and takes the difference as the read's own cost, then subtracts that from each poll's change.

This script SPENDS WCL POINTS (about 2 for the baseline reads plus, per poll, the query and 2 reads: roughly 4 to 6 a
poll if the query costs 1 to 3). It prints an estimate and waits for you to type ``yes``. It never prints a token.
It uses the app's own token (client credentials), so the report must be public; a signed-in user's token spends the
same application points on WCL's current limits (``docs/wcl-api.md``, "Budget probe results"). Needs
WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET (from the environment or the nearest ``.env``).

    uv run python scripts/wcl/measure_watch_poll.py --code <report code> [--polls 3]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from dotenv import load_dotenv


class Client(Protocol):
    async def query(self, gql: str, variables: dict[str, Any] | None = None) -> dict[str, Any]: ...

    async def rate_limit(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PollMeasurement:
    read_cost: float  # what one rateLimitData read costs
    per_poll: list[float]  # points each metadata query cost, the reads taken out
    fights: int  # fights the report listed, for context

    @property
    def mean(self) -> float:
        return sum(self.per_poll) / len(self.per_poll) if self.per_poll else 0.0


async def _spent(client: Client) -> float:
    return float((await client.rate_limit())["pointsSpentThisHour"])


async def measure(client: Client, code: str, polls: int) -> PollMeasurement:
    """Read the metadata ``polls`` times and report the points each query cost."""
    from khadbot.encounter.ingest.wcl_queries import GET_REPORT_META

    first = await _spent(client)
    second = await _spent(client)
    read_cost = second - first
    costs: list[float] = []
    fights = 0
    before = second
    for _ in range(polls):
        data = await client.query(GET_REPORT_META, {"code": code})
        report = (data.get("reportData") or {}).get("report") or {}
        fights = len(report.get("fights") or [])
        after = await _spent(client)
        costs.append(after - before - read_cost)
        before = after
    return PollMeasurement(read_cost, costs, fights)


def estimate(polls: int) -> str:
    return f"about {2 + polls * 3} to {2 + polls * 5} points ({polls} polls, each a query and a rate-limit read)"


async def main(code: str, polls: int, assume_yes: bool) -> int:
    from khadbot.config import WarcraftLogsConfig
    from khadbot.wcl.client import WarcraftLogsClient

    print(f"This will spend {estimate(polls)} of the app's WCL points on report {code}.")
    if not assume_yes and input("Type yes to continue: ").strip() != "yes":
        print("Not run.")
        return 1
    wcl = WarcraftLogsConfig()
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret) as client:
        result = await measure(client, code, polls)
    print(f"One rateLimitData read costs {result.read_cost:g} points.")
    print(f"The report lists {result.fights} boss fights.")
    for i, cost in enumerate(result.per_poll, 1):
        print(f"Poll {i}: {cost:g} points")
    print(f"Mean per poll: {result.mean:g} points. Record it in docs/wcl-api.md with the report code and date.")
    return 0


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="Measure the points one live-watch poll costs.")
    parser.add_argument("--code", required=True, help="a public report code")
    parser.add_argument("--polls", type=int, default=3)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation (the estimate is still printed)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.code, args.polls, args.yes)))
