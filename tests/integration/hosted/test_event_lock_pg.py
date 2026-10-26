"""
tests/integration/hosted/test_event_lock_pg.py

``PostgresReportLock``: the event store's per-report write lock holds across connection pools (worker processes),
so parallel process jobs of one report keep every pull in ``pulls.json``.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from khadbot.db import PostgresReportLock, open_pool
from khadbot.encounter.models import Pull
from khadbot.encounter.store import ParquetEventStore, empty_events
from khadbot.storage import LocalBlobStore

CODE = "LOCKPG"


def make_pull(fight: int) -> Pull:
    return Pull(
        report_code=CODE,
        fight_id=fight,
        encounter_id=7,
        encounter_name="Boss",
        difficulty=5,
        kill=False,
        start_ms=fight * 1000,
        end_ms=fight * 1000 + 500,
        duration_ms=500,
        last_phase=1,
        boss_pct=50.0,
        friendly_player_ids=[1],
    )


class SlowBlobs(LocalBlobStore):
    def get(self, key: str) -> bytes | None:
        data = super().get(key)
        if key.endswith("pulls.json"):
            threading.Event().wait(0.01)
        return data


def test_two_pools_writing_one_report_keep_every_pull(database_url, tmp_path):
    first = open_pool(database_url, min_size=1, max_size=4)
    second = open_pool(database_url, min_size=1, max_size=4)
    try:
        blobs = SlowBlobs(tmp_path)
        # Two "workers": separate stores, separate pools, so only the advisory lock serializes them.
        stores = [
            ParquetEventStore(blobs, report_lock=PostgresReportLock(lambda: first)),
            ParquetEventStore(blobs, report_lock=PostgresReportLock(lambda: second)),
        ]
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(
                pool.map(
                    lambda f: stores[f % 2].write_pull(make_pull(f), empty_events(), f"h{f}"),
                    range(1, 13),
                )
            )
        assert [p.fight_id for p in stores[0].pulls(CODE)] == list(range(1, 13))
    finally:
        first.close()
        second.close()
