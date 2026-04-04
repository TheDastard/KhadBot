"""
tests/integration/cli/test_tool_panel_live.py

Integration tests for the ToolPanel async context manager.

These tests exercise the real __aenter__/__aexit__ path against a Rich Live
instance backed by a StringIO console — no TTY required, no mocking of the
panel's internal state machine.

What's verified:
  - Panel starts and stops cleanly without exceptions
  - Tool lifecycle (start → finish) completes correctly inside the context
  - Tool lifecycle (start → fail) completes correctly inside the context
  - Panel state reflects reality after __aexit__: _live is None, tools are DONE/FAILED
  - Multiple sequential tools complete in correct final states
  - An exception raised inside the context manager doesn't leave Live running
"""

from __future__ import annotations

import pytest

from khadbot.cli.tool_panel import ToolPanel, ToolState

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_manager_starts_and_stops_cleanly():
    """Basic entry and exit without any tool calls must not raise."""
    panel = ToolPanel()
    async with panel:
        pass
    assert panel._live is None


@pytest.mark.asyncio
async def test_tool_finish_inside_context():
    """start_tool → finish_tool inside the context leaves the entry DONE."""
    panel = ToolPanel()
    async with panel:
        panel.start_tool("get_character_raiderio", "Fetching Thrall")
        panel.finish_tool("get_character_raiderio", "2847 M+")

    entry = panel._tools[0]
    assert entry.state == ToolState.DONE
    assert entry.result_summary == "2847 M+"
    assert entry.end_time is not None
    assert panel._live is None


@pytest.mark.asyncio
async def test_tool_fail_inside_context():
    """start_tool → fail_tool inside the context leaves the entry FAILED."""
    panel = ToolPanel()
    async with panel:
        panel.start_tool("run_simc", "Running sim")
        panel.fail_tool("run_simc", "SimC binary not found")

    entry = panel._tools[0]
    assert entry.state == ToolState.FAILED
    assert entry.result_summary == "SimC binary not found"
    assert panel._live is None


@pytest.mark.asyncio
async def test_multiple_sequential_tools():
    """Multiple tools run in sequence all reach their final states."""
    panel = ToolPanel()
    async with panel:
        panel.start_tool("get_character_raiderio", "Fetching char")
        panel.finish_tool("get_character_raiderio", "2847 M+")

        panel.start_tool("get_warcraftlogs_report", "Fetching logs")
        panel.finish_tool("get_warcraftlogs_report", "12 fights")

        panel.start_tool("run_simc", "Running sim")
        panel.fail_tool("run_simc", "timeout")

    states = {e.tool_name: e.state for e in panel._tools}
    assert states["get_character_raiderio"] == ToolState.DONE
    assert states["get_warcraftlogs_report"] == ToolState.DONE
    assert states["run_simc"] == ToolState.FAILED
    assert panel._live is None


@pytest.mark.asyncio
async def test_exception_inside_context_does_not_leave_live_running():
    """
    If the body of the context manager raises, __aexit__ must still stop
    the Live display. A leaked Live instance would corrupt subsequent
    terminal output for the rest of the session.
    """
    panel = ToolPanel()
    with pytest.raises(RuntimeError, match="agent exploded"):
        async with panel:
            panel.start_tool("get_character_raiderio")
            raise RuntimeError("agent exploded")

    assert panel._live is None


@pytest.mark.asyncio
async def test_pending_tools_visible_before_run():
    """
    add_pending_tools pre-populates entries that are visible in the panel
    before any tool actually starts — verifying they survive into the
    context correctly.
    """
    panel = ToolPanel()
    panel.add_pending_tools(["get_character_raiderio", "run_simc"])

    async with panel:
        panel.start_tool("get_character_raiderio")
        panel.finish_tool("get_character_raiderio", "done")
        # run_simc intentionally left as PENDING

    states = {e.tool_name: e.state for e in panel._tools}
    assert states["get_character_raiderio"] == ToolState.DONE
    assert states["run_simc"] == ToolState.PENDING
