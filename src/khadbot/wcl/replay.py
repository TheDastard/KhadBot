"""
wcl/replay.py

WCL replay, a development switch (``KHADBOT_WCL_REPLAY``): an ``httpx`` transport for ``WarcraftLogsClient`` that
answers every WCL data request from a directory of recorded reports, or refuses it, so ingest, live watch, the budget
and access checks run end to end with no WCL points spent and nothing sent to the network.

Why the transport: every caller (the ingest pipeline, watch polls, the budget's rate-limit read, access checks,
scripts) opens its own ``WarcraftLogsClient``, and the client picks this transport in one place when replay is on.
Everything above it (retries, 401 handling, rate-limit errors, failure codes, the budget, progress) runs as it does
against real WCL; only the bytes on the wire are recorded ones.

How a request is answered (``docs/wcl/replay.md``, "WCL replay"):

- Token requests: the client-credentials grant gets a placeholder app token locally. Every other grant (the sign-in
  code exchange and refresh) goes to real WCL, and so does the current-user query, so real sign-in keeps working.
- ``GetReportMeta``: ``<root>/<code>/report.json`` with KhadBot's own ``khadbot*`` keys removed. For the live report,
  only its recorded fights, revealed one at a time.
- ``ReportReadable``: the recorded report's ``startTime``.
- ``GetEventsPage`` for one fight: ``<root>/<code>/<fight>/<dataType>[-<hostility>]/<filter_hash>/page_NNN.json``.
  ``page_000`` answers a request starting at the fight's ``startTime``; each later page answers the previous page's
  ``nextPageTimestamp``. ``endTime`` must be the fight's ``endTime``.
- ``GetEventsPage`` for several fights: a recorded ``<root>/<code>/batch/<dir>/<filter_hash>/<fights_hash>/`` stream
  the same way, from the earliest fight start; without one the request is refused.
- The search queries (``wcl/search.py``: ``GuildHeader``, ``GuildReports``, ``CharacterReports``):
  the recorded GraphQL body under ``<root>/_search/`` (``search_*_path``), errors included, as WCL gave it. A guild's
  report listing is found by guild ID and answers whatever time window and limit are asked (the caller applies its own
  window too); a character's by region, server and name. Anything nobody recorded is refused.
- ``rateLimitData``: the simulated counter (``ReplayCounter``).
- A code with no recorded report gets a GraphQL error, as WCL answers a missing report (the wording is replay's own).
- Anything else (an unrecorded page or filter, another query, another URL) raises ``WclReplayRefusedError``, which
  names it. Nothing is ever made up.

Every answered request costs 1 point, the measured cost of a one-page request; a ``rateLimitData`` read shows the
count before its own point. With no point free in the window the answer is HTTP 429, as WCL's is. Refusals cost
nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

import httpx

from khadbot.config import WclReplayConfig
from khadbot.errors import ConfigurationError
from khadbot.wcl.client import GRAPHQL_CLIENT_URL, GRAPHQL_USER_URL, RATE_LIMIT_QUERY, TOKEN_URL, WarcraftLogsAPIError
from khadbot.wcl.oauth import CURRENT_USER_QUERY
from khadbot.wcl.search import (
    CHARACTER_OPERATION,
    GUILD_OPERATION,
    GUILD_REPORTS_OPERATION,
    guild_source,
)

logger = logging.getLogger(__name__)

# The operations ingest, watch and the access check send (``encounter/ingest/wcl_queries.py``, ``wcl/reports.py``).
REPORT_META_OPERATION = "GetReportMeta"
EVENTS_PAGE_OPERATION = "GetEventsPage"
REPORT_READABLE_OPERATION = "ReportReadable"

REPLAY_APP_TOKEN = "replay-app-token"  # noqa: S105 - a placeholder, never sent anywhere
POINTS_PER_REQUEST = 1.0  # docs/wcl/costs.md: every request costs at least 1; a one-page request costs about 1

_OPERATION = re.compile(r"^\s*query\s+(\w+)")
_PAGE = re.compile(r"page_\d+\.json")
_LFS_POINTER = b"version https://git-lfs"
_HOSTILITIES = ("Friendlies", "Enemies")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WclReplayRefusedError(WarcraftLogsAPIError):
    """WCL replay has no recorded answer for a request. The message names what was asked; nothing is invented."""


# ---------------------------------------------------------------------------
# Layout (the raw page cache's, ``encounter/ingest/fetch.py``)
# ---------------------------------------------------------------------------


def filter_hash(expression: str | None) -> str:
    """The raw-cache directory of a filter: the first 12 hex of its SHA-256, ``none`` without one."""
    return hashlib.sha256(expression.encode()).hexdigest()[:12] if expression else "none"


def fights_hash(fight_ids: list[int]) -> str:
    """The raw-cache directory of a batch stream's fights."""
    return hashlib.sha256(",".join(str(f) for f in sorted(fight_ids)).encode()).hexdigest()[:12]


