"""
tests/unit/cli/test_command_hook.py

The ``commands`` hook on run_cli: registered slash commands run their handler (not the agent),
errors are rendered and the loop continues, built-ins win, and /help lists registered commands.
PromptSession is scripted; no TTY, no agent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from khadbot.cli.cli import _help_text, run_cli


async def _run(commands, *lines: str):
    agent_calls: list[str] = []

    async def agent(question: str, callbacks: list) -> str:
        agent_calls.append(question)
        return "agent answer"

    script = iter(lines)

    async def prompt(*args, **kwargs):
        try:
            return next(script)
        except StopIteration:
            raise EOFError from None

    with (
        patch("khadbot.cli.cli.PromptSession") as session_cls,
        patch("khadbot.cli.cli.render_agent_response") as render,
        patch("khadbot.cli.cli.render_error") as render_error,
    ):
        session_cls.return_value.prompt_async = prompt
        await run_cli(agent, commands=commands)
    return agent_calls, render, render_error


@pytest.mark.asyncio
async def test_registered_command_runs_handler_with_arg_text_and_skips_agent():
    seen = []

    async def ingest(arg: str) -> str:
        seen.append(arg)
        return "## done"

    agent_calls, render, _ = await _run({"/ingest": ingest}, "/ingest ABC --fights 1,2")
    assert seen == ["ABC --fights 1,2"]
    assert agent_calls == []
    render.assert_called_once_with("## done")


@pytest.mark.asyncio
async def test_command_name_is_case_insensitive():
    seen = []

    async def ingest(arg: str) -> None:
        seen.append(arg)

    await _run({"/Ingest": ingest}, "/INGEST x")
    assert seen == ["x"]


@pytest.mark.asyncio
async def test_none_result_renders_nothing():
    async def quiet(arg: str) -> None:
        return None

    _, render, _ = await _run({"/quiet": quiet}, "/quiet")
    render.assert_not_called()


@pytest.mark.asyncio
async def test_handler_error_is_rendered_and_loop_continues():
    async def boom(arg: str) -> str:
        raise RuntimeError("kaput")

    agent_calls, _, render_error = await _run({"/boom": boom}, "/boom", "a question")
    render_error.assert_called_once()
    assert "kaput" in render_error.call_args.args[0]
    assert agent_calls == ["a question"]


@pytest.mark.asyncio
async def test_unregistered_slash_text_still_goes_to_the_agent():
    agent_calls, _, _ = await _run({"/ingest": None}, "/unknown thing")
    assert agent_calls == ["/unknown thing"]


@pytest.mark.asyncio
async def test_builtin_exit_beats_registered_command():
    called = []

    async def sneaky(arg: str) -> None:
        called.append(arg)

    await _run({"/exit": sneaky}, "/exit")
    assert called == []


def test_help_lists_registered_commands_with_first_doc_line():
    async def ingest(arg: str) -> None:
        """Ingest a report.

        More detail.
        """

    text = _help_text({"/ingest": ingest})
    assert "/ingest" in text and "Ingest a report." in text and "More detail" not in text


def test_help_without_commands_is_unchanged():
    assert "Registered commands" not in _help_text({})
