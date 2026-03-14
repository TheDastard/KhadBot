"""
cli/tool_panel.py — Live-updating tool execution panel for the WoW Coaching Agent CLI.

Usage
-----
The ToolPanel is used as a context manager wrapping the agent's run loop.
Call `.start_tool()` when a tool begins and `.finish_tool()` / `.fail_tool()`
when it completes. The panel updates in-place — no new lines are printed
while tools are running.

    async with ToolPanel() as panel:
        panel.start_tool("raiderio", "Fetching Thrall-Stormrage...")
        result = await get_character_raiderio("Thrall", "Stormrage", "us")
        panel.finish_tool("raiderio", "Thrall-Stormrage — 2847 M+")

        panel.start_tool("warcraftlogs", "Fetching report abc123...")
        data = await get_warcraftlogs_report("abc123")
        panel.finish_tool("warcraftlogs", "12 fights loaded")

Integrating with LangChain callbacks
-------------------------------------
The companion `ToolPanelCallbackHandler` implements LangChain's
`BaseCallbackHandler` so the panel updates automatically when the agent
calls tools — no manual `.start_tool()` calls needed in that case:

    handler = ToolPanelCallbackHandler()
    async with handler.panel:
        result = await agent.ainvoke({"messages": [...]},
                                     config={"callbacks": [handler]})
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .console import console

# ---------------------------------------------------------------------------
# Tool display names and icons
# Keeps the panel readable — internal function names aren't user-friendly.
# ---------------------------------------------------------------------------

TOOL_DISPLAY: dict[str, tuple[str, str]] = {
    "get_character_raiderio": ("🗺", "Raider.IO"),
    "get_warcraftlogs_report": ("📊", "WarcraftLogs"),
    "run_simc": ("⚙", "SimulationCraft"),
    "search_guide_rag": ("📖", "Icy Veins Guide"),
}

_DEFAULT_ICON = "🔧"
_DEFAULT_LABEL = "Tool"


def _display(tool_name: str) -> tuple[str, str]:
    return TOOL_DISPLAY.get(tool_name, (_DEFAULT_ICON, tool_name))


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


class ToolState(Enum):
    PENDING = auto()
    RUNNING = auto()
    DONE = auto()
    FAILED = auto()


@dataclass
class ToolEntry:
    tool_name: str
    state: ToolState = ToolState.PENDING
    detail: str = ""
    result_summary: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None

    @property
    def elapsed(self) -> str:
        t = (self.end_time or time.monotonic()) - self.start_time
        return f"{t:.1f}s"


# ---------------------------------------------------------------------------
# Panel renderer
# ---------------------------------------------------------------------------


class ToolPanel:
    """
    Manages the live tool-execution panel.

    Use as a context manager:
        async with ToolPanel() as panel:
            panel.start_tool(...)
    """

    # Title shown in the panel border
    PANEL_TITLE = "[ui.subheader]KhadBot — Working[/ui.subheader]"
    PANEL_TITLE_DONE = "[ui.subheader]KhadBot — Done[/ui.subheader]"

    def __init__(self) -> None:
        self._tools: list[ToolEntry] = []
        self._thinking_message: str = "Thinking…"
        self._live: Live | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_thinking(self, message: str) -> None:
        """Update the 'thinking' status line (shown before any tool is called)."""
        self._thinking_message = message
        self._refresh()

    def start_tool(self, tool_name: str, detail: str = "") -> None:
        """Mark a tool as actively running."""
        # If the tool already has an entry (e.g. called twice), update it
        for entry in self._tools:
            if entry.tool_name == tool_name and entry.state == ToolState.PENDING:
                entry.state = ToolState.RUNNING
                entry.detail = detail
                entry.start_time = time.monotonic()
                self._refresh()
                return

        entry = ToolEntry(tool_name=tool_name, state=ToolState.RUNNING, detail=detail)
        self._tools.append(entry)
        self._refresh()

    def finish_tool(self, tool_name: str, result_summary: str = "") -> None:
        """Mark a tool as successfully completed."""
        self._update_tool(tool_name, ToolState.DONE, result_summary)

    def fail_tool(self, tool_name: str, error: str = "") -> None:
        """Mark a tool as failed."""
        self._update_tool(tool_name, ToolState.FAILED, error)

    def add_pending_tools(self, tool_names: list[str]) -> None:
        """
        Pre-populate the panel with pending tools before they run.
        Lets the user see the full planned execution upfront.
        """
        for name in tool_names:
            if not any(e.tool_name == name for e in self._tools):
                self._tools.append(ToolEntry(tool_name=name, state=ToolState.PENDING))
        self._refresh()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> ToolPanel:
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=10,
            transient=False,  # keep panel visible after exit
        )
        self._live.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._live:
            # Final render with "Done" title before stopping
            self._live.update(self._render(done=True))
            self._live.stop()
            self._live = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_tool(self, tool_name: str, state: ToolState, summary: str) -> None:
        for entry in self._tools:
            if entry.tool_name == tool_name and entry.state == ToolState.RUNNING:
                entry.state = state
                entry.result_summary = summary
                entry.end_time = time.monotonic()
                self._refresh()
                return

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    def _render(self, done: bool = False) -> Panel:
        grid = Table.grid(padding=(0, 1))
        grid.add_column(width=2)  # icon
        grid.add_column(width=16)  # tool label
        grid.add_column()  # detail / result
        grid.add_column(width=6, justify="right")  # elapsed

        if not self._tools:
            # Nothing called yet — show spinner + thinking message
            spinner = Spinner("dots", style="tool.running")
            grid.add_row(spinner, Text(""), Text(self._thinking_message, style="tool.running"), Text(""))
        else:
            for entry in self._tools:
                icon_text, label = _display(entry.tool_name)
                status_icon, label_style, detail_style = _state_styles(entry.state)

                detail = entry.result_summary if entry.state in (ToolState.DONE, ToolState.FAILED) else entry.detail

                grid.add_row(
                    status_icon,
                    Text(label, style=label_style),
                    Text(detail, style=detail_style),
                    Text(entry.elapsed if entry.state != ToolState.PENDING else "", style="ui.muted"),
                )

        title = self.PANEL_TITLE_DONE if done else self.PANEL_TITLE
        border = "dim cyan" if done else "bright_cyan"

        return Panel(
            Padding(grid, (0, 1)),
            title=title,
            border_style=border,
            padding=(0, 0),
        )


def _state_styles(state: ToolState) -> tuple[Any, str, str]:
    """Return (status_renderable, label_style, detail_style) for a tool state."""
    if state == ToolState.PENDING:
        return Text("·", style="tool.pending"), "tool.pending", "tool.pending"
    if state == ToolState.RUNNING:
        return Spinner("dots", style="tool.running"), "tool.running", "tool.running"
    if state == ToolState.DONE:
        return Text("✓", style="tool.done"), "tool.done", "ui.muted"
    if state == ToolState.FAILED:
        return Text("✗", style="tool.failed"), "tool.failed", "tool.failed"
    return Text("?", style="ui.muted"), "ui.muted", "ui.muted"


# ---------------------------------------------------------------------------
# LangChain callback handler
# ---------------------------------------------------------------------------

try:
    from langchain_core.callbacks import BaseCallbackHandler

    class ToolPanelCallbackHandler(BaseCallbackHandler):
        """
        LangChain callback handler that drives the ToolPanel automatically.

        Attach to agent invocations via the `callbacks` config key:

            handler = ToolPanelCallbackHandler()
            async with handler.panel:
                result = await agent.ainvoke(
                    {"messages": [...]},
                    config={"callbacks": [handler]},
                )
        """

        def __init__(self) -> None:
            super().__init__()
            self.panel = ToolPanel()
            self._last_tool_name: str | None = None

        # LangChain calls these at the start/end of each tool invocation

        def on_tool_start(
            self,
            serialized: dict[str, Any],
            input_str: str,
            **kwargs: Any,
        ) -> None:
            tool_name = serialized.get("name", "unknown_tool")
            self._last_tool_name = tool_name
            # Show the raw input trimmed — can be long for SimC strings
            detail = input_str[:80] + "…" if len(input_str) > 80 else input_str
            self.panel.start_tool(tool_name, detail)

        def on_tool_end(self, output: str, **kwargs: Any) -> None:
            # Pull the tool name out of kwargs LangChain passes through
            tool_name = kwargs.get("name") or self._last_tool_name
            if tool_name:
                # Trim output for display
                summary = output[:80] + "…" if len(output) > 80 else output
                self.panel.finish_tool(tool_name, summary)

        def on_tool_error(self, error: Exception, **kwargs: Any) -> None:
            tool_name = kwargs.get("name") or self._last_tool_name
            if tool_name:
                self.panel.fail_tool(tool_name, str(error)[:80])

        def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
            self.panel.set_thinking("Reasoning…")

        def on_agent_action(self, action: Any, **kwargs: Any) -> None:
            self.panel.set_thinking(f"Calling {action.tool}…")

except ImportError:
    # langchain_core not installed — callback handler simply unavailable
    class ToolPanelCallbackHandler:  # type: ignore
        def __init__(self) -> None:
            self.panel = ToolPanel()
            console.print("[ui.warning]langchain_core not installed — callback handler unavailable[/ui.warning]")
