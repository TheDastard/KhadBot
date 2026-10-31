"""
scripts/data/seed_stack.py

Load the recorded fixture reports into a hosted stack, offline: no WCL calls, nothing written to a local data
directory. Run it inside the stack's image (``mise run stack:seed``), where it finds Postgres and the bucket through
the same environment as the API.

    docker compose -f deploy/compose.yaml --profile seed run --rm seed [--only CODE ...]

For each report it ingests the pulls into the bucket's event store, stores the report metadata, then runs the
process step per pull (annotate, build and store the records, project the read-model tables) through the same job
function the queue runs. Running it again changes nothing.

The reports are the synthetic SYNTH01 report, which needs no Git LFS pages, and every real recorded report whose
pages are present (``git lfs pull``). The real reports are large; ``--only SYNTH01`` is the quick seed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

import orjson

REPO = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES = REPO / "tests" / "fixtures" / "encounter" / "wcl"
SYNTHETIC = "SYNTH01"
SMOKE_USER = "smoke"  # scripts/dev/stack_smoke.py signs in as this test user

sys.path.insert(0, str(REPO / "tests"))  # tests/helpers is plain Python; the stack mounts it beside the fixtures


def refuse_unless_hosted() -> None:
    """The seed targets the stack only: both the database and the bucket must be configured."""
    from khadbot.config import DatabaseConfig, StorageBackend, StorageConfig

    if not DatabaseConfig().hosted or StorageConfig().backend is not StorageBackend.S3:
        sys.exit("Refusing to seed: set KHADBOT_MODE=hosted and KHADBOT_STORAGE=s3 (the stack's env file does).")


def report_codes(fixtures: Path, only: list[str]) -> list[str]:
    """The reports to seed: ``only`` when given, else SYNTH01 and every real recorded report with its pages."""
    from khadbot.encounter.ingest import recorded_reports

    if only:
        return only
    return [SYNTHETIC, *recorded_reports(fixtures)]


async def seed_report(code: str, fixtures: Path, scratch: Path) -> list[str]:
    """Ingest, store the metadata and process every pull of one report. Returns the failures."""
    from khadbot import wiring
    from khadbot.encounter.ingest import ingest_recorded, ingest_report
    from khadbot.encounter.ingest.fetch import RawPageCache
    from khadbot.jobs import JobContext, ProcessPull, run_process_pull

    blobs = wiring.blob_store()
    store = wiring.event_store(blobs)
    if code == SYNTHETIC:
        # SYNTH01 has an encounter file and registries of its own, and was recorded with filters of its own.
        from helpers.encounter.synth import (
            SYNTH_DEFENSIVE_COOLDOWN_IDS,
            SYNTH_DEFENSIVE_COOLDOWNS_FILE,
            SYNTH_ENEMY_CAST_IDS,
            SYNTH_OFFENSIVE_COOLDOWN_IDS,
            SYNTH_OFFENSIVE_COOLDOWNS_FILE,
            SYNTH_UTILITY_COOLDOWNS_FILE,
            install_synth_encounter,
        )

        from khadbot.encounter.config_files import load_cooldowns
        from khadbot.encounter.evidence import EncounterData

        result = await ingest_report(
            code,
            store=store,
            cache=RawPageCache(fixtures),
            offline=True,
            defensive_spell_ids=SYNTH_DEFENSIVE_COOLDOWN_IDS,
            offensive_spell_ids=SYNTH_OFFENSIVE_COOLDOWN_IDS,
            enemy_cast_ids=SYNTH_ENEMY_CAST_IDS,
        )
        install_synth_encounter(scratch)
        data = EncounterData(
            store,
            enc_root=scratch,
            defensive=load_cooldowns("defensive", SYNTH_DEFENSIVE_COOLDOWNS_FILE),
            offensive=load_cooldowns("offensive", SYNTH_OFFENSIVE_COOLDOWNS_FILE),
            utility=load_cooldowns("utility", SYNTH_UTILITY_COOLDOWNS_FILE),
            cache=wiring.annotation_cache(blobs),
            records_store=wiring.records_store(blobs),
        )
    else:
        result = await ingest_recorded(code, fixtures, store)
        data = wiring.encounter_data(blobs)

    # The read models take the report title and roster from the stored metadata.
    wiring.raw_cache(blobs).write_report_meta(code, orjson.loads((fixtures / code / "report.json").read_bytes()))

    failures = [f"{key}: {error}" for key, error in result.failed.items()]
    ctx = JobContext(data=data, store=store, cache=wiring.raw_cache(blobs))
    for key in [*result.pulls, *result.skipped]:
        try:
            await run_process_pull(ProcessPull(pull_key=key), ctx)
        except Exception as exc:  # one bad pull must not stop the rest; it is reported at the end
            failures.append(f"{key}: {exc}")
    print(f"{code}: {len(result.pulls)} ingested, {len(result.skipped)} unchanged, {len(failures)} failed")
    return failures


async def provision_smoke_session(codes: list[str]) -> None:
    """
    For the stack smoke test: the test user ``smoke`` (see ``khadbot.auth.dev_sessions``) with each seeded report
    cached as readable for it and as not public, so the smoke script can read them as that user and a signed-out
    visitor gets 404, all without a Warcraft Logs call. The smoke script then starts its own session for the user
    with ``khadbot test-session --user smoke``; the session started here is never handed to anyone.

    Does nothing unless test sessions are on (``KHADBOT_TEST_SESSIONS``, which only the stack's env file sets). The
    readable answers stand for the usual hour (``KHADBOT_ACCESS_RECHECK_S``) and the not-public ones for the denied
    re-check age (``KHADBOT_ACCESS_DENIED_RECHECK_S``, 5 minutes by default).
    """
    from khadbot.config import DevSessionsConfig

    dev = DevSessionsConfig()
    if not dev.enabled:
        print("test sessions are off (KHADBOT_TEST_SESSIONS): smoke user not provisioned")
        return
    from khadbot.auth.dev_sessions import issue_session
    from khadbot.auth.pg import PostgresAuthStore
    from khadbot.db import close_pool, get_pool

    try:
        await issue_session(PostgresAuthStore(get_pool()), dev, SMOKE_USER, reports=codes)
    finally:
        close_pool()  # the last use of the pool; open, its threads would fail at interpreter shutdown
    print(f"smoke user provisioned for {len(codes)} reports")


async def seed(fixtures: Path, only: list[str]) -> int:
    """Seed every chosen report. Returns the number of failures."""
    codes = report_codes(fixtures, only)
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for code in codes:
            if not (fixtures / code / "report.json").is_file():
                failures.append(f"{code}: no recorded report under {fixtures}")
                continue
            failures += await seed_report(code, fixtures, Path(tmp))
    for line in failures:
        print(f"  {line}", file=sys.stderr)
    await provision_smoke_session(codes)
    return len(failures)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES, help="Recorded raw-page directory")
    parser.add_argument("--only", nargs="+", default=[], metavar="CODE", help="Seed just these reports")
    args = parser.parse_args()
    refuse_unless_hosted()
    sys.exit(1 if asyncio.run(seed(args.fixtures, args.only)) else 0)


if __name__ == "__main__":
    main()
