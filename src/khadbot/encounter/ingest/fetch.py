"""
encounter/ingest/fetch.py

Raw WCL page cache and the paginated fetcher.

Every page WCL returns is written to the blob store as it arrives, so an interrupted ingest
resumes from the last cached page and a finished one never touches the network again.
Keys, under ``raw/`` (``$KHADBOT_DATA_DIR/raw`` in the local store)::

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
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import orjson

from khadbot.config import EncounterConfig
from khadbot.encounter.ingest.wcl_queries import GET_EVENTS_PAGE
from khadbot.encounter.models import FetchSpec, Pull
from khadbot.storage import BlobStore, join_key, resolve_blobs
from khadbot.tools.warcraftlogs._client import (
    WarcraftLogsAPIError,
    WarcraftLogsClient,
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
                data = await query_wcl(client, GET_EVENTS_PAGE, variables, token_provider)
            break
        except WarcraftLogsRateLimitError as exc:
            if attempt >= max_retries:
                raise
            delay = exc.retry_after_s if exc.retry_after_s is not None else float(2**attempt)
            logger.warning("WCL rate limited (%s); retry %d/%d in %.1fs", spec.key, attempt + 1, max_retries, delay)
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
