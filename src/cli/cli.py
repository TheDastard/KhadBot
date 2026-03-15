"""
cli/cli.py — Main REPL loop for the WoW Coaching Agent CLI.

Wires together:
  - prompt_toolkit for input (history, tab completion, multi-line)
  - Rich for all output (via renderer.py)
  - ToolPanel + ToolPanelCallbackHandler for live tool progress
  - The agent (injected via `run_agent`) so this module stays UI-only

Running directly:
    python -m wow_agent.cli.cli

Or import and call `run_cli(agent_fn)` with your own agent coroutine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style as PTStyle

from config import get_config

from .console import console
from .renderer import (
    render_agent_response,
    render_banner,
    render_error,
    render_langsmith_footer,
    render_user_message,
)
from .tool_panel import ToolPanel, ToolPanelCallbackHandler

# ---------------------------------------------------------------------------
# Prompt Toolkit styling
# Keeps the input prompt visually consistent with the Rich theme.
# ---------------------------------------------------------------------------

PT_STYLE = PTStyle.from_dict(
    {
        "prompt": "bold ansiyellow",
    }
)

PROMPT_TEXT = [("class:prompt", "⚔  ")]

# Commands that aren't passed to the agent
EXIT_COMMANDS = {"/exit", "/quit", "/q"}
HELP_COMMAND = "/help"

HELP_TEXT = """
[ui.subheader]Available commands[/ui.subheader]

  [ui.prompt]/help[/ui.prompt]           Show this message
  [ui.prompt]/exit[/ui.prompt]           Quit the CLI
  [ui.prompt]/clear[/ui.prompt]          Clear the screen

[ui.subheader]Example questions[/ui.subheader]

  Why is my DPS lower than others in my raid with similar gear?
  What trinkets should I be targeting this tier?
  Run a sim on my current gear vs. this upgrade.
  What does Icy Veins say about my opener rotation?
"""

# ---------------------------------------------------------------------------
# Agent type alias
# An agent function accepts a question string and a list of LangChain
# callbacks, and returns the response string.
# ---------------------------------------------------------------------------

AgentFn = Callable[[str, list], Awaitable[str]]


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


async def run_cli(agent_fn: AgentFn | None = None) -> None:
    """
    Start the interactive CLI loop.

    Parameters
    ----------
    agent_fn:
        Async callable `(question: str, callbacks: list) -> str`.
        If None, a placeholder echo function is used so the UI can be
        developed and tested independently of the agent.
    """
    if agent_fn is None:
        agent_fn = _echo_agent

    render_banner()

    session: PromptSession = PromptSession(
        history=InMemoryHistory(),
        style=PT_STYLE,
        mouse_support=False,
    )

    while True:
        # ── Input ──────────────────────────────────────────────────────────
        try:
            raw = await session.prompt_async(PROMPT_TEXT)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[ui.muted]Exiting. May your parses be purple.[/ui.muted]\n")
            break

        user_input = raw.strip()
        if not user_input:
            continue

        # ── Built-in commands ──────────────────────────────────────────────
        if user_input.lower() in EXIT_COMMANDS:
            console.print("\n[ui.muted]Exiting. May your parses be purple.[/ui.muted]\n")
            break

        if user_input.lower() == HELP_COMMAND:
            console.print(HELP_TEXT)
            continue

        if user_input.lower() == "/clear":
            console.clear()
            render_banner()
            continue

        # ── Agent invocation ───────────────────────────────────────────────
        render_user_message(user_input)

        handler = ToolPanelCallbackHandler()

        try:
            async with handler.panel:
                response = await agent_fn(user_input, [handler])
        except Exception as exc:  # noqa: BLE001
            render_error(str(exc), title="Agent Error")
            continue

        render_agent_response(response)

        if get_config().observability.langchain_tracing:
            render_langsmith_footer(handler.run_id)


# ---------------------------------------------------------------------------
# Placeholder agent (used when no real agent is injected)
# Useful for UI development without needing the full LangChain stack running.
# ---------------------------------------------------------------------------


async def _echo_agent(question: str, callbacks: list) -> str:
    """
    Simulates an agent response with fake tool calls so the panel can be
    demoed without a real LangChain agent attached.
    """
    import random

    panel: ToolPanel = callbacks[0].panel if callbacks else ToolPanel()

    # Simulate tool selection based on rough keyword matching
    tools_to_run: list[tuple[str, str, str]] = []

    if any(w in question.lower() for w in ["log", "parse", "dps", "warcraftlogs"]):
        tools_to_run.append(("get_warcraftlogs_report", "Fetching report…", "12 fights loaded"))

    if any(w in question.lower() for w in ["character", "score", "m+", "raider"]):
        tools_to_run.append(("get_character_raiderio", "Fetching Thrall-Stormrage…", "2847 M+ score"))

    if any(w in question.lower() for w in ["sim", "gear", "upgrade", "trinket"]):
        tools_to_run.append(("run_simc", "Running simulation…", "Mean DPS: 483,221"))

    if any(w in question.lower() for w in ["rotation", "talent", "build", "icy veins", "guide"]):
        tools_to_run.append(("search_guide_rag", "Searching Fire Mage guide…", "3 chunks retrieved"))

    if not tools_to_run:
        tools_to_run.append(("search_guide_rag", "Searching guides…", "2 chunks retrieved"))

    # Simulate each tool running
    for tool_name, detail, result in tools_to_run:
        panel.start_tool(tool_name, detail)
        await asyncio.sleep(random.uniform(0.8, 2.0))  # simulate latency
        panel.finish_tool(tool_name, result)

    await asyncio.sleep(0.3)  # simulate final LLM synthesis

    return (
        "## Coaching Response\n\n"
        "*(This is a placeholder response — wire up your agent via `run_cli(agent_fn)`.)*\n\n"
        f"You asked: **{question}**\n\n"
        "Once the real agent is connected, this panel will show live tool progress "
        "as WarcraftLogs, Raider.IO, SimC, and the Icy Veins RAG corpus are queried."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    asyncio.run(run_cli())


if __name__ == "__main__":
    main()
