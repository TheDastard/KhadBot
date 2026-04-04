"""
tests/unit/cli/test_tool_panel.py

Unit tests for cli/tool_panel.py.

What we test:
  ToolPanel (state machine — no live terminal needed):
    - start_tool / finish_tool / fail_tool state transitions
    - add_pending_tools pre-population and deduplication
    - set_thinking updates the message
    - finish_tool / fail_tool on a PENDING entry (no-op — must be RUNNING first)
    - Multiple tools tracked independently
    - Calling finish_tool on an unknown tool name is a no-op (no crash)

  ToolPanelCallbackHandler (LangChain callback wiring):
    - on_chain_start captures root run_id (parent_run_id is None)
    - on_chain_start ignores sub-chain calls (parent_run_id is set)
    - on_tool_start routes to panel.start_tool with correct name and detail
    - on_tool_start truncates long input strings
    - on_tool_end extracts content from ToolMessage object
    - on_tool_end falls back to _last_tool_name when kwargs name is absent
    - on_tool_error routes to panel.fail_tool
    - on_llm_start updates thinking message
    - on_agent_action updates thinking message with tool name

What we don't test:
  - The Live/Rich rendering path — that requires a real terminal and tests Rich
  - The async context manager (__aenter__/__aexit__) — covered by integration tests
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from khadbot.cli.tool_panel import (
    ToolEntry,
    ToolPanel,
    ToolPanelCallbackHandler,
    ToolState,
    _display,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _make_panel() -> ToolPanel:
    """Return a ToolPanel with Live disabled (no terminal needed)."""
    panel = ToolPanel()
    panel._live = None  # ensure _refresh() is a no-op
    return panel


# ===========================================================================
# _display
# ===========================================================================


class TestDisplay:
    def test_known_tool_returns_label(self) -> None:
        _, label = _display("get_character_raiderio")
        assert label == "Raider.IO"

    def test_unknown_tool_returns_name_as_label(self) -> None:
        _, label = _display("some_future_tool")
        assert label == "some_future_tool"


# ===========================================================================
# ToolPanel state machine
# ===========================================================================


class TestToolPanelStartTool:
    def test_adds_running_entry(self) -> None:
        panel = _make_panel()
        panel.start_tool("get_character_raiderio", "Fetching Thrall")
        assert len(panel._tools) == 1
        entry = panel._tools[0]
        assert entry.tool_name == "get_character_raiderio"
        assert entry.state == ToolState.RUNNING
        assert entry.detail == "Fetching Thrall"

    def test_promotes_pending_to_running(self) -> None:
        panel = _make_panel()
        panel.add_pending_tools(["run_simc"])
        panel.start_tool("run_simc", "Running sim")
        assert panel._tools[0].state == ToolState.RUNNING
        assert panel._tools[0].detail == "Running sim"
        assert len(panel._tools) == 1  # not duplicated

    def test_multiple_tools_tracked_independently(self) -> None:
        panel = _make_panel()
        panel.start_tool("get_character_raiderio", "Fetching char")
        panel.start_tool("get_warcraftlogs_report", "Fetching logs")
        assert len(panel._tools) == 2
        assert panel._tools[0].tool_name == "get_character_raiderio"
        assert panel._tools[1].tool_name == "get_warcraftlogs_report"


class TestToolPanelFinishTool:
    def test_transitions_running_to_done(self) -> None:
        panel = _make_panel()
        panel.start_tool("get_character_raiderio")
        panel.finish_tool("get_character_raiderio", "2847 M+")
        entry = panel._tools[0]
        assert entry.state == ToolState.DONE
        assert entry.result_summary == "2847 M+"
        assert entry.end_time is not None

    def test_finish_on_pending_is_noop(self) -> None:
        """finish_tool only transitions RUNNING entries — PENDING is untouched."""
        panel = _make_panel()
        panel.add_pending_tools(["run_simc"])
        panel.finish_tool("run_simc", "should not apply")
        assert panel._tools[0].state == ToolState.PENDING
        assert panel._tools[0].result_summary == ""

    def test_finish_unknown_tool_is_noop(self) -> None:
        panel = _make_panel()
        panel.start_tool("get_character_raiderio")
        panel.finish_tool("nonexistent_tool")  # must not raise
        assert panel._tools[0].state == ToolState.RUNNING


class TestToolPanelFailTool:
    def test_transitions_running_to_failed(self) -> None:
        panel = _make_panel()
        panel.start_tool("run_simc")
        panel.fail_tool("run_simc", "SimC binary not found")
        entry = panel._tools[0]
        assert entry.state == ToolState.FAILED
        assert entry.result_summary == "SimC binary not found"
        assert entry.end_time is not None

    def test_fail_on_pending_is_noop(self) -> None:
        panel = _make_panel()
        panel.add_pending_tools(["run_simc"])
        panel.fail_tool("run_simc", "error")
        assert panel._tools[0].state == ToolState.PENDING


class TestToolPanelAddPendingTools:
    def test_adds_pending_entries(self) -> None:
        panel = _make_panel()
        panel.add_pending_tools(["get_character_raiderio", "run_simc"])
        assert len(panel._tools) == 2
        assert all(e.state == ToolState.PENDING for e in panel._tools)

    def test_deduplicates(self) -> None:
        panel = _make_panel()
        panel.add_pending_tools(["run_simc"])
        panel.add_pending_tools(["run_simc"])  # called again
        assert len(panel._tools) == 1

    def test_does_not_overwrite_running_entry(self) -> None:
        panel = _make_panel()
        panel.start_tool("run_simc")
        panel.add_pending_tools(["run_simc"])  # already present as RUNNING
        assert len(panel._tools) == 1
        assert panel._tools[0].state == ToolState.RUNNING


class TestToolPanelSetThinking:
    def test_updates_message(self) -> None:
        panel = _make_panel()
        panel.set_thinking("Calling WarcraftLogs…")
        assert panel._thinking_message == "Calling WarcraftLogs…"


# ===========================================================================
# ToolEntry.elapsed
# ===========================================================================


class TestToolEntryElapsed:
    def test_elapsed_format(self) -> None:
        import time

        entry = ToolEntry(tool_name="test")
        entry.start_time = time.monotonic() - 1.5
        entry.end_time = time.monotonic()
        # Should be close to 1.5s — just verify it's a formatted float string
        assert entry.elapsed.endswith("s")
        assert float(entry.elapsed[:-1]) == pytest.approx(1.5, abs=0.2)

    def test_elapsed_uses_monotonic_when_not_finished(self) -> None:
        entry = ToolEntry(tool_name="test")
        # end_time is None — elapsed should still return a valid string
        elapsed = entry.elapsed
        assert elapsed.endswith("s")
        assert float(elapsed[:-1]) >= 0.0


# ===========================================================================
# ToolPanelCallbackHandler
# ===========================================================================


class TestCallbackHandlerRunId:
    def test_captures_root_run_id(self) -> None:
        handler = ToolPanelCallbackHandler()
        root_id = uuid4()
        handler.on_chain_start({}, {}, run_id=root_id, parent_run_id=None)
        assert handler.run_id == str(root_id)

    def test_ignores_sub_chain_run_id(self) -> None:
        """A sub-chain call must not overwrite the root run_id."""
        handler = ToolPanelCallbackHandler()
        root_id = uuid4()
        sub_id = uuid4()
        handler.on_chain_start({}, {}, run_id=root_id, parent_run_id=None)
        handler.on_chain_start({}, {}, run_id=sub_id, parent_run_id=root_id)
        assert handler.run_id == str(root_id)

    def test_run_id_none_before_chain_start(self) -> None:
        handler = ToolPanelCallbackHandler()
        assert handler.run_id is None

    def test_none_run_id_arg_is_not_stored(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.on_chain_start({}, {}, run_id=None, parent_run_id=None)
        assert handler.run_id is None


class TestCallbackHandlerToolStart:
    def test_routes_to_panel_start_tool(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler.on_tool_start({"name": "get_character_raiderio"}, "Thrall Stormrage us")
        handler.panel.start_tool.assert_called_once_with("get_character_raiderio", "Thrall Stormrage us")

    def test_truncates_long_input(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        long_input = "x" * 200
        handler.on_tool_start({"name": "run_simc"}, long_input)
        _, detail = handler.panel.start_tool.call_args[0]
        assert len(detail) <= 81  # 80 chars + ellipsis
        assert detail.endswith("…")

    def test_short_input_not_truncated(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler.on_tool_start({"name": "run_simc"}, "short input")
        _, detail = handler.panel.start_tool.call_args[0]
        assert detail == "short input"

    def test_tracks_last_tool_name(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler.on_tool_start({"name": "search_guide_rag"}, "fire mage opener")
        assert handler._last_tool_name == "search_guide_rag"

    def test_unknown_tool_name_falls_back(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler.on_tool_start({}, "some input")  # no "name" key
        name, _ = handler.panel.start_tool.call_args[0]
        assert name == "unknown_tool"


class TestCallbackHandlerToolEnd:
    def test_extracts_content_from_tool_message(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler._last_tool_name = "get_character_raiderio"

        tool_message = MagicMock()
        tool_message.content = "2847 M+ score"
        handler.on_tool_end(tool_message)

        handler.panel.finish_tool.assert_called_once_with("get_character_raiderio", "2847 M+ score")

    def test_truncates_long_content(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler._last_tool_name = "run_simc"

        tool_message = MagicMock()
        tool_message.content = "x" * 200
        handler.on_tool_end(tool_message)

        _, summary = handler.panel.finish_tool.call_args[0]
        assert len(summary) <= 81
        assert summary.endswith("…")

    def test_uses_kwargs_name_over_last_tool_name(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler._last_tool_name = "wrong_tool"

        tool_message = MagicMock()
        tool_message.content = "result"
        handler.on_tool_end(tool_message, name="get_warcraftlogs_report")

        name, _ = handler.panel.finish_tool.call_args[0]
        assert name == "get_warcraftlogs_report"

    def test_no_call_when_no_tool_name(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler._last_tool_name = None

        tool_message = MagicMock()
        tool_message.content = "result"
        handler.on_tool_end(tool_message)

        handler.panel.finish_tool.assert_not_called()


class TestCallbackHandlerToolError:
    def test_routes_to_panel_fail_tool(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler._last_tool_name = "run_simc"
        handler.on_tool_error(ValueError("SimC binary not found"))
        handler.panel.fail_tool.assert_called_once_with("run_simc", "SimC binary not found")

    def test_truncates_long_error(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler._last_tool_name = "run_simc"
        handler.on_tool_error(ValueError("x" * 200))
        _, error_str = handler.panel.fail_tool.call_args[0]
        assert len(error_str) <= 80


class TestCallbackHandlerThinking:
    def test_on_llm_start_sets_reasoning(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        handler.on_llm_start({}, [])
        handler.panel.set_thinking.assert_called_once_with("Reasoning…")

    def test_on_agent_action_sets_tool_name(self) -> None:
        handler = ToolPanelCallbackHandler()
        handler.panel = MagicMock()
        action = MagicMock()
        action.tool = "run_simc"
        handler.on_agent_action(action)
        handler.panel.set_thinking.assert_called_once_with("Calling run_simc…")
