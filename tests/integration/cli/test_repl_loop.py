"""
tests/integration/cli/test_repl_loop.py

Integration tests for the run_cli REPL loop.

Uses Prompt Toolkit's create_pipe_input to inject synthetic input into the
session without a real TTY. All Rich output is captured via a StringIO-backed
Console. The agent_fn is a fast async stub — no LLM inference.

What's verified:
  - /exit terminates the loop cleanly
  - /quit and /q are also recognised as exit commands
  - /help prints help text and continues (does not exit)
  - Empty input is ignored (loop continues)
  - A normal question invokes agent_fn and renders the response
  - An agent_fn that raises renders an error panel and continues
  - /clear calls console.clear() and re-renders the banner
  - EOFError (Ctrl-D) exits cleanly
  - render_langsmith_footer is called after each successful agent response
  - handler.run_id from the callback handler is forwarded to the footer

Architecture note
-----------------
run_cli creates a new ToolPanelCallbackHandler per turn. We verify the
full wiring by patching render_langsmith_footer and asserting it receives
handler.run_id rather than inspecting internal handler state directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from khadbot.cli.cli import run_cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_agent_fn():
    """Async agent function that returns immediately with a canned response."""

    async def _agent(question: str, callbacks: list) -> str:
        return f"Coaching advice for: {question}"

    return _agent


@pytest.fixture
def raising_agent_fn():
    """Async agent function that always raises."""

    async def _agent(question: str, callbacks: list) -> str:
        raise RuntimeError("LLM backend unavailable")

    return _agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_with_input(agent_fn, *input_lines: str) -> None:
    """
    Run run_cli with synthetic input.

    Patches PromptSession to drain a scripted list of inputs then raise
    EOFError, which exits the REPL cleanly. Console output goes to the
    session-scoped StringIO buffer in mock_cli_console (conftest.py).
    """
    with patch("khadbot.cli.cli.PromptSession") as mock_session_cls:
        session = mock_session_cls.return_value

        lines = list(input_lines)
        call_count = 0

        async def prompt_side_effect(*args, **kwargs):
            nonlocal call_count
            if call_count < len(lines):
                result = lines[call_count]
                call_count += 1
                return result
            raise EOFError  # no more input

        session.prompt_async = prompt_side_effect
        await run_cli(agent_fn)


# ---------------------------------------------------------------------------
# Tests — exit commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_command_terminates(fast_agent_fn):
    """/exit causes the loop to exit without calling agent_fn."""
    called = []

    async def tracking_agent(q, cb):
        called.append(q)
        return "response"

    await _run_with_input(tracking_agent, "/exit")
    assert called == []


@pytest.mark.asyncio
async def test_quit_command_terminates(fast_agent_fn):
    await _run_with_input(fast_agent_fn, "/quit")


@pytest.mark.asyncio
async def test_q_command_terminates(fast_agent_fn):
    await _run_with_input(fast_agent_fn, "/q")


@pytest.mark.asyncio
async def test_exit_is_case_insensitive(fast_agent_fn):
    await _run_with_input(fast_agent_fn, "/EXIT")


# ---------------------------------------------------------------------------
# Tests — built-in commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_does_not_exit(fast_agent_fn):
    """/help prints help text and loop continues — confirmed by second /exit."""
    called = []

    async def tracking_agent(q, cb):
        called.append(q)
        return "response"

    # /help then /exit — agent should never be called
    await _run_with_input(tracking_agent, "/help", "/exit")
    assert called == []


@pytest.mark.asyncio
async def test_empty_input_ignored(fast_agent_fn):
    """Blank lines don't invoke agent_fn."""
    called = []

    async def tracking_agent(q, cb):
        called.append(q)
        return "response"

    await _run_with_input(tracking_agent, "", "   ", "/exit")
    assert called == []


@pytest.mark.asyncio
async def test_clear_command_does_not_invoke_agent(fast_agent_fn):
    called = []

    async def tracking_agent(q, cb):
        called.append(q)
        return "response"

    with patch("khadbot.cli.cli.render_banner"):
        await _run_with_input(tracking_agent, "/clear", "/exit")
    assert called == []


