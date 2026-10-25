"""
scripts/data/build_fixture_data.py

Build a local data directory from the recorded WCL fixtures, offline, for web development and tests.

    mise run gen:fixture-data                     # writes .khadbot_fixture_data
    mise run serve:fixtures                       # serves it on 127.0.0.1:8000 (local mode, no sign-in)

or without mise:

    uv run python scripts/data/build_fixture_data.py --out .khadbot_fixture_data [--only CODE ...]
    set KHADBOT_DATA_DIR=.khadbot_fixture_data && uv run python main.py serve

Every real recorded report under --fixtures (default: tests/fixtures/encounter/wcl) goes through the steps the stack
seed runs, on a local directory instead of Postgres and a bucket: the pulls are ingested offline into
``<out>/events``, the report metadata is stored in ``<out>/raw`` so the API can show titles, and each pull is digested,
which fills the annotation cache (``<out>/annotated``) and the persisted pull records (``<out>/records``). Pages are
read from the fixtures and never copied. Running it again skips pulls whose events are unchanged and finds the rest
of the work cached.

The synthetic SYNTH01 report is left out: it needs an encounter file and registries of its own that a served data
directory cannot name, so only the stack seeds it. Needs the fixtures' Git LFS pages (``git lfs pull``); never calls
WCL and needs no Postgres, bucket or credentials.

``--only CODE`` builds just the named reports, so a checkout that pulled one report's pages (CI, for instance:
``git lfs pull --include "tests/fixtures/encounter/wcl/<CODE>/**"``) can build without the rest.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import orjson

from khadbot import wiring
from khadbot.encounter.ingest import ingest_recorded, recorded_reports
from khadbot.encounter.store import ParquetEventStore
from khadbot.storage import LocalBlobStore

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "encounter" / "wcl"


async def build(fixtures: Path, out: Path, only: Sequence[str] = ()) -> list[str]:
    """
    Ingest and digest every recorded report, or only the reports in ``only``, into the local data directory ``out``.

    Returns:
        One line per failure (an ingest failure, a digest failure or a missing recording); empty when all went well.
        A code in ``only`` with no recorded pages is a failure, and then nothing is built.
    """
    recorded = recorded_reports(fixtures)
    missing = [code for code in only if code not in recorded]
    if missing:
        return [
            f"No recorded pages for {code} under {fixtures}. Run "
            f'`git lfs pull --include "tests/fixtures/encounter/wcl/{code}/**"` first; SYNTH01 is never built here.'
            for code in missing
        ]
    reports = list(dict.fromkeys(only)) if only else recorded
    if not reports:
        return [f"No recorded reports under {fixtures}. Run `git lfs pull` first."]
    # Local blob store over ``out`` whatever the environment says: this command never writes a bucket.
    blobs = LocalBlobStore(out)
    store = ParquetEventStore(blobs)  # not wiring.event_store: hosted settings would add a Postgres report lock
    data = wiring.encounter_data(blobs)
    failures: list[str] = []
    for code in reports:
        wiring.raw_cache(blobs).write_report_meta(code, orjson.loads((fixtures / code / "report.json").read_bytes()))
        result = await ingest_recorded(code, fixtures, store)
        failures += [f"{key}: {error}" for key, error in result.failed.items()]
        digested = 0
        for key in [*result.pulls, *result.skipped]:
            try:
                data.records(key)
                digested += 1
            except Exception as exc:  # one bad pull must not stop the rest; it is reported at the end
                failures.append(f"{key}: {exc}")
        print(f"{code}: {len(result.pulls)} ingested, {len(result.skipped)} unchanged, {digested} digested")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, required=True, help="Data directory to write (KHADBOT_DATA_DIR layout)")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES, help="Recorded raw-page directory")
    parser.add_argument(
        "--only", nargs="+", default=[], metavar="CODE", help="Build just these reports (default: every recorded one)"
    )
    args = parser.parse_args()
    failures = asyncio.run(build(args.fixtures, args.out, args.only))
    for line in failures:
        print(f"  {line}", file=sys.stderr)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
