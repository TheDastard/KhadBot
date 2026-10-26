"""
tests/unit/encounter/test_store_lock.py

The event store's per-report write lock: process jobs for different pulls of one report rewrite ``pulls.json`` at
the same time, and none of their records may be lost. Also which lock the composition root gives each mode.
"""

from __future__ import annotations

import contextlib
import threading
from concurrent.futures import ThreadPoolExecutor

from khadbot import wiring
from khadbot.db import PostgresReportLock
from khadbot.encounter.models import Pull
from khadbot.encounter.store import ParquetEventStore, empty_events, local_report_lock
from khadbot.storage import LocalBlobStore

CODE = "LOCK01"


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
    """Widens the window between reading and writing ``pulls.json``, where an unlocked writer loses updates."""

    def get(self, key: str) -> bytes | None:
        data = super().get(key)
        if key.endswith("pulls.json"):
            threading.Event().wait(0.01)
        return data


def test_parallel_writers_keep_every_pull(tmp_path):
    store = ParquetEventStore(SlowBlobs(tmp_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda f: store.write_pull(make_pull(f), empty_events(), f"h{f}"), range(1, 17)))
    assert [p.fight_id for p in store.pulls(CODE)] == list(range(1, 17))
    assert store.events_hash(f"{CODE}:9") == "h9"


def test_the_injected_lock_is_taken_per_report(tmp_path):
    taken: list[str] = []

    @contextlib.contextmanager
    def lock(code: str):
        taken.append(code)
        yield

    store = ParquetEventStore(tmp_path, report_lock=lock)
    store.write_pull(make_pull(1), empty_events(), "h1")
    assert taken == [CODE]


def test_set_input_fingerprint_takes_the_lock(tmp_path):
    held: list[bool] = []
    inside = threading.local()

    @contextlib.contextmanager
    def lock(code: str):
        assert code == CODE
        inside.held = True
        try:
            yield
        finally:
            inside.held = False

    class Recording(LocalBlobStore):
        def put(self, key: str, data: bytes) -> None:
            if key.endswith("pulls.json"):
                held.append(getattr(inside, "held", False))
            super().put(key, data)

    store = ParquetEventStore(Recording(tmp_path), report_lock=lock)
    store.write_pull(make_pull(1), empty_events(), "h1")
    store.set_input_fingerprint(f"{CODE}:1", "fp-1")
    assert held == [True, True]  # both rewrites of pulls.json happened under the report's lock
    assert store.input_fingerprint(f"{CODE}:1") == "fp-1"


def test_local_lock_is_reentrant_across_reports():
    with local_report_lock("A"), local_report_lock("B"):
        pass  # different reports never block each other


def test_wiring_gives_hosted_mode_the_postgres_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("KHADBOT_MODE", "hosted")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = wiring.event_store(LocalBlobStore(tmp_path))  # builds without opening a pool
    assert isinstance(store._report_lock, PostgresReportLock)
    monkeypatch.delenv("KHADBOT_MODE")
    assert wiring.event_store(LocalBlobStore(tmp_path))._report_lock is local_report_lock
