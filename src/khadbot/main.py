"""
main.py

Entry point for KhadBot. Dispatches to a subcommand; ``cli`` (the interactive
REPL) is the default.

Run: python main.py [cli | ingest <code> | digest <code> <fight> | serve [--port 8000] | api [--host H --port P] | migrate | worker | backfill --encounter ID]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
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

    # Stores come from the composition root. With KHADBOT_MODE=hosted the API is the hosted one: sign-in, report
    # access checks, the ingest routes on the job queue, and findings and read models from Postgres.
    hosted = DatabaseConfig().hosted
    try:
        if hosted:
            try:
                from khadbot.api.deps import SqlReportDirectory
                from khadbot.api.night import SqlSource
                from khadbot.db import get_pool
                from khadbot.jobs.queue import build_queue
            except ImportError as exc:
                sys.exit(missing_group_message("hosted", "api in hosted mode") + f"\n({exc})")
            pool = get_pool()
            app = create_app(
                findings=wiring.findings_store(),
                read_source=SqlSource(pool),
                auth=wiring.auth_service(),
                jobs=build_queue(pool=pool),  # the API only queues and cancels; workers run the jobs
                reports=SqlReportDirectory(pool),
            )
        else:
            app = create_app(findings=wiring.findings_store())
    except (ConfigurationError, OSError, ValueError) as exc:
        sys.exit(str(exc))
    if hosted and sys.platform == "win32":
        # psycopg's async connections (the job event stream, the queue) need the selector event loop, which Windows
        # does not use by default.
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
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


def _run_worker(queues: str | None, concurrency: int | None) -> None:
    from khadbot.config import DatabaseConfig

    if not DatabaseConfig().hosted:
        sys.exit("The worker runs hosted jobs: set KHADBOT_MODE=hosted and DATABASE_URL (and the S3 settings).")
    try:
        from khadbot.jobs.queue import serve_worker
    except ImportError as exc:
        sys.exit(missing_group_message("hosted", "worker") + f"\n({exc})")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    names = [q.strip() for q in queues.split(",") if q.strip()] if queues else None
    try:
        # psycopg's async connections need the selector event loop, which Windows does not use by default.
        asyncio.run(serve_worker(names, concurrency), loop_factory=asyncio.SelectorEventLoop)
    except (ConfigurationError, OSError) as exc:
        sys.exit(str(exc))


def _run_backfill(encounter: int, difficulty: int | None, since: str | None, dry_run: bool) -> None:
    from khadbot import wiring
    from khadbot.config import DatabaseConfig

    if not DatabaseConfig().hosted:
        sys.exit("Backfill queues hosted jobs: set KHADBOT_MODE=hosted and DATABASE_URL (and the S3 settings).")
    try:
        from khadbot.jobs.backfill import parse_since, run_backfill
        from khadbot.jobs.queue import build_queue
    except ImportError as exc:
        sys.exit(missing_group_message("hosted", "backfill") + f"\n({exc})")
    try:
        since_ms = parse_since(since) if since else None
    except ValueError:
        sys.exit(f"--since must be a date such as 2026-10-01, not {since!r}")

    async def run() -> None:
        index = wiring.read_model_index()
        assert index is not None  # hosted, checked above
        queue = build_queue()
        async with queue.open():
            result = await run_backfill(
                queue, index, encounter_id=encounter, difficulty=difficulty, since_ms=since_ms, dry_run=dry_run
            )
        if dry_run:
            print(f"{len(result.matched)} pulls match; nothing queued (dry run).")
        else:
            print(
                f"{len(result.matched)} pulls match: {len(result.enqueued)} queued, "
                f"{len(result.already_queued)} already queued. No WCL points are spent."
            )

    try:
        # psycopg's async connections need the selector event loop, which Windows does not use by default.
        asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    except (ConfigurationError, OSError) as exc:
        sys.exit(str(exc))


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
    worker = sub.add_parser("worker", help="Run the job queue worker (hosted mode)")
    worker.add_argument("--queues", help="Comma-separated queues to take jobs from (default: KHADBOT_WORKER_QUEUES)")
    worker.add_argument("--concurrency", type=int, help="Jobs to run at once (default: KHADBOT_WORKER_CONCURRENCY)")
    backfill = sub.add_parser(
        "backfill",
        help="Queue low-priority process jobs for an encounter's stored pulls (hosted mode; never spends WCL points)",
    )
    backfill.add_argument("--encounter", type=int, required=True, help="WCL encounter ID")
    backfill.add_argument("--difficulty", type=int, help="Only this difficulty (default: all)")
    backfill.add_argument("--since", help="Only reports that started on or after this date (UTC), e.g. 2026-10-01")
    backfill.add_argument("--dry-run", action="store_true", help="Count the matching pulls; queue nothing")
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
    elif command == "worker":
        _run_worker(args.queues, args.concurrency)
    elif command == "backfill":
        _run_backfill(args.encounter, args.difficulty, args.since, args.dry_run)


if __name__ == "__main__":
    main()