def spec_dir_name(data_type: str, hostility: str) -> str:
    return data_type if hostility == "Friendlies" else f"{data_type}-{hostility}"


SEARCH_DIR = "_search"  # not a report code (codes are letters and digits), so report discovery never lists it


def search_guild_path(region: str, server: str, name: str) -> Path:
    """
    Where a guild header's recorded answer lives, relative to the replay root: by region and server slug, and the
    name lower case and percent-encoded (``death%20and%20destruction.json``), so one guild has one file whatever case
    it was typed in.
    """
    return Path(SEARCH_DIR, "guilds", region.lower(), server.lower(), quote(name.casefold(), safe="") + ".json")


def search_guild_reports_path(guild_id: int) -> Path:
    """Where a guild's recorded report listing lives, relative to the replay root: by WCL guild ID."""
    return Path(SEARCH_DIR, "guild_reports", f"{int(guild_id)}.json")


def search_character_path(region: str, server: str, name: str) -> Path:
    """Where a character's recorded answer (with its recent reports) lives, laid out like a guild's."""
    return Path(SEARCH_DIR, "characters", region.lower(), server.lower(), quote(name.casefold(), safe="") + ".json")


SEARCH_OPERATIONS = (
    GUILD_OPERATION,
    GUILD_REPORTS_OPERATION,
    CHARACTER_OPERATION,
)


# ---------------------------------------------------------------------------
# Rate-limit counter
# ---------------------------------------------------------------------------


@dataclass
class ReplayCounter:
    """
    ``rateLimitData`` for replay: ``limit`` points per window of ``window_s`` seconds. The first window starts at the
    first use; a window that has ended starts the next with nothing spent. ``clock`` is in seconds.
    """

    limit: float
    window_s: float
    clock: Callable[[], float] = time.monotonic
    _spent: float = field(default=0.0, init=False)
    _resets_at: float | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _roll(self) -> float:
        now = self.clock()
        if self._resets_at is None:
            self._resets_at = now + self.window_s
        elif now >= self._resets_at:
            windows = math.floor((now - self._resets_at) / self.window_s) + 1
            self._resets_at += windows * self.window_s
            self._spent = 0.0
        return now

    def reading(self) -> dict[str, float | int]:
        """``{limitPerHour, pointsSpentThisHour, pointsResetIn}`` as ``rateLimitData`` reports them."""
        with self._lock:
            now = self._roll()
            assert self._resets_at is not None
            return {
                "limitPerHour": self.limit,
                "pointsSpentThisHour": self._spent,
                "pointsResetIn": max(0, math.ceil(self._resets_at - now)),
            }

    def has_point(self) -> bool:
        """Whether 1 point is free: WCL holds one before running any request."""
        with self._lock:
            self._roll()
            return self.limit - self._spent >= POINTS_PER_REQUEST

    def charge(self, points: float = POINTS_PER_REQUEST) -> None:
        with self._lock:
            self._roll()
            self._spent += points


# ---------------------------------------------------------------------------
# Live report
# ---------------------------------------------------------------------------


@dataclass
class LiveReveal:
    """
    How many of the live report's recorded fights are visible: one more per metadata request (``interval_s`` 0), or
    one at the first request and one more every ``interval_s`` seconds after it.
    """

    interval_s: float
    clock: Callable[[], float] = time.monotonic
    _requests: int = field(default=0, init=False)
    _first: float | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def next_count(self) -> int:
        with self._lock:
            self._requests += 1
            if self.interval_s <= 0:
                return self._requests
            now = self.clock()
            if self._first is None:
                self._first = now
            return 1 + math.floor((now - self._first) / self.interval_s)


# ---------------------------------------------------------------------------
# Recorded reports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Page:
    path: Path
    start: int  # the startTime this page answers
    next: int | None  # its nextPageTimestamp


