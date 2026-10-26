"""
encounter/ingest/pipeline.py

Ingest orchestration: report metadata -> raw pages -> normalized events -> event store.

Resumable through the raw page cache and idempotent through ``events_hash`` and ``positions_hash``: a pull
whose recomputed hashes and metadata match the stored ones is skipped without rewriting anything. A pull
stored before positions were kept has no ``positions_hash``, so an offline re-ingest adds its position table.

``refresh_meta`` refetches only ``report.json`` and re-normalizes the stored pulls from their cached
pages, so a field added to the report query reaches pulls ingested before it without any event request.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping, Sequence

from khadbot.config import get_config
from khadbot.encounter.ingest.fetch import (
    FETCHED_WITH_KEY,
    MissingFixtureError,
    ProgressFn,
    RawPageCache,
    TokenProvider,
    fetch_batches,
    fetch_pull,
    input_fingerprint,
    query_wcl,
    specs_for,
)
from khadbot.encounter.ingest.normalize import (
    events_hash,
    normalize_pull_with_positions,
    parse_abilities,
    parse_report_meta,
)
from khadbot.encounter.ingest.wcl_queries import GET_REPORT_META
from khadbot.encounter.models import IngestResult, Pull
from khadbot.encounter.store import EventStore, positions_hash
from khadbot.wcl.client import WarcraftLogsAPIError, WarcraftLogsClient

logger = logging.getLogger(__name__)


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
    Ingest the requested fights of ``code`` (all encounter fights when ``fight_ids`` is None).

    ``defensive_spell_ids`` and ``offensive_spell_ids`` filter the two Buffs fetches, one per cooldown file;
    ``enemy_cast_ids`` maps encounter_id -> ability IDs for the enemy-Casts filter. A failure in one pull is
    recorded in ``failed`` and the rest continue.

    ``refresh_meta`` refetches the report metadata and rewrites the stored pulls whose metadata changed,
    reading their events from the raw page cache only; fights not stored yet are left alone. It needs WCL,
    so it cannot be combined with ``offline``.

    ``token_provider`` is an async callable returning a user's WCL access token. With it every WCL request goes
    to the user endpoint with that token and ``points_spent`` counts the user's budget; the report metadata
    records that a user token fetched it. With none, ingest uses the app token as before.

    The small specs several pulls still need are fetched first, one request per spec across those pulls
    (``fetch_batches``); a failed batch falls back to per-pull requests.
    """
    if refresh_meta and offline:
        raise ValueError("refresh_meta refetches report metadata from WCL and cannot run offline")
    async with contextlib.AsyncExitStack() as stack:
        if client is None and not offline:
            wcl = get_config().warcraftlogs
            client = await stack.enter_async_context(WarcraftLogsClient(wcl.client_id, wcl.client_secret))

        before = await _points_spent(client, token_provider) if client and not offline else None
        meta = await _report_meta(
            client, cache, code, offline=offline, refresh=refresh_meta, token_provider=token_provider
        )
        pulls, actors = parse_report_meta(code, meta)
        store.write_actors(code, actors)
        store.write_abilities(code, parse_abilities(meta))

        wanted: list[Pull] = pulls
        failed: dict[str, str] = {}
        if fight_ids is not None:
            by_id = {p.fight_id: p for p in pulls}
            wanted = [by_id[i] for i in fight_ids if i in by_id]
            for i in fight_ids:
                if i not in by_id:
                    failed[f"{code}:{i}"] = "fight not found in report"

        ingested: list[str] = []
        skipped: list[str] = []
        stored = {p.fight_id: p for p in store.pulls(code)}
        if refresh_meta:
            for p in wanted:
                if fight_ids is not None and p.fight_id not in stored:
                    failed[p.pull_key] = "not ingested yet; a metadata refresh only updates stored pulls"
            wanted = [p for p in wanted if p.fight_id in stored]
        specs_by_fight = {
            p.fight_id: specs_for(defensive_spell_ids, (enemy_cast_ids or {}).get(p.encounter_id), offensive_spell_ids)
            for p in wanted
        }
        batch_truncated: dict[int, set[str]] = {}
        if client is not None and not offline and not refresh_meta:
            try:
                batch_truncated = await fetch_batches(
                    client, cache, code, wanted, specs_by_fight, token_provider=token_provider
                )
            except Exception as exc:  # noqa: BLE001 - one bad batch must never sink the report
                logger.warning("Batched fetch failed for %s, fetching per pull: %s", code, exc)
        for pull in wanted:
            key = pull.pull_key
            try:
                specs = specs_by_fight[pull.fight_id]
                # A metadata refresh never requests event pages: they come from the raw cache only.
                truncated = await fetch_pull(
                    client,
                    cache,
                    code,
                    pull,
                    specs,
                    offline=offline or refresh_meta,
                    progress=progress,
                    token_provider=token_provider,
                )
                # Unchanged inputs skip the pull before any page is read.
                fingerprint = input_fingerprint(cache, pull, specs, actors)
                if (
                    fingerprint is not None
                    and store.input_fingerprint(key) == fingerprint
                    and stored.get(pull.fight_id) == pull
                ):
                    skipped.append(key)
                    continue
                if progress:
                    progress(key, "normalizing", 1.0)
                pages = {s.key: cache.pages(code, pull.fight_id, s) for s in specs}
                df, positions = normalize_pull_with_positions(pull, actors, pages)
                new_hash = events_hash(df)
                pos_hash = positions_hash(positions)
                # Pull metadata can change without the events (a field added to the report query), and a pull
                # stored before positions were kept has no positions_hash, so all three must match.
                if (
                    store.events_hash(key) == new_hash
                    and store.positions_hash(key) == pos_hash
                    and stored.get(pull.fight_id) == pull
                ):
                    if fingerprint is not None and store.input_fingerprint(key) != fingerprint:
                        store.set_input_fingerprint(key, fingerprint)
                    skipped.append(key)
                    continue
                truncated_specs = sorted(
                    {name for name, t in truncated.items() if t} | batch_truncated.get(pull.fight_id, set())
                )
                store.write_pull(
                    pull,
                    df,
                    new_hash,
                    truncated_specs,
                    positions,
                    input_fingerprint=fingerprint,
                    positions_hash=pos_hash,
                )
                ingested.append(key)
            except Exception as exc:  # noqa: BLE001 - one bad pull must not sink the report
                logger.warning("Ingest failed for %s: %s", key, exc)
                failed[key] = str(exc)

        after = await _points_spent(client, token_provider) if client and not offline else None

    spent = after - before if before is not None and after is not None else None
    return IngestResult(report_code=code, pulls=ingested, skipped=skipped, failed=failed, points_spent=spent)
