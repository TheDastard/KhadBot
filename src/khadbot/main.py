"""
main.py

Entry point for KhadBot. Dispatches to a subcommand; ``cli`` (the interactive
REPL) is the default.

Run: python main.py [cli]
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

    asyncio.run(run_cli(agent_fn))


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="khadbot", description="KhadBot — Agentic WoW Assistant")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("cli", help="Launch the interactive CLI (default)")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    command = args.command or "cli"

    if command == "cli":
        _run_cli()


if __name__ == "__main__":
    main()
