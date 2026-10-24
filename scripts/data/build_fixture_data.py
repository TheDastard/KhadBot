"""
scripts/data/build_fixture_data.py

Build a data directory from the recorded WCL fixtures, offline, for the dashboard (local demos, Playwright).

    uv run python scripts/data/build_fixture_data.py --out .khadbot_fixture_data
    set KHADBOT_DATA_DIR=.khadbot_fixture_data && uv run python main.py serve

Every recorded report under --fixtures (default: tests/fixtures/encounter/wcl, synthetic reports excluded) is
ingested into ``<out>/events``, and its ``report.json`` is copied to ``<out>/raw`` so the API can show the report
title. Event pages are not copied. Running it again skips pulls whose events are unchanged. Needs the fixtures'
Git LFS pages (``git lfs pull``); it never calls WCL.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from khadbot.encounter.ingest import ingest_recorded, recorded_reports
from khadbot.encounter.store import ParquetEventStore

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "encounter" / "wcl"


async def build(fixtures: Path, out: Path) -> int:
    """Ingest every recorded report into ``out``. Returns the number of failed pulls."""
    reports = recorded_reports(fixtures)
    if not reports:
        print(f"No recorded reports under {fixtures}. Run `git lfs pull` first.", file=sys.stderr)
        return 1
    store = ParquetEventStore(out / "events")
    failed = 0
    for code in reports:
        meta = out / "raw" / code / "report.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixtures / code / "report.json", meta)
        result = await ingest_recorded(code, fixtures, store)
        failed += len(result.failed)
        print(f"{code}: {len(result.pulls)} ingested, {len(result.skipped)} unchanged, {len(result.failed)} failed")
        for key, error in result.failed.items():
            print(f"  {key}: {error}", file=sys.stderr)
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[1], formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, required=True, help="Data directory to write (KHADBOT_DATA_DIR layout)")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES, help="Recorded raw-page directory")
    args = parser.parse_args()
    sys.exit(1 if asyncio.run(build(args.fixtures, args.out)) else 0)


if __name__ == "__main__":
    main()
