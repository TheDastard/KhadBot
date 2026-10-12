"""
scripts/record_wcl_fixture.py

Record the raw WCL pages for a report into a fixture directory.

Needs WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET in the environment (or .env).

    uv run python scripts/record_wcl_fixture.py <code> [--fights 3,4] --out tests/fixtures/encounter/wcl
    uv run python scripts/record_wcl_fixture.py <code> --registry-ids 1,2 --encounter-ids 3,4 --out tests/fixtures/encounter/wcl

The output is a RawPageCache rooted at --out, so it can be used directly for offline ingest.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from khadbot.config import get_config
from khadbot.encounter.ingest.fetch import RawPageCache, fetch_pull, specs_for
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.encounter.ingest.pipeline import _report_meta
from khadbot.tools.warcraftlogs._client import WarcraftLogsClient


def _ids(text: str | None) -> list[int] | None:
    return [int(p) for p in text.split(",") if p.strip()] if text else None


async def record(code: str, fights: list[int] | None, out: Path, registry_ids, encounter_ids) -> None:
    cache = RawPageCache(out)
    wcl = get_config().warcraftlogs
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret) as client:
        meta = await _report_meta(client, cache, code, offline=False)
        pulls, _ = parse_report_meta(code, meta)
        for pull in pulls:
            if fights is not None and pull.fight_id not in fights:
                continue
            specs = specs_for(registry_ids, encounter_ids)
            print(f"recording {pull.pull_key} ({pull.encounter_name}, {'kill' if pull.kill else 'wipe'})")
            truncated = await fetch_pull(
                client, cache, code, pull, specs, progress=lambda k, s, p: print(f"  {s} {p:.0%}")
            )
            for name, flag in truncated.items():
                if flag:
                    print(f"  WARNING: {name} truncated")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("code")
    parser.add_argument("--fights", help="Comma-separated fight IDs (default: all)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--registry-ids", help="Cooldown spell IDs for the Buffs filter")
    parser.add_argument("--encounter-ids", help="Encounter ability IDs for the enemy Casts filter")
    args = parser.parse_args()
    load_dotenv()
    asyncio.run(record(args.code, _ids(args.fights), args.out, _ids(args.registry_ids), _ids(args.encounter_ids)))


if __name__ == "__main__":
    main()
