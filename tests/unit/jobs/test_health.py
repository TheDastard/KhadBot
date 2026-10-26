"""
tests/unit/jobs/test_health.py

Worker liveness: a fresh heartbeat file is alive, a stale or missing one is not, and the heartbeat keeps the file fresh
while a worker runs and removes it when the worker stops.
"""

from __future__ import annotations

import asyncio
import os
import time

from khadbot.config import LoggingConfig
from khadbot.jobs.health import beat, check_alive, heartbeat, is_alive


def test_a_fresh_file_is_alive_and_a_stale_or_missing_one_is_not(tmp_path):
    path = tmp_path / "alive"
    assert not is_alive(path, 60)
    beat(path)
    assert is_alive(path, 60)
    old = time.time() - 120
    os.utime(path, (old, old))
    assert not is_alive(path, 60)


async def test_the_heartbeat_keeps_the_file_fresh_and_removes_it_on_exit(tmp_path):
    cfg = LoggingConfig(worker_health_file=str(tmp_path / "alive"), worker_heartbeat_s=0.01, worker_stale_s=5)
    assert not check_alive(cfg)
    async with heartbeat(cfg):
        await asyncio.sleep(0.05)
        assert check_alive(cfg)
    assert not check_alive(cfg)
    assert not (tmp_path / "alive").exists()