class RecordedReports:
    """The recorded reports under ``root``, read lazily; each page chain is indexed once."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._meta: dict[str, dict | None] = {}
        self._chains: dict[Path, list[_Page]] = {}
        self._lock = threading.Lock()

    def report(self, code: str) -> dict | None:
        """The recorded ``report.json`` of ``code``, or None when there is none."""
        with self._lock:
            if code not in self._meta:
                self._meta[code] = self._load_meta(code)
            return self._meta[code]

    def _load_meta(self, code: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9]+", code):
            return None
        path = self.root / code / "report.json"
        if not path.is_file():
            return None
        data = path.read_bytes()
        if data.startswith(_LFS_POINTER):
            raise WclReplayRefusedError(
                f"WCL replay: {code}/report.json is a Git LFS pointer; run `git lfs pull` to get the recording."
            )
        return json.loads(data)

    def recorded_fights(self, code: str) -> set[int]:
        directory = self.root / code
        return {int(p.name) for p in directory.iterdir() if p.is_dir() and p.name.isdigit()}

    def page(self, directory: Path, first_start: int, start: int, what: str) -> bytes:
        """The recorded page of the chain in ``directory`` (which begins at ``first_start``) that answers ``start``."""
        chain = self._chain(directory, first_start, what)
        for page in chain:
            if page.start == start:
                data = page.path.read_bytes()
                if data.startswith(_LFS_POINTER):
                    raise WclReplayRefusedError(f"WCL replay: {what} is a Git LFS pointer; run `git lfs pull`.")
                return data
        raise WclReplayRefusedError(f"WCL replay has no recorded page of {what} starting at {start}.")

    def _chain(self, directory: Path, first_start: int, what: str) -> list[_Page]:
        with self._lock:
            if directory in self._chains:
                return self._chains[directory]
        paths = sorted(p for p in directory.iterdir() if _PAGE.fullmatch(p.name)) if directory.is_dir() else []
        if not paths:
            raise WclReplayRefusedError(f"WCL replay has no recorded pages for {what}.")
        chain: list[_Page] = []
        start = first_start
        for path in paths:
            data = path.read_bytes()
            if data.startswith(_LFS_POINTER):
                raise WclReplayRefusedError(f"WCL replay: {what} is a Git LFS pointer; run `git lfs pull`.")
            nxt = json.loads(data).get("nextPageTimestamp")
            chain.append(_Page(path, start, None if nxt is None else int(nxt)))
            if nxt is None:
                break
            start = int(nxt)
        with self._lock:
            self._chains[directory] = chain
        return chain


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


def _missing_report(code: str) -> httpx.Response:
    # WCL answers a missing report with a GraphQL error; its wording is not recorded, so this message is replay's.
    return _json(
        200, {"data": {"reportData": {"report": None}}, "errors": [{"message": f"WCL replay has no report '{code}'."}]}
    )


def _strip_khadbot(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if not k.startswith("khadbot")}


async def _real_wcl(request: httpx.Request) -> httpx.Response:
    """Send ``request`` to real WCL on a transport of its own (sign-in only)."""
    async with httpx.AsyncHTTPTransport() as transport:
        response = await transport.handle_async_request(request)
        raw = b"".join([chunk async for chunk in response.aiter_raw()])
        await response.aclose()
    return httpx.Response(response.status_code, headers=response.headers, content=raw)


Passthrough = Callable[[httpx.Request], Any]


class ReplayTransport(httpx.AsyncBaseTransport):
    """
    Answers WCL requests from the recorded reports under ``root`` (see the module docstring).

    Args:
        root: The directory of recorded reports.
        limit_per_hour: The simulated counter's points per window.
        reset_s: The window's length in seconds.
        live_report: A recorded report whose recorded fights appear one at a time; empty for none.
        live_interval_s: 0 reveals one more fight per metadata request; else one more every so many seconds.
        clock: Seconds, for the counter and the live reveal; tests inject one.
        passthrough: Sends a sign-in request to real WCL; tests inject one.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        limit_per_hour: float = 3600.0,
        reset_s: float = 3600.0,
        live_report: str = "",
        live_interval_s: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        passthrough: Passthrough = _real_wcl,
    ) -> None:
        self.reports = RecordedReports(Path(root))
        self.counter = ReplayCounter(limit_per_hour, reset_s, clock)
        self.live_report = live_report
        self.live = LiveReveal(live_interval_s, clock)
        self._passthrough = passthrough

    async def aclose(self) -> None:
        """Nothing to close: every client of the process shares this transport, and it holds no connection."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url.copy_with(query=None))
        if request.method == "POST" and url == TOKEN_URL:
            await request.aread()
            grant = parse_qs(request.content.decode()).get("grant_type", [""])[0]
            if grant == "client_credentials":
                return _json(200, {"access_token": REPLAY_APP_TOKEN, "token_type": "Bearer", "expires_in": 3600})
            return await self._passthrough(request)  # the sign-in code exchange and the refresh grant
        if request.method == "POST" and url in (GRAPHQL_CLIENT_URL, GRAPHQL_USER_URL):
            await request.aread()
            body = json.loads(request.content or b"{}")
            query = str(body.get("query") or "")
            if query == CURRENT_USER_QUERY:
                return await self._passthrough(request)
            if not self.counter.has_point():
                return httpx.Response(429)
            response = self._graphql(query, body.get("variables") or {})
            self.counter.charge()
            return response
        raise WclReplayRefusedError(f"WCL replay refuses a {request.method} request to {url}.")

    # -- GraphQL ---------------------------------------------------------------

    def _graphql(self, query: str, variables: dict[str, Any]) -> httpx.Response:
        if query == RATE_LIMIT_QUERY:
            return _json(200, {"data": {"rateLimitData": self.counter.reading()}})
        match = _OPERATION.match(query)
        operation = match.group(1) if match else None
        if operation is not None and operation in SEARCH_OPERATIONS:
            return self._search(operation, variables)
        if operation not in (REPORT_META_OPERATION, REPORT_READABLE_OPERATION, EVENTS_PAGE_OPERATION):
            shown = operation or " ".join(query.split())[:60]
            raise WclReplayRefusedError(f"WCL replay has no answer for the query '{shown}'.")
        code = str(variables.get("code") or "")
        meta = self.reports.report(code)
        if meta is None:
            return _missing_report(code)
        if operation == REPORT_READABLE_OPERATION:
            return _json(200, {"data": {"reportData": {"report": {"startTime": meta.get("startTime")}}}})
        if operation == REPORT_META_OPERATION:
            return _json(200, {"data": {"reportData": {"report": self._meta(code, meta)}}})
        page = self._events(code, meta, variables)
        return httpx.Response(
            200,
            content=b'{"data":{"reportData":{"report":{"events":' + page + b"}}}}",
            headers={"Content-Type": "application/json"},
        )

    def _search(self, operation: str, variables: dict[str, Any]) -> httpx.Response:
        """A recorded search answer: the whole GraphQL body WCL gave, without KhadBot's own keys."""
        if operation == GUILD_REPORTS_OPERATION:
            guild_id = variables.get("guildID")
            if not isinstance(guild_id, int):
                raise WclReplayRefusedError(f"WCL replay needs a guildID on a guild listing; got {guild_id!r}.")
            relative = search_guild_reports_path(guild_id)
            what = f"guild {guild_id}'s reports"
        else:
            try:
                source = guild_source(
                    str(variables.get("serverRegion") or ""),
                    str(variables.get("serverSlug") or ""),
                    str(variables.get("name") or ""),
                )
            except ValueError:
                raise WclReplayRefusedError(f"WCL replay can't read the name in {variables!r}.") from None
            kind = "guild" if operation == GUILD_OPERATION else "character"
            path_of = search_guild_path if operation == GUILD_OPERATION else search_character_path
            relative = path_of(source.region, source.server, source.name)
            what = f"the {kind} {source.name!r} on {source.region}/{source.server}"
        path = self.reports.root / relative
        if not path.is_file():
            raise WclReplayRefusedError(f"WCL replay has no recording of {what} ({relative.as_posix()}).")
        data = path.read_bytes()
        if data.startswith(_LFS_POINTER):
            raise WclReplayRefusedError(f"WCL replay: {relative.as_posix()} is a Git LFS pointer; run `git lfs pull`.")
        return _json(200, _strip_khadbot(json.loads(data)))

    def _meta(self, code: str, meta: dict) -> dict:
        meta = _strip_khadbot(meta)
        if code != self.live_report:
            return meta
        recorded = self.reports.recorded_fights(code)
        fights = sorted((f for f in meta.get("fights") or [] if f.get("id") in recorded), key=lambda f: f["startTime"])
        shown = fights[: self.live.next_count()]
        logger.info("WCL replay: live report %s shows %d of %d recorded fights", code, len(shown), len(fights))
        return {**meta, "fights": shown}

    def _events(self, code: str, meta: dict, variables: dict[str, Any]) -> bytes:
        data_type = str(variables.get("dataType") or "")
        hostility = str(variables.get("hostility") or "")
        expression = variables.get("filter")
        fight_ids = variables.get("fightIDs") or []
        if hostility not in _HOSTILITIES or not data_type or not fight_ids:
            raise WclReplayRefusedError(
                f"WCL replay needs dataType, hostility and fightIDs on an events request; got {data_type!r}, "
                f"{hostility!r}, {fight_ids!r}."
            )
        fights = {f["id"]: f for f in meta.get("fights") or []}
        ids = sorted({int(f) for f in fight_ids})
        unknown = [f for f in ids if f not in fights]
        if unknown:
            raise WclReplayRefusedError(f"WCL replay: {code} has no fight {unknown[0]} in its recorded report.json.")
        dir_name = spec_dir_name(data_type, hostility)
        fh = filter_hash(expression)
        spec = f"{dir_name} filter {fh} ({expression or 'no filter'})"
        start, end = int(variables.get("startTime", -1)), int(variables.get("endTime", -1))
        if len(ids) == 1:
            fight = fights[ids[0]]
            what = f"{code}:{ids[0]} {spec}"
            directory = self.reports.root / code / str(ids[0]) / dir_name / fh
            first, last = int(fight["startTime"]), int(fight["endTime"])
        else:
            what = f"{code} fights {ids} {spec} (a multi-fight request; there is no recorded batch stream)"
            directory = self.reports.root / code / "batch" / dir_name / fh / fights_hash(ids)
            first = min(int(fights[f]["startTime"]) for f in ids)
            last = max(int(fights[f]["endTime"]) for f in ids)
        if end != last:
            raise WclReplayRefusedError(f"WCL replay recorded {what} up to {last}, not {end}.")
        return self.reports.page(directory, first, start, what)


