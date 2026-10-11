"""
main.py

Entry point for KhadBot. Dispatches to a subcommand; ``cli`` (the interactive
REPL) is the default.

Run: python main.py [cli | ingest <code> | digest <code> <fight>]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from langchain_core.messages import AIMessage, HumanMessage

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _run_cli() -> None:
    from khadbot.agent import ask_coach, build_agent_executor
    from khadbot.cli import run_cli

    executor = build_agent_executor(verbose=True)
    chat_history: list = []

    async def agent_fn(question: str, callbacks: list) -> str:
        """Bridge between the Rich CLI and ask_coach."""
        result = await ask_coach(executor, question, chat_history, callbacks=callbacks)

        chat_history.append(HumanMessage(content=question))
        chat_history.append(AIMessage(content=result["answer"]))

        return result["answer"]

    from khadbot.encounter.commands import digest_command, ingest_command, pulls_command, review_command

    commands = {
        "/ingest": ingest_command,
        "/digest": digest_command,
        "/pulls": pulls_command,
        "/review": review_command,
    }
    asyncio.run(run_cli(agent_fn, commands=commands))


def _run_ingest(code: str, fights: str | None, offline: bool) -> None:
    from rich.console import Console
    from rich.markdown import Markdown

    from khadbot.encounter.commands import default_filter_kwargs, format_ingest_result, parse_fights
    from khadbot.encounter.ingest import ingest_report

    console = Console()

    def progress(pull_key: str, stage: str, pct: float) -> None:
        console.print(f"[dim]{pull_key}  {stage}  {pct:.0%}[/dim]")

    result = asyncio.run(
        ingest_report(code, parse_fights(fights), offline=offline, progress=progress, **default_filter_kwargs())
    )
    console.print(Markdown(format_ingest_result(result)))
    if result.failed:
        sys.exit(1)


def _run_digest(code: str, fight: int, as_json: bool, unregistered: bool) -> None:
    from rich.console import Console
    from rich.markdown import Markdown

    from khadbot.encounter.commands import UNREGISTERED_LIMIT, format_unregistered
    from khadbot.encounter.digest import build_digest, default_annotation_cache, render_digest
    from khadbot.encounter.ingest import default_store

    pull_key = f"{code}:{fight}"
    kwargs = {"unregistered_cap": UNREGISTERED_LIMIT} if unregistered else {}
    try:
        d = build_digest(pull_key, default_store(), cache=default_annotation_cache(), **kwargs)
    except KeyError as exc:
        sys.exit(str(exc.args[0]))
    if unregistered:
        Console().print(Markdown(format_unregistered(pull_key, d.unregistered)))
    elif as_json:
        print(d.model_dump_json(indent=2))
    else:
        print(render_digest(d))


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="khadbot", description="KhadBot — Agentic WoW Assistant")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("cli", help="Launch the interactive CLI (default)")
    ingest = sub.add_parser("ingest", help="Ingest a WarcraftLogs report into the local event store")
    ingest.add_argument("code", help="WarcraftLogs report code")
    ingest.add_argument("--fights", help="Comma-separated fight IDs (default: all encounter fights)")
    ingest.add_argument("--offline", action="store_true", help="Use only the raw page cache; never call WCL")
    digest = sub.add_parser("digest", help="Print the deterministic digest for one ingested pull")
    digest.add_argument("code", help="WarcraftLogs report code")
    digest.add_argument("fight", type=int, help="Fight ID")
    digest.add_argument("--json", action="store_true", help="Print the PullDigest as JSON")
    digest.add_argument(
        "--unregistered", action="store_true", help="List the top unregistered abilities (encounter-file authoring)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    command = args.command or "cli"

    if command == "cli":
        _run_cli()
    elif command == "ingest":
        _run_ingest(args.code, args.fights, args.offline)
    elif command == "digest":
        _run_digest(args.code, args.fight, args.json, args.unregistered)


if __name__ == "__main__":
    main()
