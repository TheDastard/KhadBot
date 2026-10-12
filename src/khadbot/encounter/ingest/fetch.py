"""
encounter/ingest/fetch.py

Raw WCL page cache and the paginated fetcher.

Every page WCL returns is written to disk as it arrives, so an interrupted ingest
resumes from the last cached page and a finished one never touches the network again.
Layout (rooted at ``$KHADBOT_DATA_DIR/raw``)::

    <report>/report.json
    <report>/<fight>/<dir_name>/<filter_hash>/page_000.json ...
    <report>/<fight>/<dir_name>/<filter_hash>/_complete

``filter_hash`` is the first 12 hex of SHA-256 of the filter expression (``"none"`` when
there is none), so changing a filter refetches only that data type.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import orjson

from khadbot.config import EncounterConfig
from khadbot.encounter.ingest.wcl_queries import GET_EVENTS_PAGE
from khadbot.encounter.models import FetchSpec, Pull
from khadbot.tools.warcraftlogs._client import WarcraftLogsAPIError, WarcraftLogsClient, WarcraftLogsRateLimitError

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, str, float], None]  # (pull_key, stage, pct)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MissingFixtureError(Exception):
    """Raised in offline mode when a page set is not in the raw cache."""


# ---------------------------------------------------------------------------
# Filters and default specs
# ---------------------------------------------------------------------------

DEFAULT_SPECS: list[FetchSpec] = [
    FetchSpec(data_type="Deaths"),
    FetchSpec(data_type="DamageTaken"),
    FetchSpec(data_type="Casts"),
    FetchSpec(data_type="Casts", hostility="Enemies"),
    FetchSpec(data_type="Buffs"),
    FetchSpec(data_type="Debuffs"),
    FetchSpec(data_type="Interrupts"),
    FetchSpec(data_type="Dispels"),
    FetchSpec(data_type="Healing"),
    # Friendlies Threat: NPC melee swings at players (misses included) and taunts. The Enemies side is
    # player threat generation, about 40 times larger, and is not fetched.
    FetchSpec(data_type="Threat"),
]


def _ids_filter(ids: Sequence[int]) -> str | None:
    unique = sorted(set(ids))
    return f"ability.id IN ({','.join(str(i) for i in unique)})" if unique else None


def build_filter(
    data_type: str,
    hostility: str = "Friendlies",
    *,
    registry_spell_ids: Sequence[int] | None = None,
    encounter_ability_ids: Sequence[int] | None = None,
) -> str | None:
    """
    Default filter policy per data type (to be tuned against measured WCL costs).

    Buffs: only cooldown-registry spell IDs. Enemy Casts: only the encounter file's ability IDs.
    Everything else is unfiltered. With no IDs available a filter is omitted, not emptied.
    """
    if data_type == "Buffs" and hostility == "Friendlies":
        return _ids_filter(registry_spell_ids or [])
    if data_type == "Casts" and hostility == "Enemies":
        return _ids_filter(encounter_ability_ids or [])
    return None


def specs_for(
    registry_spell_ids: Sequence[int] | None = None,
    encounter_ability_ids: Sequence[int] | None = None,
) -> list[FetchSpec]:
    """DEFAULT_SPECS with the filter policy applied."""
    return [
        spec.model_copy(
            update={
                "filter_expression": build_filter(
                    spec.data_type,
                    spec.hostility,
                    registry_spell_ids=registry_spell_ids,
                    encounter_ability_ids=encounter_ability_ids,
                )
            }
        )
        for spec in DEFAULT_SPECS
    ]


def filter_hash(expression: str | None) -> str:
    return hashlib.sha256(expression.encode()).hexdigest()[:12] if expression else "none"


# ---------------------------------------------------------------------------
# Raw page cache
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class RawPageCache:
    """On-disk cache of report metadata and raw event pages."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, code: str, fight: int, spec: FetchSpec) -> Path:
        return self.root / code / str(fight) / spec.dir_name / filter_hash(spec.filter_expression)

    def report_meta(self, code: str) -> dict | None:
        path = self.root / code / "report.json"
        return orjson.loads(path.read_bytes()) if path.exists() else None

    def write_report_meta(self, code: str, data: dict) -> None:
        _atomic_write(self.root / code / "report.json", orjson.dumps(data))

    def pages(self, code: str, fight: int, spec: FetchSpec) -> list[dict]:
        directory = self._dir(code, fight, spec)
        if not directory.exists():
            return []
        return [orjson.loads(p.read_bytes()) for p in sorted(directory.glob("page_*.json"))]

    def write_page(self, code: str, fight: int, spec: FetchSpec, index: int, page: dict) -> None:
        _atomic_write(self._dir(code, fight, spec) / f"page_{index:03d}.json", orjson.dumps(page))

    def is_complete(self, code: str, fight: int, spec: FetchSpec) -> bool:
        return (self._dir(code, fight, spec) / "_complete").exists()

    def mark_complete(self, code: str, fight: int, spec: FetchSpec) -> None:
        _atomic_write(self._dir(code, fight, spec) / "_complete", b"")


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

