"""
encounter/annotate/cache.py

Annotated frames cached under ``$KHADBOT_DATA_DIR/annotated/<report>/<fight>__<encfile_hash>.parquet``,
with a JSON sidecar holding the phases, cutoff, the ``events_hash`` and ``positions_hash`` they were built
from and ``ANNOTATION_VERSION``. ``annotate_pull`` annotates a stored pull through the cache.
"""

from __future__ import annotations

import os
from pathlib import Path

import orjson
import polars as pl

from khadbot.encounter.annotate.frame import ANNOTATION_VERSION, Annotated
from khadbot.encounter.annotate.pipeline import annotate
from khadbot.encounter.config_files import EncounterFile, encfile_hash
from khadbot.encounter.models import PhaseSpan, Pull
from khadbot.encounter.store import EventStore, split_pull_key

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class AnnotationCache:
    """Annotated frames on disk, keyed by pull and ``encfile_hash``; stale if events, positions or cutoff changed."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _paths(self, pull: Pull, enc_hash: str) -> tuple[Path, Path]:
        base = self.root / pull.report_code / f"{pull.fight_id}__{enc_hash}"
        return base.with_suffix(".parquet"), base.with_suffix(".json")

    def get(
        self, pull: Pull, enc_hash: str, events_hash: str, cutoff_fraction: float, *, positions_hash: str | None
    ) -> Annotated | None:
        frame_path, meta_path = self._paths(pull, enc_hash)
        if not (frame_path.exists() and meta_path.exists()):
            return None
        meta = orjson.loads(meta_path.read_bytes())
        if (
            meta.get("events_hash") != events_hash
            or meta.get("positions_hash") != positions_hash
            or meta.get("cutoff_fraction") != cutoff_fraction
            or meta.get("annotation_version") != ANNOTATION_VERSION
        ):
            return None
        return Annotated(
            pull=pull,
            frame=pl.read_parquet(frame_path),
            phases=[PhaseSpan.model_validate(p) for p in meta["phases"]],
            cutoff_ms=meta["cutoff_ms"],
            encfile_hash=enc_hash,
            unmatched_phase=meta.get("unmatched_phase"),
        )

    def put(self, a: Annotated, events_hash: str, cutoff_fraction: float, *, positions_hash: str | None) -> None:
        frame_path, meta_path = self._paths(a.pull, a.encfile_hash)
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "events_hash": events_hash,
            "positions_hash": positions_hash,
            "cutoff_fraction": cutoff_fraction,
            "annotation_version": ANNOTATION_VERSION,
            "cutoff_ms": a.cutoff_ms,
            "phases": [p.model_dump() for p in a.phases],
            "unmatched_phase": a.unmatched_phase,
        }
        for path, write in (
            (frame_path, lambda tmp: a.frame.write_parquet(tmp)),
            (meta_path, lambda tmp: tmp.write_bytes(orjson.dumps(meta))),
        ):
            tmp = path.with_name(path.name + ".tmp")
            try:
                write(tmp)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)


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
