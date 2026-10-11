"""
tests/helpers/fake_llm.py

Tool-capable fake chat model. GenericFakeChatModel has no ``bind_tools``, so any
graph that binds tools to a model needs this wrapper.

The fake also records what it was given, so scenario tests can assert on it:
``bind_history`` holds the tool names of every ``bind_tools`` call and ``seen`` the
message list of every model call, in order.
"""

from __future__ import annotations

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