_semaphore: tuple[asyncio.AbstractEventLoop, int, asyncio.Semaphore] | None = None


def _request_semaphore(limit: int) -> asyncio.Semaphore:
    """Process-wide limit on in-flight WCL requests. Rebuilt if the event loop (or limit) changes."""
    global _semaphore
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore[0] is not loop or _semaphore[1] != limit:
        _semaphore = (loop, limit, asyncio.Semaphore(limit))
    return _semaphore[2]


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


async def _request_page(
    client: WarcraftLogsClient,
    code: str,
    pull: Pull,
    spec: FetchSpec,
    start: int,
    cfg: EncounterConfig,
    max_retries: int,
) -> dict:
    """One events request with rate-limit backoff. Gives up (re-raises) after ``max_retries`` retries."""
    variables: dict[str, Any] = {
        "code": code,
        "fightIDs": [pull.fight_id],
        "startTime": start,
        "endTime": pull.end_ms,
        "dataType": spec.data_type,
        "hostility": spec.hostility,
        "filter": spec.filter_expression,
        "limit": cfg.wcl_page_limit,
    }
    attempt = 0
    while True:
        try:
            async with _request_semaphore(cfg.wcl_max_concurrency):
                data = await client.query(GET_EVENTS_PAGE, variables)
            break
        except WarcraftLogsRateLimitError as exc:
            if attempt >= max_retries:
                raise
            delay = exc.retry_after_s if exc.retry_after_s is not None else float(2**attempt)
            logger.warning(
                "WCL rate limited (%s); retry %d/%d in %.1fs", spec.dir_name, attempt + 1, max_retries, delay
            )
            attempt += 1
            await asyncio.sleep(delay)
    report = (data.get("reportData") or {}).get("report")
    if report is None:
        raise WarcraftLogsAPIError(f"WCL returned no report for code '{code}'.")
    return report["events"]


async def _fetch_spec(
    client: WarcraftLogsClient | None,
    cache: RawPageCache,
    code: str,
    pull: Pull,
    spec: FetchSpec,
    *,
    offline: bool,
    cfg: EncounterConfig,
    max_retries: int,
) -> bool:
    """Fetch (or resume) one data type. Returns True if pagination did not terminate cleanly."""
    if cache.is_complete(code, pull.fight_id, spec):
        return False

    cached = cache.pages(code, pull.fight_id, spec)
    # A cache with a null-terminated final page just lost its marker to a crash.
    if cached and cached[-1].get("nextPageTimestamp") is None:
        cache.mark_complete(code, pull.fight_id, spec)
        return False

    if offline or client is None:
        raise MissingFixtureError(
            f"{pull.pull_key} {spec.dir_name} [{spec.filter_expression}] is not in the raw cache."
        )

    index = len(cached)
    start = int(cached[-1]["nextPageTimestamp"]) if cached else pull.start_ms
    truncated = False
    while True:
        page = await _request_page(client, code, pull, spec, start, cfg, max_retries)
        cache.write_page(code, pull.fight_id, spec, index, page)
        index += 1
        nxt = page.get("nextPageTimestamp")
        if nxt is None:
            break
        if int(nxt) <= start:  # no forward progress; stop rather than loop forever
            truncated = True
            logger.warning("%s %s: nextPageTimestamp did not advance; marking truncated", pull.pull_key, spec.dir_name)
            break
        start = int(nxt)
    cache.mark_complete(code, pull.fight_id, spec)
    return truncated


async def fetch_pull(
    client: WarcraftLogsClient | None,
    cache: RawPageCache,
    code: str,
    pull: Pull,
    specs: list[FetchSpec],
    *,
    offline: bool = False,
    progress: ProgressFn | None = None,
    max_retries: int | None = None,
) -> dict[str, bool]:
    """
    Fetch every spec for one pull into the raw cache.

    Returns ``{dir_name: truncated}``. All specs are attempted even if one fails (so cached progress is
    kept); the first failure is re-raised afterwards.
    """
    cfg = EncounterConfig()
    retries = cfg.wcl_max_retries if max_retries is None else max_retries
    done = 0

    async def one(spec: FetchSpec) -> bool:
        nonlocal done
        result = await _fetch_spec(client, cache, code, pull, spec, offline=offline, cfg=cfg, max_retries=retries)
        done += 1
        if progress:
            progress(pull.pull_key, f"fetched {spec.dir_name}", done / len(specs))
        return result

    outcomes = await asyncio.gather(*(one(s) for s in specs), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return {spec.dir_name: bool(o) for spec, o in zip(specs, outcomes, strict=True)}
