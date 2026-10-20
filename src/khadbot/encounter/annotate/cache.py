"""
encounter/annotate/cache.py

Annotated frames cached in the blob store at ``annotated/<report>/<fight>__<encfile_hash>.parquet`` (under
``$KHADBOT_DATA_DIR`` in the local store), with a JSON sidecar holding the phases, cutoff, the ``events_hash`` and
``positions_hash`` they were built from, ``ANNOTATION_VERSION`` and a hash of the pull fields annotation reads.
``annotate_pull`` annotates a stored pull through the cache.
"""

from __future__ import annotations

import hashlib
import io
import os

import orjson
import polars as pl

from khadbot.encounter.annotate.frame import ANNOTATION_VERSION, Annotated
from khadbot.encounter.annotate.pipeline import annotate
from khadbot.encounter.config_files import EncounterFile, encfile_hash
from khadbot.encounter.models import PhaseSpan, Pull
from khadbot.encounter.store import EventStore, split_pull_key
from khadbot.storage import BlobStore, join_key, resolve_blobs

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def pull_inputs_hash(pull: Pull) -> str:
    """First 16 hex of SHA-256 over the ``Pull`` fields annotation reads, so changed pull metadata misses."""
    payload = {
        "start_ms": pull.start_ms,
        "end_ms": pull.end_ms,
        "duration_ms": pull.duration_ms,
        "kill": pull.kill,
        "phase_transitions": [list(t) for t in pull.phase_transitions],
        "friendly_player_ids": sorted(pull.friendly_player_ids),
    }
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()[:16]


class AnnotationCache:
    """
    Annotated frames in a ``BlobStore`` (under ``annotated/`` by default), keyed by pull and ``encfile_hash``;
    stale if events, positions, cutoff or the pull metadata annotation reads changed.

    ``blobs`` may also be a directory laid out like the cache itself (``<data_dir>/annotated``); see
    ``storage.resolve_blobs``.
    """

    def __init__(self, blobs: BlobStore | str | os.PathLike[str], prefix: str | None = None) -> None:
        self.blobs, self.prefix = resolve_blobs(blobs, prefix, "annotated")

    def _keys(self, pull: Pull, enc_hash: str) -> tuple[str, str]:
        base = join_key(self.prefix, pull.report_code, f"{pull.fight_id}__{enc_hash}")
        return f"{base}.parquet", f"{base}.json"

    def get(
        self, pull: Pull, enc_hash: str, events_hash: str, cutoff_fraction: float, *, positions_hash: str | None
    ) -> Annotated | None:
        frame_key, meta_key = self._keys(pull, enc_hash)
        meta_bytes = self.blobs.get(meta_key)
        if meta_bytes is None:
            return None
        meta = orjson.loads(meta_bytes)
        if (
            meta.get("events_hash") != events_hash
            or meta.get("positions_hash") != positions_hash
            or meta.get("cutoff_fraction") != cutoff_fraction
            or meta.get("annotation_version") != ANNOTATION_VERSION
            or meta.get("pull_hash") != pull_inputs_hash(pull)
        ):
            return None
        if (frame := self.blobs.get(frame_key)) is None:
            return None
        return Annotated(
            pull=pull,
            frame=pl.read_parquet(io.BytesIO(frame)),
            phases=[PhaseSpan.model_validate(p) for p in meta["phases"]],
            cutoff_ms=meta["cutoff_ms"],
            encfile_hash=enc_hash,
            unmatched_phase=meta.get("unmatched_phase"),
        )

    def put(self, a: Annotated, events_hash: str, cutoff_fraction: float, *, positions_hash: str | None) -> None:
        frame_key, meta_key = self._keys(a.pull, a.encfile_hash)
        meta = {
            "events_hash": events_hash,
            "positions_hash": positions_hash,
            "cutoff_fraction": cutoff_fraction,
            "annotation_version": ANNOTATION_VERSION,
            "pull_hash": pull_inputs_hash(a.pull),
            "cutoff_ms": a.cutoff_ms,
            "phases": [p.model_dump() for p in a.phases],
            "unmatched_phase": a.unmatched_phase,
        }
        frame = io.BytesIO()
        a.frame.write_parquet(frame)
        # The frame goes first, so a sidecar never describes a frame that isn't in place yet.
        self.blobs.put(frame_key, frame.getvalue())
        self.blobs.put(meta_key, orjson.dumps(meta))


def annotate_pull(
    pull_key: str,
    store: EventStore,
    enc: EncounterFile | None,
    *,
    cache: AnnotationCache | None = None,
    cutoff_fraction: float = 0.5,
) -> Annotated:
    """Annotate one stored pull, reusing ``cache`` when it holds a fresh result."""
    code, fight = split_pull_key(pull_key)
    pull = next((p for p in store.pulls(code) if p.fight_id == fight), None)
    if pull is None:
        raise KeyError(f"pull {pull_key} is not in the event store; ingest it first")
    ev_hash = store.events_hash(pull_key) or ""
    pos_hash = store.positions_hash(pull_key)
    enc_hash = encfile_hash(enc)
    if cache and (hit := cache.get(pull, enc_hash, ev_hash, cutoff_fraction, positions_hash=pos_hash)) is not None:
        return hit
    a = annotate(
        pull,
        store.events(pull_key),
        enc,
        store.actors(code),
        positions=store.positions(pull_key),
        cutoff_fraction=cutoff_fraction,
    )
    if cache:
        cache.put(a, ev_hash, cutoff_fraction, positions_hash=pos_hash)
    return a
