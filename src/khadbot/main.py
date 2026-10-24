"""
main.py

Entry point for KhadBot. Dispatches to a subcommand; ``cli`` (the interactive
REPL) is the default.

Run: python main.py [cli | ingest <code> | digest <code> <fight> | serve [--port 8000] | api [--host H --port P] | migrate]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from langchain_core.messages import AIMessage, HumanMessage

from khadbot.errors import ConfigurationError, missing_group_message

SERVE_HOST = "127.0.0.1"
API_HOST = "0.0.0.0"  # the container binds every interface; the published port decides who can reach it

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

    from khadbot.cli.commands.encounter import digest_command, ingest_command, pulls_command, review_command

    commands = {
        "/ingest": ingest_command,
        "/digest": digest_command,
        "/pulls": pulls_command,
        "/review": review_command,
    }
    asyncio.run(run_cli(agent_fn, commands=commands))


def _run_ingest(code: str, fights: str | None, offline: bool, refresh_meta: bool = False) -> None:
    from rich.console import Console
    from rich.markdown import Markdown

    from khadbot.cli.commands.encounter import format_ingest_result, parse_fights
    from khadbot.jobs import IngestReport, JobContext, LineProgress, run_ingest_report

    console = Console()
    spec = IngestReport(code=code, fights=parse_fights(fights), offline=offline, refresh_meta=refresh_meta)
    result = asyncio.run(
        run_ingest_report(spec, JobContext(), LineProgress(lambda line: console.print(f"[dim]{line}[/dim]")))
    )
    console.print(Markdown(format_ingest_result(result)))
    if result.failed:
        sys.exit(1)


def _run_digest(code: str, fight: int, as_json: bool, unregistered: bool) -> None:
    from rich.console import Console
    from rich.markdown import Markdown

    from khadbot.cli.commands.encounter import format_unregistered, pull_digest
    from khadbot.encounter.digest import render_digest

    pull_key = f"{code}:{fight}"
    try:
        d = asyncio.run(pull_digest(pull_key, unregistered=unregistered))
    except KeyError as exc:
        sys.exit(str(exc.args[0]))
    if unregistered:
        Console().print(Markdown(format_unregistered(pull_key, d.unregistered, d.friendly_damage.unclassified)))
    elif as_json:
        print(d.model_dump_json(indent=2))
    else:
        print(render_digest(d))


def _run_serve(port: int) -> None:
    try:
        import uvicorn

        from khadbot.api import create_app
    except ImportError as exc:
        sys.exit(missing_group_message("api", "serve") + f"\n({exc})")

    # Local only: WCL credentials stay in the server's environment, and nothing is exposed beyond this machine.
    uvicorn.run(create_app(), host=SERVE_HOST, port=port)


def _run_api(host: str, port: int) -> None:
    try:
        import uvicorn

        from khadbot import wiring
        from khadbot.api import create_app
        from khadbot.config import DatabaseConfig
    except ImportError as exc:
        sys.exit(missing_group_message("api", "api") + f"\n({exc})")

    # Stores come from the composition root: with KHADBOT_MODE=hosted the findings and the night summary read Postgres.
    try:
        read_source = None
        if DatabaseConfig().hosted:
            try:
                from khadbot.api.night import SqlSource
                from khadbot.db import get_pool
            except ImportError as exc:
                sys.exit(missing_group_message("hosted", "api in hosted mode") + f"\n({exc})")
            read_source = SqlSource(get_pool())
        app = create_app(findings=wiring.findings_store(), read_source=read_source)
    except (ConfigurationError, OSError, ValueError) as exc:
        sys.exit(str(exc))
    uvicorn.run(app, host=host, port=port)


def _run_migrate() -> None:
    try:
        from khadbot.db import migrate, open_pool
    except ImportError as exc:
        sys.exit(missing_group_message("hosted", "migrate") + f"\n({exc})")

    try:
        pool = open_pool()
    except OSError as exc:
        sys.exit(str(exc))
    try:
        applied = migrate(pool)
    finally:
        pool.close()
    print("Applied: " + ", ".join(applied) if applied else "Database schema is up to date.")


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
    ingest.add_argument(
        "--refresh-meta",
        action="store_true",
        help="Refetch only the report metadata and update stored pulls; event pages come from the raw cache",
    )
    digest = sub.add_parser("digest", help="Print the deterministic digest for one ingested pull")
    digest.add_argument("code", help="WarcraftLogs report code")
    digest.add_argument("fight", type=int, help="Fight ID")
    digest.add_argument("--json", action="store_true", help="Print the PullDigest as JSON")
    digest.add_argument(
        "--unregistered",
        action="store_true",
        help="List the top unregistered and unclassified friendly abilities (authoring)",
    )
    serve = sub.add_parser("serve", help=f"Serve the read-only dashboard API on {SERVE_HOST} (KHADBOT_DATA_DIR)")
    serve.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    api = sub.add_parser(
        "api", help="Serve the dashboard API from the configured stores (the container's command; binds all interfaces)"
    )
    api.add_argument("--host", default=API_HOST, help=f"Interface to bind (default: {API_HOST})")
    api.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    sub.add_parser("migrate", help="Apply database migrations to DATABASE_URL (hosted mode)")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    command = args.command or "cli"
    if command == "ingest" and args.refresh_meta and args.offline:
        from khadbot.cli.commands.encounter import REFRESH_OFFLINE_ERROR

        parser.error(REFRESH_OFFLINE_ERROR)

    if command == "cli":
        _run_cli()
    elif command == "ingest":
        _run_ingest(args.code, args.fights, args.offline, args.refresh_meta)
    elif command == "digest":
        _run_digest(args.code, args.fight, args.json, args.unregistered)
    elif command == "serve":
        _run_serve(args.port)
    elif command == "api":
        _run_api(args.host, args.port)
    elif command == "migrate":
        _run_migrate()


if __name__ == "__main__":
    main()
