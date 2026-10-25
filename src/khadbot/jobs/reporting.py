"""
jobs/reporting.py

What a running queued job does besides its work: write progress to the ``jobs`` row and notice a cancellation.

``ProgressWriter`` is the ``Progress`` a job reports to. ``report`` only records the event; a background task writes
the latest state of each pull every ``interval`` seconds and reads the row's ``cancel_requested`` flag, so a job never
waits on the database. Once the flag is seen the next ``report`` raises ``JobCancelled``.

``CancellingRawCache`` is the raw page cache with the same check after every page it writes. Ingest stops at the page
boundary: the page just written stays written, the pull's earlier pages stay, and the next run resumes from them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from types import TracebackType
from typing import Self

from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.models import FetchSpec
from khadbot.jobs.failures import JobCancelled
from khadbot.jobs.status import JobStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class CancelFlag:
    """Set once a job's cancellation was requested; ``check`` raises ``JobCancelled`` from then on."""

    def __init__(self) -> None:
        self.requested = False

    def check(self) -> None:
        if self.requested:
            raise JobCancelled


class CancellingRawCache(RawPageCache):
    """The raw page cache of ``base``, raising ``JobCancelled`` right after a page is written once ``flag`` is set."""

    def __init__(self, base: RawPageCache, flag: CancelFlag) -> None:
        self.blobs = base.blobs
        self.prefix = base.prefix
        self._flag = flag

    def write_page(self, code: str, fight: int, spec: FetchSpec, index: int, page: dict) -> None:
        super().write_page(code, fight, spec, index, page)
        self._flag.check()

    def write_batch_page(self, code: str, spec: FetchSpec, fight_ids: Sequence[int], index: int, page: dict) -> None:
        super().write_batch_page(code, spec, fight_ids, index, page)
        self._flag.check()


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


class ProgressWriter:
    """
    Writes a job's progress events to its ``jobs`` row without blocking the job. Use it as an async context manager
    around the job: it starts the writer task on entry and, on exit, writes what is left.

    A failure to write is logged and dropped: progress is for the user, and must never fail a job.
    """

    def __init__(self, store: JobStore, job_id: str, flag: CancelFlag, interval: float = 0.5) -> None:
        self._store = store
        self._job_id = job_id
        self._flag = flag
        self._interval = interval
        self._pending: dict[str | None, tuple[str, float]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def report(self, stage: str, pull: str | None, fraction: float) -> None:
        self._flag.check()
        self._pending.pop(pull, None)  # keep the order of the latest updates
        self._pending[pull] = (stage, fraction)

    async def flush(self) -> None:
        pending, self._pending = self._pending, {}
        for pull, (stage, fraction) in pending.items():
            try:
                await self._store.report_progress(self._job_id, pull, stage, fraction)
            except Exception:  # noqa: BLE001 - see the class docstring
                logger.warning("Could not write progress for job %s", self._job_id, exc_info=True)

    async def _poll_cancel(self) -> None:
        try:
            if await self._store.cancel_requested(self._job_id):
                self._flag.requested = True
        except Exception:  # noqa: BLE001
            logger.warning("Could not read the cancel flag of job %s", self._job_id, exc_info=True)

    async def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self._interval)
            await self.flush()
            await self._poll_cancel()

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(self._run(), name=f"progress-{self._job_id}")
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        await self.flush()
