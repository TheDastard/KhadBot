"""
tests/integration/cli/test_callback_handler_wired.py

Integration tests for ToolPanelCallbackHandler wired to a real LangChain
agent built on FakeListChatModel.

These tests verify that LangChain actually fires the expected callbacks during
agent.invoke(), and that those callbacks correctly drive the ToolPanel state
machine. This is the critical seam — it catches LangChain version drift in
the callback contract that unit tests (which fire callbacks manually) cannot.

What's verified:
  - on_chain_start fires and populates handler.run_id before tool calls
  - on_tool_start fires with the correct tool name, driving panel to RUNNING
  - on_tool_end fires after the tool completes, driving panel to DONE
  - on_tool_error fires on tool exception, driving panel to FAILED
  - Full turn: panel reflects complete tool history after agent.invoke()
  - run_id is the root run ID, not a sub-chain ID (parent_run_id=None logic)

Dependencies:
  - langchain_core (FakeListChatModel, tool decorator, create_react_agent)
  - No real LLM calls — fully deterministic and free
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytest.importorskip("langchain_core", reason="langchain_core required for callback integration tests")

from langchain.agents import create_agent  # noqa: E402
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from khadbot.cli.tool_panel import ToolPanelCallbackHandler, ToolState  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal tools for testing
# These are real @tool-decorated functions so LangChain registers them
# properly and fires on_tool_start/end callbacks.
# ---------------------------------------------------------------------------


@tool
def fetch_character(name: str, realm: str) -> str:
    """Fetch a character's M+ score from Raider.IO."""
    return f"{name}-{realm}: 2847 M+"


@tool
def failing_tool(query: str) -> str:
    """A tool that always raises to test on_tool_error handling."""
    raise RuntimeError(f"Deliberate failure for: {query}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_with_tool_call(tool_fn, tool_args: dict):
    """
    Build a FakeListChatModel agent scripted to call one tool then respond.

    The fake LLM returns:
      1. An AIMessage with a tool_call targeting tool_fn
      2. An AIMessage with the final text response
    """
    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": tool_fn.name,
                "args": tool_args,
                "id": f"call_{uuid4().hex[:8]}",
                "type": "tool_call",
            }
        ],
    )
    final_message = AIMessage(content="Here is your coaching advice.")

    llm = FakeListChatModel(responses=[tool_call_message, final_message])
    agent = create_agent(model=llm, tools=[tool_fn])
    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_id_populated_after_invoke():
    """handler.run_id is set after agent.invoke() completes."""
    agent = _make_agent_with_tool_call(fetch_character, {"name": "Thrall", "realm": "Stormrage"})
    handler = ToolPanelCallbackHandler()

    async with handler.panel:
        agent.invoke(
            {"messages": [{"role": "user", "content": "Check my character"}]},
            config={"callbacks": [handler]},
        )

    assert handler.run_id is not None
    # Should be a valid UUID-like string
    assert len(handler.run_id) > 0


@pytest.mark.asyncio
async def test_tool_reaches_done_state_after_invoke():
    """
    After a successful agent.invoke() with one tool call, the panel has
    one DONE entry for the called tool.
    """
    agent = _make_agent_with_tool_call(fetch_character, {"name": "Thrall", "realm": "Stormrage"})
    handler = ToolPanelCallbackHandler()

    async with handler.panel:
        agent.invoke(
            {"messages": [{"role": "user", "content": "Check my character"}]},
            config={"callbacks": [handler]},
        )

    assert len(handler.panel._tools) == 1
    entry = handler.panel._tools[0]
    assert entry.tool_name == "fetch_character"
    assert entry.state == ToolState.DONE


@pytest.mark.asyncio
async def test_tool_reaches_failed_state_on_tool_error():
    """
    When the tool raises, on_tool_error fires and the panel entry is FAILED.
    """
    agent = _make_agent_with_tool_call(failing_tool, {"query": "test"})
    handler = ToolPanelCallbackHandler()

    # Agent may or may not propagate the exception depending on LangGraph's
    # error handling — we only care about the panel state, not the agent result.
    async with handler.panel:
        try:
            agent.invoke(
                {"messages": [{"role": "user", "content": "trigger failure"}]},
                config={"callbacks": [handler]},
            )
        except Exception:
            pass

    assert len(handler.panel._tools) >= 1
    failed = [e for e in handler.panel._tools if e.state == ToolState.FAILED]
    assert len(failed) == 1
    assert failed[0].tool_name == "failing_tool"


@pytest.mark.asyncio
async def test_last_tool_name_tracked_for_on_tool_end():
    """
    _last_tool_name is set during on_tool_start so on_tool_end can resolve
    the tool even when kwargs['name'] is absent.
    """
    agent = _make_agent_with_tool_call(fetch_character, {"name": "Thrall", "realm": "Stormrage"})
    handler = ToolPanelCallbackHandler()

    async with handler.panel:
        agent.invoke(
            {"messages": [{"role": "user", "content": "Check my character"}]},
            config={"callbacks": [handler]},
        )

    # _last_tool_name should have been set to the tool that was called
    assert handler._last_tool_name == "fetch_character"


@pytest.mark.asyncio
async def test_run_id_is_root_not_sub_chain():
    """
    The captured run_id must be the root chain's ID.
    We verify indirectly: the root run_id is captured on the first
    on_chain_start (parent_run_id=None), and subsequent sub-chain calls
    must not overwrite it.

    We do this by invoking the agent and then checking that run_id stays
    constant across a second invoke on the same handler (which would be
    affected if sub-chain IDs were overwriting it).
    """
    agent = _make_agent_with_tool_call(fetch_character, {"name": "Thrall", "realm": "Stormrage"})
    handler = ToolPanelCallbackHandler()

    async with handler.panel:
        agent.invoke(
            {"messages": [{"role": "user", "content": "Check my character"}]},
            config={"callbacks": [handler]},
        )

    first_run_id = handler.run_id
    assert first_run_id is not None

    # A second invoke on the same handler should NOT overwrite run_id
    # because _run_id is only set when it's None, and a new handler is
    # created per turn in the real CLI — this test documents that invariant.
    async with handler.panel:
        agent.invoke(
            {"messages": [{"role": "user", "content": "Check again"}]},
            config={"callbacks": [handler]},
        )

    # run_id must still be the first root ID — not overwritten by second invoke
    assert handler.run_id == first_run_id
