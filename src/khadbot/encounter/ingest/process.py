"""
encounter/ingest/process.py

The per-pull half of ingest: a pull's fetched raw pages normalized into the event store. The ingest job only
fetches (``pipeline.fetch_report``); the process job calls ``process_pull_events`` before it builds records, and the
in-process ``ingest_report`` calls ``normalize_pull_into_store`` right after fetching.

``normalize_pull_into_store`` is the one place that reads a pull's pages, normalizes them, compares the result with
the stored events and writes it. A pull whose input fingerprint (``fetch.input_fingerprint``: normalize version,
pull metadata, actors, and each spec's page names and sizes) and metadata match the stored ones is skipped before any
page is read; otherwise one whose events hash, positions hash and metadata all match is left alone (only a missing
or stale fingerprint is recorded), so running it twice writes nothing. It normalizes exactly the specs in the fetch manifest it is given, never specs
derived from the authored files, which may have changed since the fetch.

``process_pull_events`` adds what a process job meets in a cache it did not fill itself: raw pages that expired
(hosted mode keeps ``raw/`` for 14 days) or a pull fetched before manifests existed. With stored events those are
final; with neither raw pages nor events the pull cannot be processed (``PullNotFetchedError``).
"""

from __future__ import annotations

import logging
from enum import StrEnum

from khadbot.encounter.ingest.fetch import RawPageCache, input_fingerprint
from khadbot.encounter.ingest.normalize import (
    events_hash,
    normalize_pull_with_positions,
    parse_abilities,
    parse_report_meta,
)
from khadbot.encounter.models import Actor, FetchManifest, Pull
from khadbot.encounter.store import EventStore, positions_hash, split_pull_key

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Outcomes and errors
# ---------------------------------------------------------------------------


class EventsOutcome(StrEnum):
    """What processing did to a pull's stored events."""

    WRITTEN = "written"  # normalized and stored: new, or changed
    UNCHANGED = "unchanged"  # normalized; the stored events, positions and metadata already matched
    KEPT = "kept"  # no fetched pages to normalize (expired, or fetched before manifests); stored events are final


class PullNotFetchedError(KeyError):
    """A pull has neither complete raw pages with a fetch manifest nor stored events, so there is nothing to process."""

    def __init__(self, pull_key: str) -> None:
        super().__init__(
            f"{pull_key} has neither fetched raw pages nor stored events; ingest the report again "
            "(raw pages expire after 14 days in hosted mode)."
        )
        self.pull_key = pull_key

    def __str__(self) -> str:  # KeyError's own str() quotes the message
        return str(self.args[0])


# ---------------------------------------------------------------------------
# Normalize one pull
# ---------------------------------------------------------------------------


def normalize_pull_into_store(
    store: EventStore, cache: RawPageCache, pull: Pull, actors: list[Actor], manifest: FetchManifest
) -> bool:
    """
    Read the pages ``manifest`` names, normalize them and store the pull unless it is unchanged. Returns True when it
    wrote the pull; False when the input fingerprint matched (no page read) or the normalized events, positions and
    metadata matched the stored ones (then only a missing or stale fingerprint is recorded). ``truncated`` comes
    from the manifest.
    """
    code, key = pull.report_code, pull.pull_key
    specs = manifest.fetch_specs()
    stored = next((p for p in store.pulls(code) if p.fight_id == pull.fight_id), None)
    # Unchanged inputs skip the pull before any page is read.
    fingerprint = input_fingerprint(cache, pull, specs, actors)
    if fingerprint is not None and fingerprint == store.input_fingerprint(key) and stored == pull:
        return False
    pages = {spec.key: cache.pages(code, pull.fight_id, spec) for spec in specs}
    df, positions = normalize_pull_with_positions(pull, actors, pages)
    new_hash = events_hash(df)
    pos_hash = positions_hash(positions)
    # Pull metadata can change without the events (a field added to the report query), and a pull stored before
    # positions were kept has no positions_hash, so all three must match.
    if store.events_hash(key) == new_hash and store.positions_hash(key) == pos_hash and stored == pull:
        if fingerprint is not None and store.input_fingerprint(key) != fingerprint:
            store.set_input_fingerprint(key, fingerprint)
        return False
    store.write_pull(
        pull,
        df,
        new_hash,
        manifest.truncated(),
        positions,
        input_fingerprint=fingerprint,
        positions_hash=pos_hash,
    )
    return True


def process_pull_events(store: EventStore, cache: RawPageCache, pull_key: str) -> EventsOutcome:
    """
    Bring a pull's stored events up to date with its fetched raw pages, for the process job.

    The pages are the ones its fetch manifest names, and the pull and actors come from the cached ``report.json``
    (so a metadata refresh reaches the stored pull). The report's actors and abilities are rewritten only when they
    differ from the stored ones. Without a manifest, ``report.json`` or a complete spec, stored events are kept as
    final (``KEPT``).

    Raises:
        PullNotFetchedError: Nothing to normalize and no stored events.
    """
    code, fight = split_pull_key(pull_key)
    manifest = cache.manifest(code, fight)
    meta = cache.report_meta(code) if manifest is not None else None
    pull: Pull | None = None
    actors: list[Actor] = []
    if manifest is not None and meta is not None:
        pulls, actors = parse_report_meta(code, meta)
        pull = next((p for p in pulls if p.fight_id == fight), None)
    if (
        manifest is None
        or meta is None
        or pull is None
        or not all(cache.is_complete(code, fight, spec) for spec in manifest.fetch_specs())
    ):
        if store.events_hash(pull_key) is not None:
            logger.info("No fetched pages to normalize for %s; keeping its stored events", pull_key)
            return EventsOutcome.KEPT
        raise PullNotFetchedError(pull_key)
    assert meta is not None and manifest is not None  # narrowed above
    if store.actors(code) != actors:
        store.write_actors(code, actors)
    abilities = parse_abilities(meta)
    if store.abilities(code) != abilities:
        store.write_abilities(code, abilities)
    written = normalize_pull_into_store(store, cache, pull, actors, manifest)
    return EventsOutcome.WRITTEN if written else EventsOutcome.UNCHANGED


__all__ = ["EventsOutcome", "PullNotFetchedError", "normalize_pull_into_store", "process_pull_events"]
