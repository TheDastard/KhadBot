"""
encounter/ingest/fetch.py

Raw WCL page cache and the paginated fetcher.

Every page WCL returns is written to the blob store as it arrives, so an interrupted ingest
resumes from the last cached page and a finished one never touches the network again.
Keys, under ``raw/`` (``$KHADBOT_DATA_DIR/raw`` in the local store)::

    <report>/report.json
    <report>/<fight>/<dir_name>/<filter_hash>/page_000.json ...
    <report>/<fight>/<dir_name>/<filter_hash>/_complete
    <report>/batch/<dir_name>/<filter_hash>/<fights_hash>/page_000.json ...
    <report>/batch/<dir_name>/<filter_hash>/<fights_hash>/_complete
    <report>/batch/<dir_name>/<filter_hash>/<fights_hash>/stalled

``filter_hash`` is the first 12 hex of SHA-256 of the filter expression (``"none"`` when
there is none), so changing a filter refetches only that data type.

Batching: the small specs (``BATCHED_SPEC_KEYS``, about one page per pull) are fetched by ``fetch_batches`` in
one paginated stream across several pulls, because the events query takes a list of fight IDs and every row
names its ``fight``. The stream is cached under ``batch/`` (``fights_hash`` names the set of fights), split
by ``fight`` into each pull's own entry above, and deleted once every pull of the batch is complete, so
normalize and offline ingest only ever see the per-fight layout. A stream that stops advancing marks truncated
only the pulls ending after the stall (``stalled`` holds its timestamp). A failed batch is logged and left to
the per-pull fetch, which also handles pulls with partly cached pages.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import orjson

from khadbot.config import EncounterConfig
from khadbot.encounter.ingest.wcl_queries import GET_EVENTS_PAGE
from khadbot.encounter.models import FetchSpec, Pull
from khadbot.storage import BlobStore, join_key, resolve_blobs
from khadbot.wcl.client import (
    WarcraftLogsAPIError,
    WarcraftLogsClient,
    WarcraftLogsNetworkError,
    WarcraftLogsRateLimitError,
    WarcraftLogsUnauthorizedError,
)

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, str, float], None]  # (pull_key, stage, pct)

# Returns a user's WCL access token, refreshing it if needed. ``None`` in place of a provider means the app token.
TokenProvider = Callable[[], Awaitable[str]]

# Key added to the cached report metadata: which kind of token fetched it. Never records which user.
FETCHED_WITH_KEY = "khadbotFetchedWith"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MissingFixtureError(Exception):
    """Raised in offline mode when a page set is not in the raw cache."""


class UserTokenRejectedError(WarcraftLogsAPIError):
    """Raised when WCL rejects a user token as unauthorized even after the provider was asked for a fresh one."""


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

# Specs fetched in one request across pulls when several pulls need them (``fetch_batches``): each is about one page
# per pull. Healing, DamageTaken and friendly Casts run to many pages per pull and stay per pull.
BATCHED_SPEC_KEYS = frozenset(
    {"Deaths", "Buffs", "Buffs:offensive", "Debuffs", "Dispels", "Interrupts", "Threat", "Casts-Enemies"}
)


def _ids_filter(ids: Sequence[int]) -> str | None:
    unique = sorted(set(ids))
    return f"ability.id IN ({','.join(str(i) for i in unique)})" if unique else None


def build_filter(
    data_type: str,
    hostility: str = "Friendlies",
    *,
    buff_spell_ids: Sequence[int] | None = None,
    encounter_ability_ids: Sequence[int] | None = None,
) -> str | None:
    """
    Default filter policy per data type (to be tuned against measured WCL costs).

    Buffs: only the given cooldown spell IDs. Enemy Casts: only the encounter file's ability IDs.
    Everything else is unfiltered. With no IDs available a filter is omitted, not emptied.
    """
    if data_type == "Buffs" and hostility == "Friendlies":
        return _ids_filter(buff_spell_ids or [])
    if data_type == "Casts" and hostility == "Enemies":
        return _ids_filter(encounter_ability_ids or [])
    return None


def specs_for(
    defensive_spell_ids: Sequence[int] | None = None,
    encounter_ability_ids: Sequence[int] | None = None,
    offensive_spell_ids: Sequence[int] | None = None,
) -> list[FetchSpec]:
    """
    DEFAULT_SPECS with the filter policy applied, plus a second Buffs request for offensive cooldowns.

    Each cooldown file gets its own Buffs filter, so editing one refetches only its own pages. The offensive
    request is left out when it has no IDs: an omitted filter would fetch every buff.
    """
    specs = [
        spec.model_copy(
            update={
                "filter_expression": build_filter(
                    spec.data_type,
                    spec.hostility,
                    buff_spell_ids=defensive_spell_ids,
                    encounter_ability_ids=encounter_ability_ids,
                )
            }
        )
        for spec in DEFAULT_SPECS
    ]
    if offensive := build_filter("Buffs", buff_spell_ids=offensive_spell_ids):
        specs.append(FetchSpec(data_type="Buffs", filter_expression=offensive, part="offensive"))
    return specs


def filter_hash(expression: str | None) -> str:
    return hashlib.sha256(expression.encode()).hexdigest()[:12] if expression else "none"


# ---------------------------------------------------------------------------
# Raw page cache
# ---------------------------------------------------------------------------


# The page objects directly inside a spec's directory; the path-based cache matched them with ``page_*.json``.
_PAGE_NAME = re.compile(r"page_[^/]*\.json")


class RawPageCache:
    """
    Report metadata and raw event pages in a ``BlobStore``, under the ``raw/`` prefix by default.

    ``blobs`` may also be a directory laid out like the cache itself (a recorded fixture, or ``<data_dir>/raw``);
    see ``storage.resolve_blobs``.
    """

    def __init__(self, blobs: BlobStore | str | os.PathLike[str], prefix: str | None = None) -> None:
        self.blobs, self.prefix = resolve_blobs(blobs, prefix, "raw")

    def _dir(self, code: str, fight: int, spec: FetchSpec) -> str:
        return join_key(self.prefix, code, str(fight), spec.dir_name, filter_hash(spec.filter_expression))

    def report_meta(self, code: str) -> dict | None:
        data = self.blobs.get(join_key(self.prefix, code, "report.json"))
        return orjson.loads(data) if data is not None else None

    def write_report_meta(self, code: str, data: dict) -> None:
        self.blobs.put(join_key(self.prefix, code, "report.json"), orjson.dumps(data))

    def fetched_with(self, code: str) -> str | None:
        """``"user"`` or ``"app"``: the token kind that fetched the cached metadata. ``None`` if unrecorded."""
        value = (self.report_meta(code) or {}).get(FETCHED_WITH_KEY)
        return value if value in ("user", "app") else None

    def pages(self, code: str, fight: int, spec: FetchSpec) -> list[dict]:
        directory = self._dir(code, fight, spec) + "/"
        keys = [k for k in self.blobs.list(directory) if _PAGE_NAME.fullmatch(k[len(directory) :])]
        return [orjson.loads(data) for k in keys if (data := self.blobs.get(k)) is not None]

    def write_page(self, code: str, fight: int, spec: FetchSpec, index: int, page: dict) -> None:
        self.blobs.put(f"{self._dir(code, fight, spec)}/page_{index:03d}.json", orjson.dumps(page))

    def is_complete(self, code: str, fight: int, spec: FetchSpec) -> bool:
        return self.blobs.exists(f"{self._dir(code, fight, spec)}/_complete")

    def mark_complete(self, code: str, fight: int, spec: FetchSpec) -> None:
        self.blobs.put(f"{self._dir(code, fight, spec)}/_complete", b"")

    def replace_pages(self, code: str, fight: int, spec: FetchSpec, pages: list[dict]) -> None:
        """Replace a fight's cached pages for ``spec`` with ``pages``. Does not write ``_complete``."""
        directory = self._dir(code, fight, spec) + "/"
        for key in self.blobs.list(directory):
            if _PAGE_NAME.fullmatch(key[len(directory) :]):
                self.blobs.delete(key)
        for index, page in enumerate(pages):
            self.write_page(code, fight, spec, index, page)

    # Batch streams: one paginated request series for several fights, split into the per-fight entries later.

    def _batch_dir(self, code: str, spec: FetchSpec, fight_ids: Sequence[int]) -> str:
        fights_hash = hashlib.sha256(",".join(str(f) for f in sorted(fight_ids)).encode()).hexdigest()[:12]
        return join_key(self.prefix, code, "batch", spec.dir_name, filter_hash(spec.filter_expression), fights_hash)

    def batch_pages(self, code: str, spec: FetchSpec, fight_ids: Sequence[int]) -> list[dict]:
        directory = self._batch_dir(code, spec, fight_ids) + "/"
        keys = [k for k in self.blobs.list(directory) if _PAGE_NAME.fullmatch(k[len(directory) :])]
        return [orjson.loads(data) for k in keys if (data := self.blobs.get(k)) is not None]

    def write_batch_page(self, code: str, spec: FetchSpec, fight_ids: Sequence[int], index: int, page: dict) -> None:
        self.blobs.put(f"{self._batch_dir(code, spec, fight_ids)}/page_{index:03d}.json", orjson.dumps(page))

    def is_batch_complete(self, code: str, spec: FetchSpec, fight_ids: Sequence[int]) -> bool:
        return self.blobs.exists(f"{self._batch_dir(code, spec, fight_ids)}/_complete")

    def mark_batch_complete(self, code: str, spec: FetchSpec, fight_ids: Sequence[int]) -> None:
        self.blobs.put(f"{self._batch_dir(code, spec, fight_ids)}/_complete", b"")

    def mark_batch_stalled(self, code: str, spec: FetchSpec, fight_ids: Sequence[int], at_ms: int) -> None:
        """Record the timestamp at which the batch stream stopped advancing."""
        self.blobs.put(f"{self._batch_dir(code, spec, fight_ids)}/stalled", str(at_ms).encode("ascii"))

    def batch_stalled_at(self, code: str, spec: FetchSpec, fight_ids: Sequence[int]) -> int | None:
        data = self.blobs.get(f"{self._batch_dir(code, spec, fight_ids)}/stalled")
        return int(data.decode("ascii")) if data else None

    def delete_batch(self, code: str, spec: FetchSpec, fight_ids: Sequence[int]) -> None:
        """Delete the batch stream: its pages, completion marker and stall record."""
        for key in self.blobs.list(self._batch_dir(code, spec, fight_ids) + "/"):
            self.blobs.delete(key)


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


