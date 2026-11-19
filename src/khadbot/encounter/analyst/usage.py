"""
encounter/analyst/usage.py

A LangChain callback that reports every model call's token usage to a sink the caller supplies. The analyst graph
passes its run config to each model call, so a handler given to ``review_pull(callbacks=[...])`` sees every call of a
review: the per-pull investigation rounds and the verify retry.

The handler reads the standard ``AIMessage.usage_metadata`` of each reply (``input_tokens``, ``output_tokens`` and
``input_token_details`` with the cache counts) and the model name the reply reports in ``response_metadata``
(``model_name``, else ``model``). It knows nothing about ledgers or jobs: ``khadbot.jobs`` supplies a sink that writes
the usage ledger. A reply without usage metadata is skipped, and a failing sink is logged, never raised, so recording
usage can't fail a review.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import ChatGeneration, LLMResult

logger = logging.getLogger(__name__)

UsageSink = Callable[[str, Mapping[str, Any]], Awaitable[None]]
"""Called with ``(model name, usage_metadata)`` once per model call."""

# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


def reported_model(response_metadata: Mapping[str, Any] | None, default: str) -> str:
    """The model name a reply reports (``model_name``, else ``model``), or ``default`` when it names none."""
    meta = response_metadata or {}
    for key in ("model_name", "model"):
        value = meta.get(key)
        if isinstance(value, str) and value:
            return value
    return default


class UsageCallback(AsyncCallbackHandler):
    """
    Reports each model call's usage to ``sink``.

    Args:
        sink: Receives ``(model, usage_metadata)`` per call.
        default_model: The name to report when a reply names no model.
    """

    raise_error = False

    def __init__(self, sink: UsageSink, default_model: str) -> None:
        self._sink = sink
        self._default_model = default_model

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                if not isinstance(generation, ChatGeneration):
                    continue
                message = generation.message
                usage = getattr(message, "usage_metadata", None)
                if not usage:
                    continue
                model = reported_model(getattr(message, "response_metadata", None), self._default_model)
                try:
                    await self._sink(model, usage)
                except Exception:  # noqa: BLE001 - recording usage must never fail the review
                    logger.warning("Could not record a model call's usage", exc_info=True)
