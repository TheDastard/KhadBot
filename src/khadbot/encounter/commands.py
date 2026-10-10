"""
encounter/commands.py

CLI command handlers for encounter review. Each handler takes the text after the command
and returns markdown to render (or None). They are registered with ``run_cli(commands=...)``.
"""

from __future__ import annotations

import argparse
import shlex
from typing import NoReturn

from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.models import IngestResult

# ---------------------------------------------------------------------------
# /ingest
# ---------------------------------------------------------------------------

INGEST_USAGE = "Usage: /ingest <report-code> [--fights 3,4] [--offline]"


class UsageError(Exception):
    """Bad command arguments; the message is safe to show the user."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{message}\n\n{INGEST_USAGE}")


def parse_fights(text: str | None) -> list[int] | None:
    """Parse ``"3,4"`` into ``[3, 4]``; None/empty means all fights."""
    if not text:
        return None
    try:
        return [int(part) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise UsageError(f"Invalid --fights value '{text}': expected comma-separated integers.") from exc


def ingest_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="/ingest", add_help=False)
    parser.add_argument("code")
    parser.add_argument("--fights")
    parser.add_argument("--offline", action="store_true")
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


def _console_progress(pull_key: str, stage: str, pct: float) -> None:
    from khadbot.cli.console import console

    console.print(f"[ui.muted]{pull_key}  {stage}  {pct:.0%}[/ui.muted]")


async def ingest_command(arg: str) -> str:
    """Ingest a WarcraftLogs report into the local event store."""
    try:
        ns = ingest_parser().parse_args(shlex.split(arg))
        fights = parse_fights(ns.fights)
    except UsageError as exc:
        return f"**{exc}**"
    result = await ingest_report(ns.code, fights, offline=ns.offline, progress=_console_progress)
    return format_ingest_result(result)