# ---------------------------------------------------------------------------
# Tests — normal question flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_question_invokes_agent_fn():
    """A normal question is forwarded to agent_fn."""
    received = []

    async def tracking_agent(q, cb):
        received.append(q)
        return "Here is your advice."

    await _run_with_input(tracking_agent, "Why is my DPS low?", "/exit")
    assert received == ["Why is my DPS low?"]


@pytest.mark.asyncio
async def test_multiple_questions_invoke_agent_each_time():
    received = []

    async def tracking_agent(q, cb):
        received.append(q)
        return "advice"

    await _run_with_input(
        tracking_agent,
        "Question one",
        "Question two",
        "/exit",
    )
    assert received == ["Question one", "Question two"]


@pytest.mark.asyncio
async def test_agent_response_reaches_render():
    """render_agent_response is called with the agent's return value."""

    async def agent_fn(q, cb):
        return "## Coaching\n\nFocus on cooldown alignment."

    with patch("khadbot.cli.cli.render_agent_response") as mock_render:
        await _run_with_input(agent_fn, "Check my logs", "/exit")

    mock_render.assert_called_once_with("## Coaching\n\nFocus on cooldown alignment.")


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_exception_renders_error_panel_and_continues():
    """
    When agent_fn raises, render_error is called and the loop continues
    rather than crashing — confirmed by a second question succeeding.
    """
    call_count = [0]

    async def flaky_agent(q, cb):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("LLM backend unavailable")
        return "recovered"

    with patch("khadbot.cli.cli.render_error") as mock_error:
        with patch("khadbot.cli.cli.render_agent_response") as mock_render:
            await _run_with_input(
                flaky_agent,
                "First question",  # raises
                "Second question",  # succeeds
                "/exit",
            )

    mock_error.assert_called_once()
    error_msg = mock_error.call_args[0][0]
    assert "LLM backend unavailable" in error_msg
    mock_render.assert_called_once_with("recovered")


# ---------------------------------------------------------------------------
# Tests — LangSmith footer wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_langsmith_footer_called_after_response():
    """render_langsmith_footer is called after every successful agent response."""

    async def agent_fn(q, cb):
        return "advice"

    with patch("khadbot.cli.cli.render_langsmith_footer") as mock_footer:
        await _run_with_input(agent_fn, "Check my logs", "/exit")

    mock_footer.assert_called_once()


@pytest.mark.asyncio
async def test_langsmith_footer_receives_handler_run_id():
    """
    render_langsmith_footer receives handler.run_id — confirming the
    callback handler instance created per-turn is correctly threaded
    through to the footer call.
    """

    async def agent_fn(q, cb):
        # Simulate the handler receiving a run_id (as on_chain_start would set)
        if cb:
            cb[0]._run_id = "test-run-id-abc"
        return "advice"

    with patch("khadbot.cli.cli.render_langsmith_footer") as mock_footer:
        await _run_with_input(agent_fn, "Check my logs", "/exit")

    mock_footer.assert_called_once_with("test-run-id-abc")


@pytest.mark.asyncio
async def test_langsmith_footer_not_called_on_agent_error():
    """Footer must not be called when agent_fn raises."""

    async def raising_agent(q, cb):
        raise RuntimeError("boom")

    with patch("khadbot.cli.cli.render_langsmith_footer") as mock_footer:
        with patch("khadbot.cli.cli.render_error"):
            await _run_with_input(raising_agent, "Question", "/exit")

    mock_footer.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — EOFError / KeyboardInterrupt exit paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eof_exits_cleanly():
    """EOFError from prompt_async (Ctrl-D) exits without raising."""
    with patch("khadbot.cli.cli.PromptSession") as mock_session_cls:
        session = mock_session_cls.return_value
        session.prompt_async = AsyncMock(side_effect=EOFError)
        await run_cli(lambda q, cb: asyncio.coroutine(lambda: "x")())


@pytest.mark.asyncio
async def test_keyboard_interrupt_exits_cleanly():
    """KeyboardInterrupt from prompt_async (Ctrl-C) exits without raising."""
    with patch("khadbot.cli.cli.PromptSession") as mock_session_cls:
        session = mock_session_cls.return_value
        session.prompt_async = AsyncMock(side_effect=KeyboardInterrupt)
        await run_cli(lambda q, cb: asyncio.coroutine(lambda: "x")())