# ---------------------------------------------------------------------------
# The configured transport
# ---------------------------------------------------------------------------

_configured: tuple[WclReplayConfig, ReplayTransport] | None = None
_configured_lock = threading.Lock()


def describe(cfg: WclReplayConfig) -> str:
    """One line for the start-up log: where replay answers from and how it counts."""
    live = f"; live report {cfg.live_report}" if cfg.live_report else ""
    interval = (
        f", one more fight every {cfg.live_interval_s:g} s" if cfg.live_report and cfg.live_interval_s > 0 else ""
    )
    per = ", one more fight per metadata request" if cfg.live_report and cfg.live_interval_s <= 0 else ""
    return (
        f"WCL replay is on: WCL data requests are answered from {cfg.directory} or refused, never sent to WCL "
        f"(sign-in still goes to WCL); {cfg.limit_per_hour:g} points per {cfg.reset_s:g} s{live}{interval}{per}"
    )


def check_config(cfg: WclReplayConfig) -> None:
    """
    Raises:
        ConfigurationError: Replay is on and its settings can't work (no such directory, no window, a live report
            that is not recorded there).
    """
    root = Path(cfg.directory)
    if not root.is_dir():
        raise ConfigurationError(f"KHADBOT_WCL_REPLAY is not a directory: {cfg.directory}")
    if cfg.reset_s <= 0 or cfg.limit_per_hour < 0:
        raise ConfigurationError("KHADBOT_WCL_REPLAY_RESET_S must be above 0 and KHADBOT_WCL_REPLAY_LIMIT at least 0")
    if cfg.live_report and not (root / cfg.live_report / "report.json").is_file():
        raise ConfigurationError(f"KHADBOT_WCL_REPLAY_LIVE names no recorded report in {cfg.directory}")


