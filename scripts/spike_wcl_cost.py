"""
scripts/spike_wcl_cost.py

Measure what ingesting a report costs. Prints a markdown table for the
write-up: per pull and data type, WCL points spent, wall time, pages, events; then json vs orjson
parse time and peak memory while normalizing.

Needs WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET. Uses a throwaway cache directory so
every request really hits the network.

    uv run python scripts/spike_wcl_cost.py <code> [--fights 3,4]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
import tracemalloc
from pathlib import Path

import orjson
from dotenv import load_dotenv

from khadbot.config import get_config
from khadbot.encounter.ingest.fetch import RawPageCache, fetch_pull, specs_for
from khadbot.encounter.ingest.normalize import normalize_pull, parse_report_meta
from khadbot.encounter.ingest.pipeline import _report_meta
from khadbot.tools.warcraftlogs._client import WarcraftLogsClient


async def _points(client: WarcraftLogsClient) -> float:
    return float((await client.rate_limit())["pointsSpentThisHour"])


async def spike(code: str, fights: list[int] | None) -> None:
    cache = RawPageCache(Path(tempfile.mkdtemp(prefix="khadbot-spike-")))
    wcl = get_config().warcraftlogs
    rows: list[str] = []
    parse_rows: list[str] = []
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret) as client:
        meta = await _report_meta(client, cache, code, offline=False)
        pulls, actors = parse_report_meta(code, meta)
        for pull in pulls:
            if fights is not None and pull.fight_id not in fights:
                continue
            # One spec at a time so points and wall time are attributable.
            for spec in specs_for():
                before, t0 = await _points(client), time.perf_counter()
                await fetch_pull(client, cache, code, pull, [spec])
                wall = time.perf_counter() - t0
                spent = await _points(client) - before
                pages = cache.pages(code, pull.fight_id, spec)
                events = sum(len(p.get("data") or []) for p in pages)
                rows.append(
                    f"| {pull.pull_key} | {spec.dir_name} | {spent:.1f} | {wall:.1f} | {len(pages)} | {events} |"
                )

            raw = [(spec.dir_name, (cache._dir(code, pull.fight_id, spec)).glob("page_*.json")) for spec in specs_for()]
            blobs = [p.read_bytes() for _, paths in raw for p in paths]
            t0 = time.perf_counter()
            for b in blobs:
                json.loads(b)
            t_json = time.perf_counter() - t0
            t0 = time.perf_counter()
            for b in blobs:
                orjson.loads(b)
            t_orjson = time.perf_counter() - t0

            pages = {s.dir_name: cache.pages(code, pull.fight_id, s) for s in specs_for()}
            tracemalloc.start()
            t0 = time.perf_counter()
            normalize_pull(pull, actors, pages)
            t_norm = time.perf_counter() - t0
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            parse_rows.append(f"| {pull.pull_key} | {t_json:.2f} | {t_orjson:.2f} | {t_norm:.2f} | {peak / 1e6:.0f} |")

    print("| Pull | Data type | Points | Wall s | Pages | Events |")
    print("|---|---|---|---|---|---|")
    print("\n".join(rows))
    print()
    print("| Pull | json parse s | orjson parse s | normalize s | peak MB |")
    print("|---|---|---|---|---|")
    print("\n".join(parse_rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("code")
    parser.add_argument("--fights", help="Comma-separated fight IDs (default: all)")
    args = parser.parse_args()
    load_dotenv()
    fights = [int(p) for p in args.fights.split(",")] if args.fights else None
    asyncio.run(spike(args.code, fights))


if __name__ == "__main__":
    main()
