"""
jobs/backfill.py

Backfill: after an encounter-file or code change, queue a low-priority ``ProcessPull`` for each stored pull of one
encounter so its records and read-model rows are rebuilt. It never spends WCL points, by construction:

- a ``ProcessPull`` reads the stored events and the annotation cache, never the raw page cache and never WCL, so a
  pull whose raw pages have expired (``raw/`` is deleted after 14 days) is rebuilt from its events like any other;
- the jobs are queued with app auth, and no ingest is queued, so nothing in the chain holds or resolves a WCL token;
- the candidates are the pulls already in the read-model tables, i.e. pulls that were ingested.

What a backfill cannot fix: a change to the fetch filters (a new fetched ability, say) makes stored events stale
in a way only a fresh ingest repairs, and that ingest spends points. Backfill does not do it.

A pull that already has a queued or running process job is skipped, so a backfill run twice queues each pull once.
Processing an up-to-date pull writes nothing, so queueing pulls that need no rebuild costs only the worker's time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from khadbot.encounter.records_store import ReadModelIndex

if TYPE_CHECKING:
    from khadbot.jobs.queue import Enqueued

__all__ = ["BACKFILL_PRIORITY", "BackfillQueue", "BackfillResult", "parse_since", "run_backfill"]

# procrastinate runs higher priorities first and the default is 0, so a backfill waits behind interactive work.
BACKFILL_PRIORITY = -10

# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


class BackfillQueue(Protocol):
    async def enqueue_backfill(self, pull_key: str) -> Enqueued: ...


@dataclass(frozen=True)
class BackfillResult:
    """``matched`` pulls were selected; ``enqueued`` got a new job, ``already_queued`` already had one."""

    matched: list[str]
    enqueued: list[str] = field(default_factory=list)
    already_queued: list[str] = field(default_factory=list)


def parse_since(text: str) -> int:
    """A date (``2026-10-01``) or ISO timestamp as milliseconds since the epoch; a date without a zone is UTC."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


async def run_backfill(
    queue: BackfillQueue,
    index: ReadModelIndex,
    *,
    encounter_id: int,
    difficulty: int | None = None,
    since_ms: int | None = None,
    dry_run: bool = False,
) -> BackfillResult:
    """
    Queue a low-priority process job for each stored pull of ``encounter_id`` (one ``difficulty``, or all), of reports
    that started at or after ``since_ms``. A dry run selects and queues nothing.
    """
    matched = await asyncio.to_thread(index.pull_keys, encounter_id, difficulty, since_ms)
    if dry_run:
        return BackfillResult(matched)
    enqueued: list[str] = []
    already: list[str] = []
    for key in matched:
        result = await queue.enqueue_backfill(key)
        (enqueued if result.created else already).append(key)
    return BackfillResult(matched, enqueued, already)
