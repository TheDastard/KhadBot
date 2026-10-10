"""
tests/helpers/fake_llm.py

Tool-capable fake chat model. GenericFakeChatModel has no ``bind_tools``, so any
graph that binds tools to a model needs this wrapper.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class FakeToolModel(GenericFakeChatModel):
    """Scripted chat model whose ``bind_tools`` records tool names and returns itself."""

    bound_tools: list[str] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeToolModel:
        names = []
        for tool in tools:
            names.append(getattr(tool, "name", None) or getattr(tool, "__name__", None) or tool["name"])
        self.bound_tools = names
        return self


def make_fake(messages: Iterable[AIMessage | str]) -> FakeToolModel:
    """Build a FakeToolModel that replays ``messages`` in order."""
    return FakeToolModel(messages=iter([AIMessage(content=m) if isinstance(m, str) else m for m in messages]))
