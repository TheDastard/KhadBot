"""
tests/unit/cli/test_cli.py

Unit tests for cli/cli.py.

What we test:
  - _echo_agent(): keyword routing logic — which tools are selected for
    which question keywords, including the fallback case
  - _echo_agent(): returns a non-empty string response in all cases
  - _echo_agent(): uses the panel from the first callback when provided

What we don't test:
  - run_cli() REPL loop — covered by integration tests (test_repl_loop.py)
  - main() — one-liner asyncio.run wrapper, not worth a unit test
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from khadbot.cli.cli import _echo_agent
from khadbot.cli.tool_panel import ToolPanel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _panel() -> ToolPanel:
    """Return a ToolPanel with Live disabled."""
    p = ToolPanel()
    p._live = None
    return p


def _handler(panel: ToolPanel) -> MagicMock:
    handler = MagicMock()
    handler.panel = panel
    return handler


async def _run(question: str, panel: ToolPanel | None = None) -> tuple[str, ToolPanel]:
    """Run _echo_agent with zero sleep and return (response, panel)."""
    p = panel or _panel()
    with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
        response = await _echo_agent(question, [_handler(p)])
    return response, p


# ===========================================================================
# Tool selection by keyword
# ===========================================================================


class TestEchoAgentToolRouting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "question",
        [
            "check my log",
            "why is my parse low",
            "my dps is bad",
            "warcraftlogs report abc123",
        ],
    )
    async def test_warcraftlogs_keywords(self, question: str) -> None:
        _, panel = await _run(question)
        tool_names = [e.tool_name for e in panel._tools]
        assert "get_warcraftlogs_report" in tool_names

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "question",
        [
            "check my character",
            "what is my m+ score",
            "raider io profile",
        ],
    )
    async def test_raiderio_keywords(self, question: str) -> None:
        _, panel = await _run(question)
        tool_names = [e.tool_name for e in panel._tools]
        assert "get_character_raiderio" in tool_names

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "question",
        [
            "run a sim on my gear",
            "compare this upgrade",
            "what trinket should I use",
        ],
    )
    async def test_simc_keywords(self, question: str) -> None:
        _, panel = await _run(question)
        tool_names = [e.tool_name for e in panel._tools]
        assert "run_simc" in tool_names

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "question",
        [
            "what is my opener rotation",
            "which talents should I pick",
            "what does icy veins say",
            "fire mage build guide",
        ],
    )
    async def test_rag_keywords(self, question: str) -> None:
        _, panel = await _run(question)
        tool_names = [e.tool_name for e in panel._tools]
        assert "search_guide_rag" in tool_names

    @pytest.mark.asyncio
    async def test_no_keyword_match_falls_back_to_rag(self) -> None:
        """Questions that match no keyword still trigger search_guide_rag."""
        _, panel = await _run("hello there")
        tool_names = [e.tool_name for e in panel._tools]
        assert "search_guide_rag" in tool_names
        assert len(tool_names) == 1

    @pytest.mark.asyncio
    async def test_multiple_keywords_trigger_multiple_tools(self) -> None:
        """A question matching multiple keyword groups invokes all relevant tools."""
        _, panel = await _run("check my log and run a sim")
        tool_names = [e.tool_name for e in panel._tools]
        assert "get_warcraftlogs_report" in tool_names
        assert "run_simc" in tool_names

    @pytest.mark.asyncio
    async def test_all_keywords_trigger_all_tools(self) -> None:
        question = "check my log parse dps character score m+ raider sim gear upgrade trinket rotation talent build"
        _, panel = await _run(question)
        tool_names = [e.tool_name for e in panel._tools]
        assert "get_warcraftlogs_report" in tool_names
        assert "get_character_raiderio" in tool_names
        assert "run_simc" in tool_names
        assert "search_guide_rag" in tool_names


# ===========================================================================
# Tool state after run
# ===========================================================================


class TestEchoAgentToolState:
    @pytest.mark.asyncio
    async def test_all_tools_reach_done_state(self) -> None:
        """Every tool started by _echo_agent must be finished (DONE) on return."""
        from khadbot.cli.tool_panel import ToolState

        _, panel = await _run("check my log parse dps character score sim gear rotation")
        for entry in panel._tools:
            assert entry.state == ToolState.DONE, f"{entry.tool_name} was left in state {entry.state}"

    @pytest.mark.asyncio
    async def test_uses_panel_from_callback(self) -> None:
        """_echo_agent must drive the panel from callbacks[0], not a new one."""
        p = _panel()
        await _run("check my log", panel=p)
        assert len(p._tools) > 0


# ===========================================================================
# Response content
# ===========================================================================


class TestEchoAgentResponse:
    @pytest.mark.asyncio
    async def test_returns_non_empty_string(self) -> None:
        response, _ = await _run("hello")
        assert isinstance(response, str)
        assert len(response) > 0

    @pytest.mark.asyncio
    async def test_response_contains_question(self) -> None:
        """The placeholder response echoes the question back."""
        response, _ = await _run("why is my DPS low?")
        assert "why is my DPS low?" in response

    @pytest.mark.asyncio
    async def test_response_consistent_across_keyword_matches(self) -> None:
        """Response is always a string regardless of which tools were triggered."""
        for question in ["check my log", "run a sim", "what rotation", "hello"]:
            response, _ = await _run(question)
            assert isinstance(response, str) and response
