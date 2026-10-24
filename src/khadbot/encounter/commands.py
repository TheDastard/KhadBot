"""
encounter/commands.py

CLI command handlers for encounter review. Each handler takes the text after the command
and returns markdown to render (or None). They are registered with ``run_cli(commands=...)``.

``/digest`` and ``/pulls`` default to the report most recently ingested in this session.
"""

from __future__ import annotations

import argparse
import shlex
from typing import NoReturn
from uuid import uuid4

from khadbot.encounter.analyst import ReviewEstimate, estimate_review_cost, review
from khadbot.encounter.digest import build_digest, default_annotation_cache, render_digest
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import FindingsStore, default_findings_store
from khadbot.encounter.ingest import default_store
from khadbot.encounter.models import (
    AnalysisReport,
    AnalysisRequest,
    IngestResult,
    Pull,
    PullDigest,
    UnregisteredAbility,
)
from khadbot.encounter.store import EventStore
from khadbot.errors import ConfigurationError
from khadbot.jobs import (
    IngestReport,
    JobContext,
    LineProgress,
    ProcessPull,
    Progress,
    run_ingest_report,
    run_process_pull,
)
from khadbot.jobs import (
    default_filter_kwargs as default_filter_kwargs,  # re-exported for the eval harness
)

# Report code of the last successful /ingest in this process; /digest and /pulls default to it.
_last_report: str | None = None

# ---------------------------------------------------------------------------
# /ingest
# ---------------------------------------------------------------------------

INGEST_USAGE = "Usage: /ingest <report-code> [--fights 3,4] [--offline | --refresh-meta]"
REFRESH_OFFLINE_ERROR = "--refresh-meta refetches report metadata from WarcraftLogs and cannot be used with --offline."


class UsageError(Exception):
    """Bad command arguments; the message is safe to show the user."""


class _Parser(argparse.ArgumentParser):
    def __init__(self, usage_text: str, **kwargs) -> None:
        super().__init__(add_help=False, **kwargs)
        self.usage_text = usage_text

    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{message}\n\n{self.usage_text}")


