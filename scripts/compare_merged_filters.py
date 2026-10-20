"""
scripts/compare_merged_filters.py

Measure whether merging WCL fetch requests saves points or time, and whether the merged rows normalize to the
same event frame as today's fetch.

On one recorded pull (default ``trMgApZCVYkv3jdq`` fight 26, 526 s) it fetches:

  baseline   the current specs (``specs_for``), through the real ``fetch_pull``;
  hostility  one request per hostility, no ``dataType``, one combined ``filterExpression`` each;
  datatype   one request per data type and hostility, the Buffs filters of both cooldown files merged.

For each it reports points spent (``rateLimitData`` before and after, app token), requests, pages, bytes, events
and wall time. Each merged variant's rows are assigned back to the baseline specs by row identity and run
through the real ``normalize_pull``; the script reports whether the frames are identical to the baseline's.

A variant that sends an argument combination the API may refuse (no ``dataType``) first sends a one-second,
small-limit probe request, so a rejection costs about one request, and the rejection message is reported.

This script SPENDS WCL POINTS. It prints an estimate before each live step and waits for you to type ``yes``.
It never prints a token. Needs WARCRAFTLOGS_CLIENT_ID / WARCRAFTLOGS_CLIENT_SECRET (read from the environment or
the nearest ``.env``). Report metadata comes from the recorded fixture, so it costs nothing.

    uv run python scripts/compare_merged_filters.py [--code trMgApZCVYkv3jdq] [--fight 26] [--variants hostility,datatype]
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import orjson
import polars as pl
from dotenv import load_dotenv

from khadbot.config import EncounterConfig, get_config
from khadbot.encounter.ingest.fetch import RawPageCache, fetch_pull, specs_for
from khadbot.encounter.ingest.normalize import normalize_pull, parse_report_meta
from khadbot.encounter.models import Actor, FetchSpec, Pull
from khadbot.encounter.store import EVENT_SCHEMA
from khadbot.tools.warcraftlogs._client import (
    WarcraftLogsAPIError,
    WarcraftLogsClient,
    WarcraftLogsRateLimitError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "encounter" / "wcl"
DEFAULT_CODE = "trMgApZCVYkv3jdq"
DEFAULT_FIGHT = 26

# The follow-up's bar for building a merged fetch: at least this share of points or wall time saved.
SAVING_THRESHOLD_PCT = 15.0

# docs/wcl-api.md "Measured cost": a request costs about 2 points plus about 1 per extra page.
POINTS_PER_REQUEST = 2
POINTS_PER_EXTRA_PAGE = 1

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

# Same arguments as encounter/ingest/wcl_queries.GET_EVENTS_PAGE, but ``$dataType`` is nullable so a request can
# leave it out. Whether the API accepts that is what the probe finds out. Defined here, not in the ingest
# package, because it is a measurement and ships no behaviour.
EVENTS_PAGE_OPTIONAL_TYPE = """
query GetEventsPageMerged(
  $code: String!
  $fightIDs: [Int]!
  $startTime: Float!
  $endTime: Float!
  $dataType: EventDataType
  $hostility: HostilityType
  $filter: String
  $limit: Int
) {
  reportData {
    report(code: $code) {
      events(
        fightIDs: $fightIDs
        startTime: $startTime
        endTime: $endTime
        dataType: $dataType
        hostilityType: $hostility
        filterExpression: $filter
        includeResources: true
        limit: $limit
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""


class QueryClient(Protocol):
    """What the script needs from a WCL client (the real one, or a stub in tests)."""

    async def query(self, gql: str, variables: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]: ...

    async def rate_limit(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Requests and metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stream:
    """One paginated request stream: the arguments that stay fixed while ``startTime`` advances."""

    label: str
    data_type: str | None
    hostility: str
    filter_expression: str | None
    # Keys of the baseline specs this stream stands in for. A row is only assigned to one of them, so a row that
    # two streams both return (a cast that is also in Threat) is counted once per stream, not twice per spec.
    covers: tuple[str, ...] | None = None


@dataclass
class Variant:
    name: str
    description: str
    streams: list[Stream]
    # True when the argument combination may be refused by the API (so a cheap probe runs first).
    uncertain: bool = False


@dataclass
class Metrics:
    requests: int = 0  # request streams
    pages: int = 0  # HTTP requests (one per page)
    bytes: int = 0  # compact JSON size of the pages, measured the same way for every run
    events: int = 0
    seconds: float = 0.0
    points: float | None = None  # rateLimitData delta; None when it could not be read


@dataclass
class StreamResult:
    pages: list[dict]
    truncated: bool = False  # nextPageTimestamp stopped advancing
    aborted: bool = False  # page cap reached


@dataclass
class FrameComparison:
    identical: bool
    identical_ignoring_tie_order: bool
    rows_baseline: int
    rows_variant: int
    note: str = ""


@dataclass
class VariantResult:
    name: str
    status: str  # "ok" | "rejected" | "aborted" | "declined"
    detail: str = ""
    metrics: Metrics | None = None
    comparison: FrameComparison | None = None
    extra_rows: int = 0  # merged rows no baseline spec contains
    missing_rows: int = 0  # baseline rows the merged stream lacks
    ambiguous_rows: int = 0  # merged rows several baseline specs contain


def count_pages(pages: Sequence[dict]) -> Metrics:
    """Pages, compact-JSON bytes and events of a page set, measured the same way for baseline and variants."""
    return Metrics(
        pages=len(pages),
        bytes=sum(len(orjson.dumps(p)) for p in pages),
        events=sum(len(p.get("data") or []) for p in pages),
    )


# ---------------------------------------------------------------------------
# Building variants
# ---------------------------------------------------------------------------

_IDS_FILTER = re.compile(r"^ability\.id IN \(([0-9,]+)\)$")


def merge_filters(expressions: Sequence[str | None]) -> str | None:
    """
    One filter that returns what all of ``expressions`` return. ``None`` (unfiltered) absorbs the rest.

    Only the ``ability.id IN (...)`` form KhadBot uses is understood, and the IDs are unioned. Anything else
    raises ``ValueError`` rather than guessing at WCL's expression syntax.
    """
    if not expressions or any(e is None for e in expressions):
        return None
    ids: set[int] = set()
    for expr in expressions:
        assert expr is not None
        match = _IDS_FILTER.match(expr)
        if match is None:
            raise ValueError(f"cannot merge filter expression: {expr!r}")
        ids.update(int(i) for i in match.group(1).split(",") if i)
    return f"ability.id IN ({','.join(str(i) for i in sorted(ids))})"


def _keys(specs: Sequence[FetchSpec], hostility: str) -> tuple[str, ...]:
    return tuple(s.key for s in specs if s.hostility == hostility)


def build_variants(
    specs: Sequence[FetchSpec], *, friendly_filter: str | None = None, enemy_filter: str | None = None
) -> dict[str, Variant]:
    """
    The merged variants for a baseline spec list.

    ``hostility``: one request per hostility with no ``dataType``. Friendlies uses ``friendly_filter`` (default:
    unfiltered, because most friendly specs are unfiltered) and Enemies ``enemy_filter`` (default: the enemy
    Casts spec's filter). ``datatype``: one request per (data type, hostility), each with the specs' filters
    merged; today that merges the two Buffs requests.
    """
    if enemy_filter is None:
        enemy_filter = next((s.filter_expression for s in specs if s.hostility == "Enemies"), None)
    hostility = Variant(
        name="hostility",
        description="one request per hostility, no dataType, one combined filterExpression each",
        streams=[
            Stream("Friendlies/all", None, "Friendlies", friendly_filter, _keys(specs, "Friendlies")),
            Stream("Enemies/all", None, "Enemies", enemy_filter, _keys(specs, "Enemies")),
        ],
        uncertain=True,
    )
    groups: dict[tuple[str, str], list[FetchSpec]] = {}
    for spec in specs:
        groups.setdefault((spec.data_type, spec.hostility), []).append(spec)
    datatype = Variant(
        name="datatype",
        description="one request per data type and hostility, filters of the same data type merged",
        streams=[
            Stream(
                f"{dt}/{host}",
                dt,
                host,
                merge_filters([s.filter_expression for s in group]),
                tuple(s.key for s in group),
            )
            for (dt, host), group in groups.items()
        ],
    )
    return {v.name: v for v in (hostility, datatype)}


# ---------------------------------------------------------------------------
# Fetching a stream
# ---------------------------------------------------------------------------


def _variables(code: str, pull: Pull, stream: Stream, start: int, end: int, limit: int) -> dict[str, Any]:
    return {
        "code": code,
        "fightIDs": [pull.fight_id],
        "startTime": start,
        "endTime": end,
        "dataType": stream.data_type,
        "hostility": stream.hostility,
        "filter": stream.filter_expression,
        "limit": limit,
    }


async def _one_request(
    client: QueryClient,
    code: str,
    variables: dict[str, Any],
    *,
    max_retries: int,
    sleep: Callable[[float], Awaitable[Any]],
) -> dict:
    attempt = 0
    while True:
        try:
            data = await client.query(EVENTS_PAGE_OPTIONAL_TYPE, variables)
            break
        except WarcraftLogsRateLimitError as exc:
            if attempt >= max_retries:
                raise
            await sleep(exc.retry_after_s if exc.retry_after_s is not None else float(2**attempt))
            attempt += 1
    report = (data.get("reportData") or {}).get("report")
    if report is None:
        raise WarcraftLogsAPIError(f"WCL returned no report for code '{code}'.")
    return report["events"]


async def fetch_stream(
    client: QueryClient,
    code: str,
    pull: Pull,
    stream: Stream,
    *,
    page_limit: int,
    max_pages: int,
    max_retries: int = 3,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> StreamResult:
    """Follow ``nextPageTimestamp`` to the end, stopping at ``max_pages`` (reported as ``aborted``)."""
    pages: list[dict] = []
    start = pull.start_ms
    while True:
        variables = _variables(code, pull, stream, start, pull.end_ms, page_limit)
        page = await _one_request(client, code, variables, max_retries=max_retries, sleep=sleep)
        pages.append(page)
        nxt = page.get("nextPageTimestamp")
        if nxt is None:
            return StreamResult(pages)
        if int(nxt) <= start:
            return StreamResult(pages, truncated=True)
        if len(pages) >= max_pages:
            return StreamResult(pages, aborted=True)
        start = int(nxt)


async def probe_stream(
    client: QueryClient, code: str, pull: Pull, stream: Stream, *, sleep: Callable[[float], Awaitable[Any]]
) -> str | None:
    """
    One tiny request (first second of the pull, 5 events) with the stream's arguments.

    Returns None when the API accepts them, else the API's error message. A rate limit is not a rejection and
    propagates.
    """
    variables = _variables(code, pull, stream, pull.start_ms, min(pull.end_ms, pull.start_ms + 1_000), 5)
    try:
        await _one_request(client, code, variables, max_retries=3, sleep=sleep)
    except WarcraftLogsRateLimitError:
        raise
    except WarcraftLogsAPIError as exc:
        return str(exc)
    return None


# ---------------------------------------------------------------------------
# Assigning merged rows back to specs, and comparing frames
# ---------------------------------------------------------------------------


def _row_id(event: dict) -> bytes:
    return orjson.dumps(event, option=orjson.OPT_SORT_KEYS)


@dataclass
class Assignment:
    pages_by_key: dict[str, list[dict]]
    extra: int = 0
    missing: int = 0
    ambiguous: int = 0


def assign_events(
    streams: Sequence[tuple[Sequence[str] | None, Sequence[dict]]], baseline_by_key: dict[str, list[dict]]
) -> Assignment:
    """
    Split merged rows into per-spec page sets using the baseline as the key.

    ``streams`` is ``(covers, pages)`` per request stream; ``covers`` limits the specs its rows can go to (None:
    any). A merged row goes to every covered baseline spec that holds an identical row (compared as sorted-key
    JSON, with a per-spec count so duplicates stay duplicates). A row no covered spec holds is ``extra``;
    baseline rows no stream produced are ``missing``; a row several covered specs hold (a cast also in Threat)
    is ``ambiguous``: its data type cannot be told from the row itself. Each spec's rows keep stream order.
    """
    remaining = {
        k: Counter(_row_id(e) for p in pages for e in p.get("data") or []) for k, pages in baseline_by_key.items()
    }
    out: dict[str, list[dict]] = {k: [] for k in baseline_by_key}
    result = Assignment({})
    for covers, pages in streams:
        allowed = [k for k in remaining if covers is None or k in covers]
        for page in pages:
            for event in page.get("data") or []:
                rid = _row_id(event)
                owners = [k for k in allowed if remaining[k][rid] > 0]
                if not owners:
                    result.extra += 1
                    continue
                if len(owners) > 1:
                    result.ambiguous += 1
                for k in owners:
                    remaining[k][rid] -= 1
                    out[k].append(event)
    result.missing = sum(sum(c.values()) for c in remaining.values())
    result.pages_by_key = {k: [{"data": events, "nextPageTimestamp": None}] for k, events in out.items()}
    return result


def compare_frames(baseline: pl.DataFrame, variant: pl.DataFrame) -> FrameComparison:
    """
    Frame equality for the variant check. ``identical`` is exact (including ``seq``, so tie order);
    ``identical_ignoring_tie_order`` sorts both on every column but ``seq`` first.
    """
    columns = [c for c in EVENT_SCHEMA if c != "seq"]
    exact = baseline.equals(variant)
    relaxed = baseline.select(columns).sort(columns).equals(variant.select(columns).sort(columns))
    note = ""
    if not relaxed:
        note = f"{baseline.height} baseline rows, {variant.height} variant rows"
    elif not exact:
        note = "same rows; order differs between rows with equal timestamps"
    return FrameComparison(exact, relaxed, baseline.height, variant.height, note)


# ---------------------------------------------------------------------------
# Savings and estimates
# ---------------------------------------------------------------------------


def saving_pct(baseline: float | None, variant: float | None) -> float | None:
    """Percent saved by the variant relative to the baseline; None when either is unknown or the baseline is 0."""
    if baseline is None or variant is None or baseline <= 0:
        return None
    return (baseline - variant) / baseline * 100


def meets_threshold(points_saving: float | None, time_saving: float | None) -> bool:
    """True when points or wall time are saved by at least ``SAVING_THRESHOLD_PCT``."""
    return any(s is not None and s >= SAVING_THRESHOLD_PCT for s in (points_saving, time_saving))


def stream_cost_ceiling(streams: int, max_pages: int) -> int:
    """Most points ``streams`` request streams can spend under the page cap, by the documented cost model."""
    return streams * (POINTS_PER_REQUEST + POINTS_PER_EXTRA_PAGE * (max_pages - 1))


def baseline_estimate(n_specs: int) -> list[str]:
    return [
        f"Baseline: {n_specs} requests (the current specs), fetched concurrently.",
        "  Documented: about 14 points per pull on average (docs/wcl-api.md, 2026-10-11, 25-pull night: 348 points).",
        "  This pull is longer than average; Healing alone cost 10 points on it (9 pages) in that run.",
        "  Expect roughly 14 to 30 points. The upper end is not documented.",
    ]


def variant_estimate(variant: Variant, max_pages: int) -> list[str]:
    ceiling = stream_cost_ceiling(len(variant.streams), max_pages)
    lines = [
        f"Variant '{variant.name}': {variant.description}.",
        f"  {len(variant.streams)} request streams: " + ", ".join(s.label for s in variant.streams),
    ]
    if variant.uncertain:
        lines.append("  First a 1-second probe per stream (about 2 points each) to see whether the API accepts it.")
        lines.append("  A stream with no dataType may return every event type, so it can cost more than the baseline.")
    lines.append(
        f"  Ceiling under --max-pages {max_pages}: {ceiling} points (documented cost model: "
        f"{POINTS_PER_REQUEST} per request + {POINTS_PER_EXTRA_PAGE} per extra page). Expected: unknown."
    )
    return lines


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def ask(question: str) -> bool:
    """Wait for a typed ``yes``. No input available (a closed pipe) counts as no."""
    try:
        answer = input(f"{question} Type 'yes' to run: ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


async def read_points(client: QueryClient) -> float | None:
    try:
        return float((await client.rate_limit())["pointsSpentThisHour"])
    except Exception:  # noqa: BLE001 - a failed reading must not fail the measurement
        return None


async def _measured(client: QueryClient, run: Callable[[], Awaitable[Any]]) -> tuple[Any, float, float | None]:
    before = await read_points(client)
    t0 = time.perf_counter()
    result = await run()
    seconds = time.perf_counter() - t0
    after = await read_points(client)
    return result, seconds, (after - before) if before is not None and after is not None else None


@dataclass
class Comparison:
    code: str
    fight: int
    baseline: Metrics | None = None
    read_overhead: float | None = None
    variants: list[VariantResult] = field(default_factory=list)


async def run_comparison(
    client: QueryClient,
    code: str,
    pull: Pull,
    actors: list[Actor],
    specs: list[FetchSpec],
    variants: Sequence[Variant],
    *,
    confirm: Callable[[str], bool] = ask,
    out: Callable[[str], None] = print,
    max_pages: int = 60,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> Comparison:
    """Fetch the baseline, then each variant, asking before every live step."""
    result = Comparison(code, pull.fight_id)
    page_limit = EncounterConfig().wcl_page_limit

    out("\n".join(baseline_estimate(len(specs))))
    if not confirm("Run the baseline?"):
        out("Stopped before the baseline.")
        return result

    first = await read_points(client)
    second = await read_points(client)
    if first is not None and second is not None:
        result.read_overhead = second - first
        out(f"Two back-to-back rateLimitData reads moved the counter by {result.read_overhead:g} points.")

    with tempfile.TemporaryDirectory(prefix="khadbot-merged-") as tmp:
        cache = RawPageCache(Path(tmp))
        _, seconds, points = await _measured(client, lambda: fetch_pull(client, cache, code, pull, specs))  # type: ignore[arg-type]
        baseline_by_key = {s.key: cache.pages(code, pull.fight_id, s) for s in specs}
        all_pages = [p for pages in baseline_by_key.values() for p in pages]
        metrics = count_pages(all_pages)
        metrics.requests, metrics.seconds, metrics.points = len(specs), seconds, points
        result.baseline = metrics
        baseline_frame = normalize_pull(pull, actors, baseline_by_key)
        out(f"Baseline: {metrics.pages} pages, {metrics.events} events, {points} points, {seconds:.1f} s.")

        for variant in variants:
            out("\n" + "\n".join(variant_estimate(variant, max_pages)))
            if not confirm(f"Run variant '{variant.name}'?"):
                result.variants.append(VariantResult(variant.name, "declined"))
                continue
            result.variants.append(
                await _run_variant(
                    client, code, pull, actors, variant, baseline_by_key, baseline_frame,
                    page_limit=page_limit, max_pages=max_pages, sleep=sleep, out=out,
                )
            )  # fmt: skip
    return result


async def _run_variant(
    client: QueryClient,
    code: str,
    pull: Pull,
    actors: list[Actor],
    variant: Variant,
    baseline_by_key: dict[str, list[dict]],
    baseline_frame: pl.DataFrame,
    *,
    page_limit: int,
    max_pages: int,
    sleep: Callable[[float], Awaitable[Any]],
    out: Callable[[str], None],
) -> VariantResult:
    if variant.uncertain:
        for stream in variant.streams:
            rejection = await probe_stream(client, code, pull, stream, sleep=sleep)
            if rejection is not None:
                out(f"  REJECTED on stream {stream.label}: {rejection}")
                return VariantResult(variant.name, "rejected", f"{stream.label}: {rejection}")

    async def run() -> list[StreamResult]:
        return list(
            await asyncio.gather(
                *(
                    fetch_stream(client, code, pull, s, page_limit=page_limit, max_pages=max_pages, sleep=sleep)
                    for s in variant.streams
                )
            )
        )

    results, seconds, points = await _measured(client, run)
    pages = [p for r in results for p in r.pages]
    metrics = count_pages(pages)
    metrics.requests, metrics.seconds, metrics.points = len(variant.streams), seconds, points
    if any(r.aborted for r in results):
        out(f"  ABORTED: a stream reached the page cap ({max_pages}); frames not compared.")
        return VariantResult(variant.name, "aborted", f"page cap {max_pages} reached", metrics)
    notes = "; ".join(
        f"{s.label} stopped advancing" for s, r in zip(variant.streams, results, strict=True) if r.truncated
    )

    assignment = assign_events(
        [(st.covers, r.pages) for st, r in zip(variant.streams, results, strict=True)], baseline_by_key
    )
    frame = normalize_pull(pull, actors, assignment.pages_by_key)
    comparison = compare_frames(baseline_frame, frame)
    out(
        f"  {metrics.pages} pages, {metrics.events} events, {points} points, {seconds:.1f} s; frames identical: {comparison.identical}"
    )
    return VariantResult(
        variant.name, "ok", notes, metrics, comparison, assignment.extra, assignment.missing, assignment.ambiguous
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _fmt(value: float | None, spec: str = "g") -> str:
    return "unknown" if value is None else format(value, spec)


def render_report(result: Comparison, today: date | None = None) -> str:
    """The markdown block to paste into docs/wcl-api.md."""
    base = result.baseline
    lines = [
        f"### Merged-filter measurement ({(today or date.today()).isoformat()}, `{result.code}:{result.fight}`)",
        "",
        "| Run | Requests | Pages | Bytes | Events | Wall s | Points | Points saved | Time saved | Frames identical |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    if base is None:
        return "\n".join(lines[:1] + ["", "No baseline was fetched."])
    lines.append(
        f"| baseline | {base.requests} | {base.pages} | {base.bytes} | {base.events} | {base.seconds:.1f} | "
        f"{_fmt(base.points)} | | | |"
    )
    for v in result.variants:
        if v.metrics is None or v.status != "ok":
            lines.append(f"| {v.name} | {v.status}: {v.detail or '-'} | | | | | | | | |")
            continue
        m = v.metrics
        ps, ts = saving_pct(base.points, m.points), saving_pct(base.seconds, m.seconds)
        c = v.comparison
        same = "unknown" if c is None else f"{c.identical} (ignoring tie order: {c.identical_ignoring_tie_order})"
        lines.append(
            f"| {v.name} | {m.requests} | {m.pages} | {m.bytes} | {m.events} | {m.seconds:.1f} | {_fmt(m.points)} | "
            f"{_fmt(ps, '.0f')}% | {_fmt(ts, '.0f')}% | {same} |"
        )
    lines.append("")
    if result.read_overhead is not None:
        lines.append(f"A back-to-back `rateLimitData` read moved the counter by {result.read_overhead:g} points.")
    for v in result.variants:
        if v.status == "ok" and v.metrics is not None:
            ps, ts = saving_pct(base.points, v.metrics.points), saving_pct(base.seconds, v.metrics.seconds)
            verdict = "meets" if meets_threshold(ps, ts) else "does not meet"
            lines.append(
                f"- `{v.name}` {verdict} the {SAVING_THRESHOLD_PCT:g}% bar; rows no baseline spec holds: {v.extra_rows}, "
                f"baseline rows missing: {v.missing_rows}, rows several specs hold: {v.ambiguous_rows}."
                + (f" {v.comparison.note}." if v.comparison and v.comparison.note else "")
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _load_pull(root: Path, code: str, fight: int) -> tuple[Pull, list[Actor]]:
    meta = RawPageCache(root).report_meta(code)
    if meta is None:
        raise SystemExit(f"No report.json for {code} under {root} (git lfs pull?).")
    pulls, actors = parse_report_meta(code, meta)
    for pull in pulls:
        if pull.fight_id == fight:
            return pull, actors
    raise SystemExit(f"Fight {fight} is not in {code}'s report metadata.")


async def _amain(args: argparse.Namespace) -> int:
    from khadbot.encounter.commands import default_filter_kwargs

    pull, actors = _load_pull(args.fixture_root, args.code, args.fight)
    kwargs = default_filter_kwargs()
    specs = specs_for(
        kwargs["defensive_spell_ids"],
        (kwargs["enemy_cast_ids"] or {}).get(pull.encounter_id),
        kwargs["offensive_spell_ids"],
    )
    available = build_variants(specs, friendly_filter=args.friendly_filter, enemy_filter=args.enemy_filter)
    names = [n.strip() for n in args.variants.split(",") if n.strip()]
    unknown = [n for n in names if n not in available]
    if unknown:
        raise SystemExit(f"Unknown variant(s) {unknown}; choose from {sorted(available)}.")

    print(f"Pull {pull.pull_key}: {pull.duration_ms / 1000:.0f} s, {len(specs)} baseline requests.")
    wcl = get_config().warcraftlogs
    async with WarcraftLogsClient(wcl.client_id, wcl.client_secret) as client:
        result = await run_comparison(
            client, args.code, pull, actors, specs, [available[n] for n in names], max_pages=args.max_pages
        )
    print("\n" + render_report(result))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", default=DEFAULT_CODE)
    parser.add_argument("--fight", type=int, default=DEFAULT_FIGHT)
    parser.add_argument(
        "--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT, help="Directory holding <code>/report.json"
    )
    parser.add_argument("--variants", default="hostility,datatype", help="Comma-separated: hostility, datatype")
    parser.add_argument(
        "--friendly-filter", help="filterExpression for the Friendlies stream of 'hostility' (default: none)"
    )
    parser.add_argument(
        "--enemy-filter",
        help="filterExpression for the Enemies stream of 'hostility' (default: the enemy Casts filter)",
    )
    parser.add_argument("--max-pages", type=int, default=60, help="Abort a stream after this many pages (cost cap)")
    args = parser.parse_args(argv)
    load_dotenv()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