async def query_wcl(
    client: WarcraftLogsClient,
    gql: str,
    variables: dict[str, Any],
    token_provider: TokenProvider | None = None,
) -> dict[str, Any]:
    """
    One WCL query, on the app token or (with ``token_provider``) the signed-in user's.

    The provider is asked for the token on every request, so it can refresh an expired one. If WCL answers 401,
    it is asked once more and the request is retried once; a second 401 raises ``UserTokenRejectedError``.
    """
    if token_provider is None:
        return await client.query(gql, variables)
    try:
        return await client.query(gql, variables, user_token=await token_provider())
    except WarcraftLogsUnauthorizedError:
        logger.info("WCL rejected the user token; asking for a fresh one and retrying once")
    try:
        return await client.query(gql, variables, user_token=await token_provider())
    except WarcraftLogsUnauthorizedError as exc:
        raise UserTokenRejectedError("WCL rejected the user's access token after a refresh.") from exc


async def _request_events(
    client: WarcraftLogsClient,
    code: str,
    variables: dict[str, Any],
    label: str,
    cfg: EncounterConfig,
    max_retries: int,
    token_provider: TokenProvider | None = None,
) -> dict:
    """One events request with backoff on rate limits and network errors. Re-raises after ``max_retries`` retries."""
    attempt = 0
    while True:
        try:
            async with _request_semaphore(cfg.wcl_max_concurrency):
                data = await query_wcl(client, GET_EVENTS_PAGE, variables, token_provider)
            break
        except (WarcraftLogsRateLimitError, WarcraftLogsNetworkError) as exc:
            if attempt >= max_retries:
                raise
            if isinstance(exc, WarcraftLogsRateLimitError):
                kind = "rate limited"
                delay = exc.retry_after_s if exc.retry_after_s is not None else float(2**attempt)
            else:
                kind = "network error"
                delay = float(2**attempt)
            logger.warning("WCL %s (%s); retry %d/%d in %.1fs", kind, label, attempt + 1, max_retries, delay)
            attempt += 1
            await asyncio.sleep(delay)
    report = (data.get("reportData") or {}).get("report")
    if report is None:
        raise WarcraftLogsAPIError(f"WCL returned no report for code '{code}'.")
    return report["events"]


