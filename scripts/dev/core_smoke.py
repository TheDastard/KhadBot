"""
scripts/dev/core_smoke.py

Smoke test for a core-plus-cli install (``uv sync --no-dev --extra cli``): import ``khadbot.main``, ingest the
recorded synthetic report offline, and print one pull's digest through the ``digest`` subcommand. It needs no
optional group and no network, and uses a temporary data directory.

    uv run --no-sync python scripts/dev/core_smoke.py

The synthetic report was recorded with filters of its own, so the ingest passes them in. The path a user runs,
``main.py ingest <code> --offline``, takes them from the registries.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))  # tests/helpers is plain Python with no test-only dependency here

CODE = "SYNTH01"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        os.environ["KHADBOT_DATA_DIR"] = str(data)
        os.environ.setdefault("WARCRAFTLOGS_CLIENT_ID", "smoke-placeholder")
        os.environ.setdefault("WARCRAFTLOGS_CLIENT_SECRET", "smoke-placeholder")

        from helpers.encounter.synth import (
            FIXTURES,
            SYNTH_DEFENSIVE_COOLDOWN_IDS,
            SYNTH_ENEMY_CAST_IDS,
            SYNTH_OFFENSIVE_COOLDOWN_IDS,
        )

        import khadbot.main as khadbot_main
        from khadbot import wiring
        from khadbot.encounter.ingest import ingest_report

        shutil.copytree(FIXTURES / "wcl" / CODE, data / "raw" / CODE)
        result = asyncio.run(
            ingest_report(
                CODE,
                store=wiring.event_store(),
                cache=wiring.raw_cache(),
                offline=True,
                defensive_spell_ids=SYNTH_DEFENSIVE_COOLDOWN_IDS,
                offensive_spell_ids=SYNTH_OFFENSIVE_COOLDOWN_IDS,
                enemy_cast_ids=SYNTH_ENEMY_CAST_IDS,
            )
        )
        if result.failed or not result.pulls:
            sys.exit(f"offline ingest failed: {result.failed or 'no pulls written'}")
        print(f"ingested {len(result.pulls)} pulls: {', '.join(result.pulls)}")
        khadbot_main.main(["digest", CODE, "1"])


if __name__ == "__main__":
    main()
