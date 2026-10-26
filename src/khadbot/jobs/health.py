"""
jobs/health.py

Worker liveness. A worker has no HTTP server, so it proves it is alive with a file: ``Heartbeat`` touches
``KHADBOT_WORKER_HEALTH_FILE`` every few seconds from the worker's event loop, and ``main.py worker --check`` (the
container health check) exits 0 only if the file was touched recently. If the loop is blocked or the process is gone
the file goes stale, so a hung worker fails the check as well as a dead one. The API's ``/v1/health`` covers the API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from khadbot.config import LoggingConfig

logger = logging.getLogger(__name__)


def beat(path: Path | str) -> None:
    """Touch the liveness file, creating it on the first call."""
    target = Path(path)
    target.touch()
    os.utime(target, None)


def is_alive(path: Path | str, max_age_s: float, *, now: float | None = None) -> bool:
    """Whether the file exists and was touched within ``max_age_s`` seconds."""
    try:
        age = (time.time() if now is None else now) - Path(path).stat().st_mtime
    except OSError:
        return False
    return age <= max_age_s


def check_alive(config: LoggingConfig | None = None) -> bool:
    """The worker health check: is the configured liveness file fresh?"""
    cfg = config or LoggingConfig()
    return is_alive(cfg.worker_health_file, cfg.worker_stale_s)


@contextlib.asynccontextmanager
async def heartbeat(config: LoggingConfig | None = None) -> AsyncIterator[None]:
    """Keep the liveness file fresh while the block runs, and remove it when the worker stops."""
    cfg = config or LoggingConfig()

    async def loop() -> None:
        while True:
            try:
                beat(cfg.worker_health_file)
            except OSError:
                logger.warning("Could not write the worker health file", exc_info=True)
            await asyncio.sleep(cfg.worker_heartbeat_s)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(OSError):
            Path(cfg.worker_health_file).unlink()


__all__ = ["beat", "check_alive", "heartbeat", "is_alive"]
