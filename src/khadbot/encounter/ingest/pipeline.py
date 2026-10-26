"""
encounter/ingest/pipeline.py

Ingest orchestration in two halves:

- ``fetch_report``: report metadata and raw pages only. It fetches several pulls at once (``wcl_pull_concurrency``;
  every request still waits for the process-wide ``wcl_max_concurrency`` permits) and, once all of a pull's specs are
  complete, writes the pull's fetch manifest. The ingest job runs this; it never writes the event store.
- ``process.normalize_pull_into_store``: one pull's manifest pages normalized into the event store. The process job
  runs it (``process.process_pull_events``).

``ingest_report`` runs both in one call, for in-process callers that want a populated event store at once: offline
recorded ingest (tests, ``scripts/data/build_fixture_data.py``), the stack seed and the smoke scripts. It writes no
manifest, because its caches are often recorded fixture directories that must stay unchanged.

Resumable through the raw page cache and idempotent: a repeated fetch with every page cached makes no request and
writes nothing (an unchanged manifest is not rewritten), and a pull whose recomputed hashes and metadata match the
stored ones is not rewritten.

``refresh_meta`` refetches only ``report.json``; the stored pulls' pages must already be cached (no event request),
and normalizing them again brings the new metadata to the stored pulls.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from khadbot.config import EncounterConfig, get_config
from khadbot.encounter.ingest.fetch import (
    FETCHED_WITH_KEY,
    MissingFixtureError,
    ProgressFn,
    RawPageCache,
    TokenProvider,
    fetch_batches,
    fetch_pull,
    query_wcl,
    specs_for,
)
from khadbot.encounter.ingest.normalize import parse_abilities, parse_report_meta
from khadbot.encounter.ingest.process import normalize_pull_into_store
from khadbot.encounter.ingest.wcl_queries import GET_REPORT_META
from khadbot.encounter.models import Actor, FetchManifest, FetchSpec, IngestResult, ManifestEntry, Pull
from khadbot.encounter.store import EventStore
from khadbot.wcl.client import WarcraftLogsAPIError, WarcraftLogsClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Report metadata and points
# ---------------------------------------------------------------------------


async def _report_meta(
    client: WarcraftLogsClient | None,
    cache: RawPageCache,
    code: str,
    *,
    offline: bool,
    refresh: bool = False,
    token_provider: TokenProvider | None = None,
) -> dict:
    if not refresh and (meta := cache.report_meta(code)) is not None:
        return meta
    if offline or client is None:
        raise MissingFixtureError(f"Report metadata for '{code}' is not in the raw cache.")
    data = await query_wcl(client, GET_REPORT_META, {"code": code}, token_provider)
    report = (data.get("reportData") or {}).get("report")
    if report is None:
        raise WarcraftLogsAPIError(f"WarcraftLogs report '{code}' was not found.")
    # Which kind of token fetched it, never which user: a report fetched with a user token may be private.
    report = {**report, FETCHED_WITH_KEY: "user" if token_provider else "app"}
    cache.write_report_meta(code, report)
    return report


async def _points_spent(client: WarcraftLogsClient, token_provider: TokenProvider | None = None) -> float | None:
    """Points spent this hour on the budget the ingest draws from: the user's with a provider, else the app's."""
    try:
        user_token = await token_provider() if token_provider else None
        return float((await client.rate_limit(user_token=user_token))["pointsSpentThisHour"])
    except Exception:  # noqa: BLE001 - cost reporting must never fail an ingest
        return None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


@dataclass
class FetchedReport:
    """
    What ``fetch_report`` left in the raw page cache. Every pull in ``pulls`` has complete pages for the specs its
    manifest names (``manifests``, by fight ID). ``written`` are the keys of the pulls whose manifest is new or
    changed (pages fetched now or by an interrupted run, or another filter); ``cached`` those whose manifest was
    already stored as is.
    """

    report_code: str
    meta: dict
    pulls: list[Pull] = field(default_factory=list)
    actors: list[Actor] = field(default_factory=list)
    manifests: dict[int, FetchManifest] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    points_spent: float | None = None


def _manifest(specs: Sequence[FetchSpec], truncated: Mapping[str, bool], carried: Sequence[str]) -> FetchManifest:
    return FetchManifest(
        specs=[
            ManifestEntry(**spec.model_dump(), truncated=bool(truncated.get(spec.key)) or spec.key in carried)
            for spec in specs
        ]
    )


async def fetch_report(
    code: str,
    fight_ids: list[int] | None = None,
    *,
    cache: RawPageCache,
    store: EventStore,
    offline: bool = False,
    progress: ProgressFn | None = None,
    client: WarcraftLogsClient | None = None,
    defensive_spell_ids: Sequence[int] | None = None,
    enemy_cast_ids: Mapping[int, Sequence[int]] | None = None,
    refresh_meta: bool = False,
    offensive_spell_ids: Sequence[int] | None = None,
    token_provider: TokenProvider | None = None,
    write_manifests: bool = True,
    pull_concurrency: int | None = None,
) -> FetchedReport:
    """
    Fetch the report metadata and the raw pages of the requested fights of ``code`` (all encounter fights when
    ``fight_ids`` is None). Writes nothing outside the raw page cache; ``store`` is only read.

    ``defensive_spell_ids`` and ``offensive_spell_ids`` filter the two Buffs fetches, one per cooldown file;
    ``enemy_cast_ids`` maps encounter_id -> ability IDs for the enemy-Casts filter. The small specs several pulls
    still need are fetched first, one request per spec across those pulls (``fetch_batches``); a failed batch falls
    back to per-pull requests. Then up to ``pull_concurrency`` pulls (default ``wcl_pull_concurrency``) are fetched
    at once. A failure in one pull is recorded in ``failed`` and the others go on. A cancellation (a
    ``BaseException`` that is not an ``Exception``, such as the queue's ``JobCancelled``) lets the running pulls
    reach their next page boundary, starts no new pull and is re-raised.

    Each pull that completes gets a manifest of the specs it was fetched with and their truncation flags, written to
    the cache with ``write_manifests`` unless the stored one is the same. A pull with no manifest yet that is already
    in the event store carries the store's ``truncated`` list over (it was fetched before manifests existed).

    ``refresh_meta`` refetches the report metadata and fetches no event page: the stored pulls' pages must be cached,
    with the specs of their manifest when they have one. Fights not stored yet fail. It cannot run ``offline``.

    ``token_provider`` makes every WCL request use a signed-in user's token and ``points_spent`` count the user's
    budget; the report metadata records that a user token fetched it. Without it ingest uses the app token.
    """
    if refresh_meta and offline:
        raise ValueError("refresh_meta refetches report metadata from WCL and cannot run offline")
    cfg = EncounterConfig()
    async with contextlib.AsyncExitStack() as stack:
        if client is None and not offline:
            wcl = get_config().warcraftlogs
            client = await stack.enter_async_context(WarcraftLogsClient(wcl.client_id, wcl.client_secret))

        before = await _points_spent(client, token_provider) if client and not offline else None
        meta = await _report_meta(
            client, cache, code, offline=offline, refresh=refresh_meta, token_provider=token_provider
        )
        pulls, actors = parse_report_meta(code, meta)
        out = FetchedReport(report_code=code, meta=meta, actors=actors)

        wanted: list[Pull] = pulls
        failed: dict[str, str] = {}
        if fight_ids is not None:
            by_id = {p.fight_id: p for p in pulls}
            wanted = [by_id[i] for i in fight_ids if i in by_id]
            for i in fight_ids:
                if i not in by_id:
                    failed[f"{code}:{i}"] = "fight not found in report"

        stored = {p.fight_id for p in store.pulls(code)}
        if refresh_meta:
            for p in wanted:
                if fight_ids is not None and p.fight_id not in stored:
                    failed[p.pull_key] = "not ingested yet; a metadata refresh only updates stored pulls"
            wanted = [p for p in wanted if p.fight_id in stored]
        previous = {p.fight_id: cache.manifest(code, p.fight_id) for p in wanted}
        specs_by_fight: dict[int, list[FetchSpec]] = {}
        for p in wanted:
            prior = previous[p.fight_id]
            if refresh_meta and prior is not None:
                specs_by_fight[p.fight_id] = prior.fetch_specs()  # the pages it was fetched with
            else:
                specs_by_fight[p.fight_id] = specs_for(
                    defensive_spell_ids, (enemy_cast_ids or {}).get(p.encounter_id), offensive_spell_ids
                )

        batch_truncated: dict[int, set[str]] = {}
        if client is not None and not offline and not refresh_meta:
            try:
                batch_truncated = await fetch_batches(
                    client, cache, code, wanted, specs_by_fight, token_provider=token_provider
                )
            except Exception as exc:  # noqa: BLE001 - one bad batch must never sink the report
                logger.warning("Batched fetch failed for %s, fetching per pull: %s", code, exc)

        slots = asyncio.Semaphore(max(1, pull_concurrency or cfg.wcl_pull_concurrency))
        stop = asyncio.Event()
        done: dict[int, tuple[FetchManifest, bool]] = {}
        errors: dict[int, str] = {}

        async def one(pull: Pull) -> None:
            async with slots:
                if stop.is_set():
                    return
                try:
                    specs = specs_by_fight[pull.fight_id]
                    # A metadata refresh never requests event pages: they come from the raw cache only.
                    flags = await fetch_pull(
                        client,
                        cache,
                        code,
                        pull,
                        specs,
                        offline=offline or refresh_meta,
                        progress=progress,
                        token_provider=token_provider,
                    )
                    in_batch = batch_truncated.get(pull.fight_id, set())
                    flags = {k: v or k in in_batch for k, v in flags.items()}
                    prior = previous[pull.fight_id]
                    carried = store.truncated(pull.pull_key) if prior is None and pull.fight_id in stored else []
                    manifest = _manifest(specs, flags, carried)
                    changed = manifest != prior
                    if changed and write_manifests:
                        cache.write_manifest(code, pull.fight_id, manifest)
                    done[pull.fight_id] = (manifest, changed)
                except Exception as exc:  # noqa: BLE001 - one bad pull must not sink the report
                    logger.warning("Ingest failed for %s: %s", pull.pull_key, exc)
                    errors[pull.fight_id] = str(exc)
                except BaseException:
                    stop.set()  # a cancellation: start no further pull
                    raise

        outcomes = await asyncio.gather(*(one(p) for p in wanted), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome

        for pull in wanted:
            if pull.fight_id in errors:
                failed[pull.pull_key] = errors[pull.fight_id]
            elif pull.fight_id in done:
                manifest, changed = done[pull.fight_id]
                out.pulls.append(pull)
                out.manifests[pull.fight_id] = manifest
                (out.written if changed else out.cached).append(pull.pull_key)
        out.failed = failed

        after = await _points_spent(client, token_provider) if client and not offline else None

    out.points_spent = after - before if before is not None and after is not None else None
    return out


# ---------------------------------------------------------------------------
# Fetch and normalize in one call
# ---------------------------------------------------------------------------


async def ingest_report(
    code: str,
    fight_ids: list[int] | None = None,
    *,
    store: EventStore,
    cache: RawPageCache,
    offline: bool = False,
    progress: ProgressFn | None = None,
    client: WarcraftLogsClient | None = None,
    defensive_spell_ids: Sequence[int] | None = None,
    enemy_cast_ids: Mapping[int, Sequence[int]] | None = None,
    refresh_meta: bool = False,
    offensive_spell_ids: Sequence[int] | None = None,
    token_provider: TokenProvider | None = None,
) -> IngestResult:
    """
    Fetch the requested fights of ``code`` (``fetch_report``, without writing manifests) and normalize each fetched
    pull into ``store`` (``normalize_pull_into_store``, in a worker thread), with the report's actors and abilities.
    The result's ``pulls`` are the pulls written and ``skipped`` those whose stored data already matched. A failure in
    one pull is recorded in ``failed`` and the rest continue.

    The jobs split this in two: the ingest job fetches, and a process job per pull normalizes from the manifest.
    """
    fetched = await fetch_report(
        code,
        fight_ids,
        cache=cache,
        store=store,
        offline=offline,
        progress=progress,
        client=client,
        defensive_spell_ids=defensive_spell_ids,
        enemy_cast_ids=enemy_cast_ids,
        refresh_meta=refresh_meta,
        offensive_spell_ids=offensive_spell_ids,
        token_provider=token_provider,
        write_manifests=False,
    )
    store.write_actors(code, fetched.actors)
    store.write_abilities(code, parse_abilities(fetched.meta))
    ingested: list[str] = []
    skipped: list[str] = []
    failed = dict(fetched.failed)
    for pull in fetched.pulls:
        key = pull.pull_key
        if progress:
            progress(key, "normalizing", 1.0)
        try:
            manifest = fetched.manifests[pull.fight_id]
            written = await asyncio.to_thread(normalize_pull_into_store, store, cache, pull, fetched.actors, manifest)
        except Exception as exc:  # noqa: BLE001 - one bad pull must not sink the report
            logger.warning("Normalize failed for %s: %s", key, exc)
            failed[key] = str(exc)
            continue
        (ingested if written else skipped).append(key)
    return IngestResult(
        report_code=code, pulls=ingested, skipped=skipped, failed=failed, points_spent=fetched.points_spent
    )


__all__ = ["FetchedReport", "fetch_report", "ingest_report"]
