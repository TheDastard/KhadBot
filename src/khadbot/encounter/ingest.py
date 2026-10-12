"""
encounter/ingest.py

Ingest orchestration: report metadata -> raw pages -> normalized events -> event store.

Resumable through the raw page cache and idempotent through ``events_hash``: a pull whose
recomputed hash and metadata match the stored ones is skipped without rewriting anything.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from khadbot.config import EncounterConfig, get_config
from khadbot.encounter.fetch import (
    MissingFixtureError,
    ProgressFn,
    RawPageCache,
    fetch_pull,
    specs_for,
)
from khadbot.encounter.models import IngestResult, Pull
from khadbot.encounter.normalize import events_hash, normalize_pull, parse_abilities, parse_report_meta
from khadbot.encounter.store import EventStore, ParquetEventStore
from khadbot.encounter.wcl_queries import GET_REPORT_META
from khadbot.tools.warcraftlogs._client import WarcraftLogsAPIError, WarcraftLogsClient

logger = logging.getLogger(__name__)


def default_cache() -> RawPageCache:
    return RawPageCache(Path(EncounterConfig().data_dir) / "raw")


def default_store() -> ParquetEventStore:
    return ParquetEventStore(Path(EncounterConfig().data_dir) / "events")


async def _report_meta(client: WarcraftLogsClient | None, cache: RawPageCache, code: str, *, offline: bool) -> dict:
    if (meta := cache.report_meta(code)) is not None:
        return meta
    if offline or client is None:
        raise MissingFixtureError(f"Report metadata for '{code}' is not in the raw cache.")
    data = await client.query(GET_REPORT_META, {"code": code})
    report = (data.get("reportData") or {}).get("report")
    if report is None:
        raise WarcraftLogsAPIError(f"WarcraftLogs report '{code}' was not found.")
    cache.write_report_meta(code, report)
    return report


async def _points_spent(client: WarcraftLogsClient) -> float | None:
    try:
        return float((await client.rate_limit())["pointsSpentThisHour"])
    except Exception:  # noqa: BLE001 - cost reporting must never fail an ingest
        return None


async def ingest_report(
    code: str,
    fight_ids: list[int] | None = None,
    *,
    store: EventStore | None = None,
    cache: RawPageCache | None = None,
    offline: bool = False,
    progress: ProgressFn | None = None,
    client: WarcraftLogsClient | None = None,
    registry_spell_ids: Sequence[int] | None = None,
    enemy_cast_ids: Mapping[int, Sequence[int]] | None = None,
) -> IngestResult:
    """
    Ingest the requested fights of ``code`` (all encounter fights when ``fight_ids`` is None).

    ``registry_spell_ids`` filter the Buffs fetch; ``enemy_cast_ids`` maps encounter_id -> ability IDs for
    the enemy-Casts filter. A failure in one pull is recorded in ``failed`` and the rest continue.
    """
    store = store or default_store()
    cache = cache or default_cache()

    async with contextlib.AsyncExitStack() as stack:
        if client is None and not offline:
            wcl = get_config().warcraftlogs
            client = await stack.enter_async_context(WarcraftLogsClient(wcl.client_id, wcl.client_secret))

        before = await _points_spent(client) if client and not offline else None
        meta = await _report_meta(client, cache, code, offline=offline)
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
        for pull in wanted:
            key = pull.pull_key
            try:
                specs = specs_for(registry_spell_ids, (enemy_cast_ids or {}).get(pull.encounter_id))
                truncated = await fetch_pull(client, cache, code, pull, specs, offline=offline, progress=progress)
                if progress:
                    progress(key, "normalizing", 1.0)
                pages = {s.dir_name: cache.pages(code, pull.fight_id, s) for s in specs}
                df = normalize_pull(pull, actors, pages)
                new_hash = events_hash(df)
                # Pull metadata can change without the events (a field added to the report query), so both must match.
                if store.events_hash(key) == new_hash and stored.get(pull.fight_id) == pull:
                    skipped.append(key)
                    continue
                store.write_pull(pull, df, new_hash, sorted(name for name, t in truncated.items() if t))
                ingested.append(key)
            except Exception as exc:  # noqa: BLE001 - one bad pull must not sink the report
                logger.warning("Ingest failed for %s: %s", key, exc)
                failed[key] = str(exc)

        after = await _points_spent(client) if client and not offline else None

    spent = after - before if before is not None and after is not None else None
    return IngestResult(report_code=code, pulls=ingested, skipped=skipped, failed=failed, points_spent=spent)
