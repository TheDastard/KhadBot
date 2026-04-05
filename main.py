"""
main.py

CLI entry point for KhadBot.

Wires the LangGraph graph to the Rich/Prompt Toolkit CLI.
Each CLI session gets a fixed thread_id so LangGraph's MemorySaver
persists character context and conversation history across turns.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from khadbot.agent import build_graph, run_turn


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "cli"

    if mode == "cli":
        _run_cli()
    else:
        print(f"Unknown mode: {mode}.")
        sys.exit(1)


def _run_cli() -> None:
    from khadbot.cli import run_cli

    # Build the graph once per process.  Persona resolved from KHADBOT_PERSONA
    # env var or None (base identity).
    graph, persona_id = build_graph()

    # Stable thread ID for this CLI session — persists across turns so
    # LangGraph checkpoint layer maintains character context and history.
    thread_id = f"cli-{uuid.uuid4().hex[:8]}"

    async def agent_fn(question: str, callbacks: list) -> str:
        result = await run_turn(
            graph=graph,
            user_message=question,
            thread_id=thread_id,
            persona_id=persona_id,
            callbacks=callbacks,
        )
        return result["answer"]

    asyncio.run(run_cli(agent_fn))


if __name__ == "__main__":
    main()
