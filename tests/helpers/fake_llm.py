"""
tests/helpers/fake_llm.py

Tool-capable fake chat model. GenericFakeChatModel has no ``bind_tools``, so any
graph that binds tools to a model needs this wrapper.

The fake also records what it was given, so scenario tests can assert on it:
``bind_history`` holds the tool names of every ``bind_tools`` call and ``seen`` the
message list of every model call, in order.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import Field


class FakeToolModel(GenericFakeChatModel):
    """Scripted chat model whose ``bind_tools`` records tool names and returns itself."""

    bound_tools: list[str] = Field(default_factory=list)
    bind_history: list[list[str]] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeToolModel:
        names = []
        for tool in tools:
            names.append(getattr(tool, "name", None) or getattr(tool, "__name__", None) or tool["name"])
        self.bound_tools = names
        self.bind_history.append(names)
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def make_fake(messages: Iterable[AIMessage | str]) -> FakeToolModel:
    """Build a FakeToolModel that replays ``messages`` in order."""
    return FakeToolModel(messages=iter([AIMessage(content=m) if isinstance(m, str) else m for m in messages]))


class ReactiveFakeModel:
    """
    Stateless fake chat model: ``respond(messages, bound_tool_names)`` picks each reply.

    Use it when calls can interleave (parallel analyses), where a scripted iterator would hand replies
    to the wrong caller. It records every call in ``calls`` as ``(bound tool names, messages)`` and the
    peak number of calls in flight in ``peak``; ``delay`` holds each call open so overlap is observable.
    ``bind_tools`` returns a view that shares this model's records.
    """

    def __init__(self, respond: Any, *, model_name: str = "reactive-fake", delay: float = 0.0) -> None:
        self.respond = respond
        self.model_name = model_name
        self.delay = delay
        self.calls: list[tuple[list[str], list[BaseMessage]]] = []
        self.in_flight = 0
        self.peak = 0

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> _BoundReactive:
        return _BoundReactive(self, [getattr(t, "name", None) or t["name"] for t in tools])


class _BoundReactive:
    def __init__(self, root: ReactiveFakeModel, tool_names: list[str]) -> None:
        self.root = root
        self.tool_names = tool_names

    async def ainvoke(self, messages: list[BaseMessage], config: Any = None, **kwargs: Any) -> AIMessage:
        root = self.root
        root.calls.append((self.tool_names, list(messages)))
        root.in_flight += 1
        root.peak = max(root.peak, root.in_flight)
        try:
            if root.delay:
                await asyncio.sleep(root.delay)
            return root.respond(messages, self.tool_names)
        finally:
            root.in_flight -= 1
