"""
jobs/retention.py

The records cleanup: removes ``records/`` objects that no read-model row references and that are older than the
minimum age. A records blob is never updated (changed inputs give a new key), so every encounter-file or code change
leaves the previous blob behind; this is what removes them. The bucket's lifecycle rules (``deploy/``) handle ``raw/``
and ``annotated/``; ``events/`` is never removed.

The age check is what makes the cleanup safe against a job in flight: a process job writes a blob and then projects
its row, and a blob younger than the minimum age is kept whether or not a row points at it yet. The row keys are read
after the listing, so a row added in between is still seen. A deleted blob that is needed again is rebuilt from the
stored events the next time its pull is read or processed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from khadbot import wiring
from khadbot.encounter.records_store import ReadModelIndex
from khadbot.storage import BlobStore

log = logging.getLogger(__name__)

__all__ = ["CleanupResult", "CleanupSources", "cleanup_records"]

RECORDS_PREFIX = "records/"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleanupResult:
    """What a cleanup pass found. ``deleted`` are the keys removed (or, in a dry run, that would be)."""

    examined: int
    kept_referenced: int
    kept_young: int
    deleted: list[str] = field(default_factory=list)


def cleanup_records(
    blobs: BlobStore,
    index: ReadModelIndex,
    *,
    min_age: timedelta,
    now: datetime | None = None,
    dry_run: bool = False,
) -> CleanupResult:
    """
    Delete every ``records/`` object that is at least ``min_age`` old and that no read-model row references.

    Running it twice deletes nothing the second time. A failure to delete one object is logged and the pass goes on.
    """
    now = now or datetime.now(UTC)
    listed = blobs.list_info(RECORDS_PREFIX)
    referenced = index.records_keys()  # after the listing: a row projected meanwhile still counts
    kept_referenced = kept_young = 0
    deleted: list[str] = []
    for info in listed:
        if info.key in referenced:
            kept_referenced += 1
        elif now - info.modified < min_age:
            kept_young += 1
        else:
            deleted.append(info.key)
    if not dry_run:
        removed: list[str] = []
        for key in deleted:
            try:
                blobs.delete(key)
            except Exception as exc:  # noqa: BLE001 - one object that will not delete must not stop the rest
                log.warning("could not delete records blob %s: %s", key, exc)
                continue
            removed.append(key)
        deleted = removed
    log.info(
        "records cleanup: %d examined, %d referenced, %d too young, %d %s",
        len(listed),
        kept_referenced,
        kept_young,
        len(deleted),
        "to delete" if dry_run else "deleted",
    )
    return CleanupResult(len(listed), kept_referenced, kept_young, deleted)


# ---------------------------------------------------------------------------
# What a worker cleans
# ---------------------------------------------------------------------------


@dataclass
class CleanupSources:
    """
    The blob store and read-model index a worker's cleanup runs against; each is built on first use from the
    configuration. Tests pass their own.
    """

    blobs: Callable[[], BlobStore] = wiring.blob_store
    index: Callable[[], ReadModelIndex | None] = wiring.read_model_index