def parse_fights(text: str | None) -> list[int] | None:
    """Parse ``"3,4"`` into ``[3, 4]``; None/empty means all fights."""
    if not text:
        return None
    try:
        return [int(part) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise UsageError(f"Invalid --fights value '{text}': expected comma-separated integers.") from exc


def ingest_parser() -> argparse.ArgumentParser:
    parser = _Parser(INGEST_USAGE, prog="/ingest")
    parser.add_argument("code")
    parser.add_argument("--fights")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-meta", action="store_true")
    return parser


def format_ingest_result(result: IngestResult) -> str:
    """Markdown summary of an IngestResult."""

    def fights(keys) -> str:
        return ", ".join(k.split(":")[-1] for k in keys) or "-"

    lines = [
        f"### Ingest `{result.report_code}`",
        "",
        "| Outcome | Fights |",
        "|---|---|",
        f"| Ingested | {fights(result.pulls)} |",
        f"| Skipped (unchanged) | {fights(result.skipped)} |",
        f"| Failed | {fights(result.failed)} |",
    ]
    if result.points_spent is not None:
        lines += ["", f"WCL points spent: {result.points_spent:.1f}"]
    if result.failed:
        lines += ["", "**Failures**", ""] + [f"- `{k}`: {v}" for k, v in result.failed.items()]
    return "\n".join(lines)


def console_progress() -> Progress:
    """Job progress printed as muted ``<pull>  <stage>  <percent>`` lines on the CLI console."""
    from khadbot.cli.console import console

    return LineProgress(lambda line: console.print(f"[ui.muted]{line}[/ui.muted]"))


async def ingest_command(arg: str) -> str:
    """Ingest a WarcraftLogs report into the local event store."""
    try:
        ns = ingest_parser().parse_args(shlex.split(arg))
        fights = parse_fights(ns.fights)
        if ns.refresh_meta and ns.offline:
            raise UsageError(f"{REFRESH_OFFLINE_ERROR}\n\n{INGEST_USAGE}")
    except UsageError as exc:
        return f"**{exc}**"
    spec = IngestReport(code=ns.code, fights=fights, offline=ns.offline, refresh_meta=ns.refresh_meta)
    result = await run_ingest_report(spec, JobContext(), console_progress())
    global _last_report
    if result.pulls or result.skipped:
        _last_report = result.report_code
    return format_ingest_result(result)


# ---------------------------------------------------------------------------
# /digest and /pulls
# ---------------------------------------------------------------------------

DIGEST_USAGE = "Usage: /digest [<report-code>] <fight> [--json] [--unregistered]"
PULLS_USAGE = "Usage: /pulls [<report-code>]"
UNREGISTERED_LIMIT = 20


def digest_parser() -> argparse.ArgumentParser:
    parser = _Parser(DIGEST_USAGE, prog="/digest")
    parser.add_argument("target", nargs="+", help="[<report-code>] <fight>")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--unregistered", action="store_true")
    return parser


def resolve_pull_key(target: list[str], last_report: str | None) -> str:
    """``["CODE", "3"]`` or ``["3"]`` (with a last-ingested report) -> ``"CODE:3"``."""
    if len(target) > 2:
        raise UsageError(DIGEST_USAGE)
    code = target[0] if len(target) == 2 else last_report
    if code is None:
        raise UsageError(f"No report ingested in this session; pass a report code.\n\n{DIGEST_USAGE}")
    try:
        fight = int(target[-1])
    except ValueError as exc:
        raise UsageError(f"Invalid fight '{target[-1]}': expected an integer.\n\n{DIGEST_USAGE}") from exc
    return f"{code}:{fight}"


def _ability_table(rows: list[UnregisteredAbility], empty: str) -> list[str]:
    if not rows:
        return [empty]
    lines = ["| Ability | Name | Damage | Share |", "|---|---|---|---|"]
    return lines + [f"| {u.ability_id} | {u.name or '?'} | {u.damage:,} | {u.share_pct:.1f}% |" for u in rows]


def format_unregistered(
    pull_key: str, rows: list[UnregisteredAbility], unclassified: list[UnregisteredAbility] | None = None
) -> str:
    """Abilities missing from the encounter file, then friendly-source abilities missing from the friendly-damage
    registry: the authoring lists for both files."""
    lines = [f"### Unregistered damage `{pull_key}`", "", *_ability_table(rows, "No unregistered damage.")]
    lines += ["", f"### Unclassified friendly damage `{pull_key}`", ""]
    lines += _ability_table(unclassified or [], "No unclassified friendly damage.")
    return "\n".join(lines)


def format_digest(d: PullDigest, *, as_json: bool = False) -> str:
    if as_json:
        return f"```json\n{d.model_dump_json(indent=2)}\n```"
    return f"```text\n{render_digest(d)}\n```"


async def pull_digest(pull_key: str, *, unregistered: bool = False, store: EventStore | None = None) -> PullDigest:
    """
    The digest of one ingested pull, from the local store. The ordinary digest comes from the ``ProcessPull`` job,
    run silently and without a records store, so a digest command writes only what it always wrote (the annotation
    cache). ``unregistered`` widens the authoring lists past what the job's records hold, so it builds directly.
    Raises ``KeyError`` for a pull that is not ingested.
    """
    store = store or default_store()
    cache = default_annotation_cache()
    if unregistered:
        return build_digest(pull_key, store, cache=cache, unregistered_cap=UNREGISTERED_LIMIT)
    data = EncounterData(store, cache=cache)
    await run_process_pull(ProcessPull(pull_key=pull_key), JobContext(data=data))
    return data.digest(pull_key)


async def run_digest(
    pull_key: str, *, as_json: bool = False, unregistered: bool = False, store: EventStore | None = None
) -> str:
    """Build a digest from the local store and format it as markdown."""
    d = await pull_digest(pull_key, unregistered=unregistered, store=store)
    if unregistered:
        return format_unregistered(pull_key, d.unregistered, d.friendly_damage.unclassified)
    return format_digest(d, as_json=as_json)


async def digest_command(arg: str) -> str:
    """Show the deterministic digest for one ingested pull."""
    try:
        ns = digest_parser().parse_args(shlex.split(arg))
        pull_key = resolve_pull_key(ns.target, _last_report)
        return await run_digest(pull_key, as_json=ns.json, unregistered=ns.unregistered)
    except UsageError as exc:
        return f"**{exc}**"
    except KeyError as exc:
        return f"**{exc.args[0]}**"
    except ConfigurationError as exc:
        return f"**Configuration error:** {exc}"


def format_pulls(code: str, pulls: list[Pull]) -> str:
    if not pulls:
        return f"**No pulls stored for `{code}`. Run /ingest {code} first.**"
    lines = [
        f"### Pulls `{code}`",
        "",
        "| Fight | Encounter | Outcome | Duration | Fight % | Boss HP % | Phase reached |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in pulls:
        pct = f"{p.boss_pct:.1f}" if p.boss_pct is not None else "-"
        hp = f"{p.boss_hp_pct:.1f}" if p.boss_hp_pct is not None else "-"
        outcome = "kill" if p.kill else "wipe"
        phase = p.wcl_phase_reached() or "-"
        lines.append(
            f"| {p.fight_id} | {p.encounter_name} | {outcome} | {p.duration_ms / 1000:.1f}s | {pct} | {hp} | {phase} |"
        )
    return "\n".join(lines)


async def pulls_command(arg: str, *, store: EventStore | None = None) -> str:
    """List the pulls stored for a report."""
    parts = shlex.split(arg)
    code = parts[0] if parts else _last_report
    if code is None:
        return f"**No report ingested in this session; pass a report code.**\n\n{PULLS_USAGE}"
    if len(parts) > 1:
        return f"**{PULLS_USAGE}**"
    return format_pulls(code, (store or default_store()).pulls(code))


# ---------------------------------------------------------------------------
# /review
# ---------------------------------------------------------------------------

REVIEW_USAGE = (
    "Usage: /review [--report <code>] [--fights 3,4] [--depth quick|standard|deep] [--estimate] [question...]"
)
DEFAULT_REVIEW_QUESTION = "What went wrong across these pulls, and what repeated?"


def review_parser() -> argparse.ArgumentParser:
    parser = _Parser(REVIEW_USAGE, prog="/review")
    parser.add_argument("--report")
    parser.add_argument("--fights")
    parser.add_argument("--depth", choices=["quick", "standard", "deep"], default="standard")
    parser.add_argument("--estimate", action="store_true")
    parser.add_argument("words", nargs="*")
    return parser


def format_estimate(estimate: ReviewEstimate) -> str:
    return (
        f"Estimated cost for {estimate.pulls} pulls: about {estimate.total:,} tokens "
        f"({estimate.digest_tokens:,} digest in, {estimate.output_tokens:,} out; "
        "upper bound, pulls already reviewed are free)."
    )


def format_review(report: AnalysisReport) -> str:
    lines = ["### Review", ""]
    lines += [f"- **{f.confidence}** {f.claim} (`{'`, `'.join(f.refs)}`)" for f in report.findings] or ["No findings."]
    if report.unknowns:
        lines += ["", "**Unknown:**", *(f"- {u}" for u in report.unknowns)]
    if report.rejected:
        lines += ["", f"{len(report.rejected)} finding(s) failed verification and were dropped."]
    if report.partial:
        lines += ["", "Some analysis hit its step budget; the result may be incomplete."]
    return "\n".join(lines)


async def review_command(arg: str, *, data: EncounterData | None = None, findings: FindingsStore | None = None) -> str:
    """Review ingested pulls with the analyst and answer a question about them."""
    try:
        ns = review_parser().parse_args(shlex.split(arg))
        fights = parse_fights(ns.fights)
        words = list(ns.words)
        code = ns.report or _last_report
        if code is None:
            raise UsageError("No report ingested in this session; pass a report code.")
    except UsageError as exc:
        return f"**{exc}**"
    data = data or EncounterData.from_config()
    pulls = [p.pull_key for p in data.store.pulls(code) if fights is None or p.fight_id in fights]
    if not pulls:
        return f"No pulls stored for `{code}`."
    estimate = format_estimate(estimate_review_cost(pulls, data))
    if ns.estimate:
        return estimate
    from khadbot.cli.console import console

    console.print(f"[ui.muted]{estimate}[/ui.muted]")
    # Records are built (and stored) pull by pull, with progress, before the analyst reads them.
    ctx, progress = JobContext(data=data), console_progress()
    for key in pulls:
        await run_process_pull(ProcessPull(pull_key=key), ctx, progress)
    req = AnalysisRequest(
        request_id=uuid4().hex, question=" ".join(words) or DEFAULT_REVIEW_QUESTION, pulls=pulls, depth=ns.depth
    )
    return format_review(await review(req, store=data, findings=findings or default_findings_store()))
