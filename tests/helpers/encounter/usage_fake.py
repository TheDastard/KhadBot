"""
tests/helpers/encounter/usage_fake.py

A chat model that plays the analyst like ``ReactiveFakeModel`` but is a real LangChain chat model, so callbacks run:
each reply carries ``usage_metadata`` (fixed counts, with cache details) and ``response_metadata["model_name"]``, as a
provider's reply does. For tests of what reads them: the usage callback and the usage ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

USAGE = {
    "input_tokens": 1000,
    "output_tokens": 200,
    "total_tokens": 1200,
    "input_token_details": {"cache_read": 400, "cache_creation": 100},
}


class UsageFakeModel(BaseChatModel):
    """``respond(messages, tool names)`` picks each reply; every reply reports ``USAGE`` under ``model_name``."""

    respond: Callable[[list[BaseMessage], list[str]], AIMessage]
    model_name: str = "test-analyst"
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "usage-fake"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> UsageFakeModel:
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        self.calls += 1
        reply = self.respond(messages, [])
        reply = reply.model_copy(update={"usage_metadata": USAGE, "response_metadata": {"model_name": self.model_name}})
        return ChatResult(generations=[ChatGeneration(message=reply)])