async def _request_page(
    client: WarcraftLogsClient,
    code: str,
    pull: Pull,
    spec: FetchSpec,
    start: int,
    cfg: EncounterConfig,
    max_retries: int,
    token_provider: TokenProvider | None = None,
) -> dict:
    """One events page of one pull."""
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
    return await _request_events(
        client, code, variables, f"{pull.pull_key} {spec.key}", cfg, max_retries, token_provider
    )


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
    token_provider: TokenProvider | None = None,
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
        raise MissingFixtureError(f"{pull.pull_key} {spec.key} [{spec.filter_expression}] is not in the raw cache.")

    index = len(cached)
    start = int(cached[-1]["nextPageTimestamp"]) if cached else pull.start_ms
    truncated = False
    while True:
        page = await _request_page(client, code, pull, spec, start, cfg, max_retries, token_provider)
        cache.write_page(code, pull.fight_id, spec, index, page)
        index += 1
        nxt = page.get("nextPageTimestamp")
        if nxt is None:
            break
        if int(nxt) <= start:  # no forward progress; stop rather than loop forever
            truncated = True
            logger.warning("%s %s: nextPageTimestamp did not advance; marking truncated", pull.pull_key, spec.key)
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
    token_provider: TokenProvider | None = None,
) -> dict[str, bool]:
    """
    Fetch every spec for one pull into the raw cache.

    Returns ``{spec.key: truncated}``. All specs are attempted even if one fails (so cached progress is
    kept); the first failure is re-raised afterwards. ``token_provider`` makes every request use a user token
    (see ``query_wcl``); without it they use the app token.
    """
    cfg = EncounterConfig()
    retries = cfg.wcl_max_retries if max_retries is None else max_retries
    done = 0

    async def one(spec: FetchSpec) -> bool:
        nonlocal done
        result = await _fetch_spec(
            client,
            cache,
            code,
            pull,
            spec,
            offline=offline,
            cfg=cfg,
            max_retries=retries,
            token_provider=token_provider,
        )
        done += 1
        if progress:
            progress(pull.pull_key, f"fetched {spec.key}", done / len(specs))
        return result

    outcomes = await asyncio.gather(*(one(s) for s in specs), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return {spec.key: bool(o) for spec, o in zip(specs, outcomes, strict=True)}


# ---------------------------------------------------------------------------
# Batched fetch
# ---------------------------------------------------------------------------


async def fetch_batch(
    client: WarcraftLogsClient,
    cache: RawPageCache,
    code: str,
    pulls: Sequence[Pull],
    spec: FetchSpec,
    *,
    cfg: EncounterConfig,
    max_retries: int,
    token_provider: TokenProvider | None = None,
) -> dict[int, bool]:
    """
    Fetch one spec for several pulls in one paginated stream and split it into each pull's cache entry.

    The stream resumes from its last cached page. The per-fight entries are written last (marker after
    pages), so a crash at any point is repaired by the next call. Returns ``{fight_id: truncated}``.
    """
    fights = sorted(p.fight_id for p in pulls)
    stalled_at: int | None = None

    if not cache.is_batch_complete(code, spec, fights):
        cached = cache.batch_pages(code, spec, fights)
        if cached and cached[-1].get("nextPageTimestamp") is None:
            cache.mark_batch_complete(code, spec, fights)  # a crash lost the marker
        else:
            index = len(cached)
            start = int(cached[-1]["nextPageTimestamp"]) if cached else min(p.start_ms for p in pulls)
            end = max(p.end_ms for p in pulls)
            label = f"{code} batch {spec.key} fights {fights[0]}-{fights[-1]}"
            while True:
                variables: dict[str, Any] = {
                    "code": code,
                    "fightIDs": fights,
                    "startTime": start,
                    "endTime": end,
                    "dataType": spec.data_type,
                    "hostility": spec.hostility,
                    "filter": spec.filter_expression,
                    "limit": cfg.wcl_page_limit,
                }
                page = await _request_events(client, code, variables, label, cfg, max_retries, token_provider)
                cache.write_batch_page(code, spec, fights, index, page)
                index += 1
                nxt = page.get("nextPageTimestamp")
                if nxt is None:
                    break
                if int(nxt) <= start:  # no forward progress; stop rather than loop forever
                    stalled_at = start
                    logger.warning("%s: nextPageTimestamp did not advance at %d; marking truncated", label, start)
                    break
                start = int(nxt)
            if stalled_at is not None:
                cache.mark_batch_stalled(code, spec, fights, stalled_at)
            cache.mark_batch_complete(code, spec, fights)

    if stalled_at is None:
        stalled_at = cache.batch_stalled_at(code, spec, fights)

    rows_by_fight: dict[int, list[dict]] = {f: [] for f in fights}
    stray = 0
    for page in cache.batch_pages(code, spec, fights):
        for row in page.get("data") or []:
            fight = row.get("fight") if isinstance(row, dict) else None
            if fight in rows_by_fight:
                rows_by_fight[fight].append(row)
            else:
                stray += 1
    if stray:
        logger.warning("%s batch %s: dropped %d rows with a missing or unrequested fight", code, spec.key, stray)

    for pull in pulls:
        if not cache.is_complete(code, pull.fight_id, spec):
            cache.replace_pages(
                code, pull.fight_id, spec, [{"data": rows_by_fight[pull.fight_id], "nextPageTimestamp": None}]
            )
            cache.mark_complete(code, pull.fight_id, spec)
    cache.delete_batch(code, spec, fights)
    return {p.fight_id: stalled_at is not None and p.end_ms > stalled_at for p in pulls}


async def fetch_batches(
    client: WarcraftLogsClient,
    cache: RawPageCache,
    code: str,
    pulls: Sequence[Pull],
    specs_by_fight: Mapping[int, Sequence[FetchSpec]],
    *,
    max_retries: int | None = None,
    token_provider: TokenProvider | None = None,
    cfg: EncounterConfig | None = None,
) -> dict[int, set[str]]:
    """
    Fetch the batchable specs several pulls still need, one stream per spec, filter and chunk.

    Returns ``{fight_id: {truncated spec keys}}``. A failed batch is logged and left for the per-pull fetch.
    """
    cfg = cfg or EncounterConfig()
    retries = cfg.wcl_max_retries if max_retries is None else max_retries

    groups: dict[tuple[str, str | None], tuple[FetchSpec, list[Pull]]] = {}
    for pull in pulls:
        for spec in specs_by_fight.get(pull.fight_id, ()):
            if spec.key not in BATCHED_SPEC_KEYS:
                continue
            if cache.is_complete(code, pull.fight_id, spec) or cache.pages(code, pull.fight_id, spec):
                continue  # done, or partly fetched: the per-pull fetch resumes it
            groups.setdefault((spec.key, spec.filter_expression), (spec, []))[1].append(pull)

    jobs: list[tuple[FetchSpec, list[Pull]]] = []
    size = max(1, cfg.wcl_batch_max_fights)
    for spec, group in groups.values():
        ordered = sorted(group, key=lambda p: p.fight_id)
        for i in range(0, len(ordered), size):
            chunk = ordered[i : i + size]
            if len(chunk) >= 2:
                jobs.append((spec, chunk))

    outcomes = await asyncio.gather(
        *(
            fetch_batch(client, cache, code, chunk, spec, cfg=cfg, max_retries=retries, token_provider=token_provider)
            for spec, chunk in jobs
        ),
        return_exceptions=True,
    )
    truncated: dict[int, set[str]] = {}
    for (spec, chunk), outcome in zip(jobs, outcomes, strict=True):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, BaseException):
            fights = [p.fight_id for p in chunk]
            logger.warning(
                "Batched fetch of %s for fights %s failed; falling back per pull: %s", spec.key, fights, outcome
            )
            continue
        for fight, was_truncated in outcome.items():
            if was_truncated:
                truncated.setdefault(fight, set()).add(spec.key)
    return truncated