def configured_transport() -> ReplayTransport | None:
    """
    The process's replay transport when ``KHADBOT_WCL_REPLAY`` is set, else None (real WCL). Built once per
    configuration, so every client in the process shares one counter and one live reveal.

    Raises:
        ConfigurationError: Replay is on and its settings can't work (``check_config``).
    """
    global _configured
    cfg = WclReplayConfig()
    if not cfg.enabled:
        return None
    with _configured_lock:
        if _configured is None or _configured[0] != cfg:
            check_config(cfg)
            transport = ReplayTransport(
                cfg.directory,
                limit_per_hour=cfg.limit_per_hour,
                reset_s=cfg.reset_s,
                live_report=cfg.live_report,
                live_interval_s=cfg.live_interval_s,
            )
            _configured = (cfg, transport)
            logger.warning(describe(cfg))
        return _configured[1]


def reset_configured_transport() -> None:
    """Forget the process's replay transport (its counter and live reveal). Tests only."""
    global _configured
    with _configured_lock:
        _configured = None


__all__ = [
    "EVENTS_PAGE_OPERATION",
    "LiveReveal",
    "POINTS_PER_REQUEST",
    "REPLAY_APP_TOKEN",
    "REPORT_META_OPERATION",
    "REPORT_READABLE_OPERATION",
    "RecordedReports",
    "ReplayCounter",
    "ReplayTransport",
    "WclReplayRefusedError",
    "check_config",
    "configured_transport",
    "describe",
    "filter_hash",
    "fights_hash",
    "reset_configured_transport",
    "SEARCH_OPERATIONS",
    "search_character_path",
    "search_guild_path",
    "search_guild_reports_path",
    "spec_dir_name",
]
