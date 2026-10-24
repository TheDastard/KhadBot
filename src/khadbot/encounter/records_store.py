"""
encounter/records_store.py

Persisted ``PullRecords``: the analysis records and digest for one pull as a JSON blob at
``records/<report>/<fight>__<hash>.json`` in the blob store, where ``<hash>`` covers the digest's ``produced_from``
(events, positions, encounter file, cooldown files, registry, code versions, cutoff and the whole ``Pull``). A
process that did not build a pull's records reads them here instead of re-running the analysis.

What the blob holds: the deaths, mechanics, cooldown uses, window coverage and the capped digest. What it leaves
out is rebuilt on load from stages that are already stored: the annotated frame (``annotated/``, through the
annotation cache), the pull, actors and abilities (the event store) and the encounter file (``config/``).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping
from typing import Protocol

import orjson
from pydantic import BaseModel, ValidationError

from khadbot.encounter.annotate import AnnotationCache, annotate_pull
from khadbot.encounter.config_files import EncounterFile
from khadbot.encounter.digest import PullRecords
from khadbot.encounter.models import CooldownUse, DeathRecord, MechanicStat, Pull, PullDigest, WindowCoverage
from khadbot.encounter.store import EventStore
from khadbot.storage import BlobStore, join_key, resolve_blobs

log = logging.getLogger(__name__)

__all__ = ["ReadModelProjector", "RecordsStore", "StoredRecords", "records_hash"]

# ---------------------------------------------------------------------------
# Read-model projection hook
# ---------------------------------------------------------------------------


class ReadModelProjector(Protocol):
    """Writes one pull's read-model rows (hosted mode). Must be idempotent: the same records twice change nothing."""

    async def project(self, records: PullRecords) -> None: ...


# ---------------------------------------------------------------------------
# Blob format
# ---------------------------------------------------------------------------


class StoredRecords(BaseModel):
    """The JSON body of a records blob."""

    deaths: list[DeathRecord]
    mechanics: list[MechanicStat]
    cooldowns: list[CooldownUse]
    windows: list[WindowCoverage]
    digest: PullDigest

    @classmethod
    def of(cls, r: PullRecords) -> StoredRecords:
        return cls(deaths=r.deaths, mechanics=r.mechanics, cooldowns=r.cooldowns, windows=r.windows, digest=r.digest)


def records_hash(inputs: Mapping[str, str]) -> str:
    """First 16 hex of SHA-256 over the ``produced_from`` inputs, the part of a records key that says what changed."""
    return hashlib.sha256(orjson.dumps(dict(inputs), option=orjson.OPT_SORT_KEYS)).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class RecordsStore:
    """
    ``PullRecords`` in a ``BlobStore`` (under ``records/`` by default), one blob per pull and set of inputs.

    A blob is never updated: changed inputs give a new key and the old blob stays until something deletes it.
    ``blobs`` may also be a directory that holds the records layout directly; see ``storage.resolve_blobs``.
    """

    def __init__(self, blobs: BlobStore | str | os.PathLike[str], prefix: str | None = None) -> None:
        self.blobs, self.prefix = resolve_blobs(blobs, prefix, "records")

    def key(self, pull: Pull, inputs: Mapping[str, str]) -> str:
        return join_key(self.prefix, pull.report_code, f"{pull.fight_id}__{records_hash(inputs)}.json")

    def get(
        self,
        pull: Pull,
        inputs: Mapping[str, str],
        *,
        store: EventStore,
        enc: EncounterFile | None,
        cache: AnnotationCache | None,
        cutoff_fraction: float,
    ) -> PullRecords | None:
        """
        The stored records for ``pull`` built from ``inputs``, or None on a miss. An unreadable, malformed or
        mismatched blob is a miss, logged, so the caller rebuilds and overwrites it.
        """
        key = self.key(pull, inputs)
        try:
            data = self.blobs.get(key)
            if data is None:
                return None
            stored = StoredRecords.model_validate_json(data)
        except (OSError, ValidationError, ValueError) as exc:
            log.warning("records blob %s is unreadable, rebuilding: %s", key, exc)
            return None
        if stored.digest.produced_from != dict(inputs):
            log.warning("records blob %s was built from other inputs, rebuilding", key)
            return None
        code = pull.report_code
        annotated = annotate_pull(pull.pull_key, store, enc, cache=cache, cutoff_fraction=cutoff_fraction)
        return PullRecords(
            pull=pull,
            annotated=annotated,
            enc=enc,
            actors=store.actors(code),
            abilities=store.abilities(code),
            deaths=stored.deaths,
            mechanics=stored.mechanics,
            cooldowns=stored.cooldowns,
            windows=stored.windows,
            digest=stored.digest,
        )

    def put(self, records: PullRecords) -> bool:
        """
        Store ``records`` under the key their digest's ``produced_from`` gives. A failed write is logged and
        returns False; it never raises, because the caller already has the records.
        """
        key = self.key(records.pull, records.digest.produced_from)
        try:
            self.blobs.put(key, StoredRecords.of(records).model_dump_json().encode())
        except Exception as exc:  # noqa: BLE001 - any storage failure must leave the read that built these intact
            log.warning("could not write records blob %s: %s", key, exc)
            return False
        return True

    def keys(self, report_code: str) -> list[str]:
        """Every records blob stored for a report."""
        return self.blobs.list(join_key(self.prefix, report_code) + "/")
