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

It also times each call, from ``on_chat_model_start`` (or ``on_llm_start``) to ``on_llm_end`` or ``on_llm_error``,
matched by ``run_id``, and logs one ``llm.call`` record per call with the model, duration, token counts and outcome
(``ok`` or ``error``). Never the prompt or the reply. Logging is guarded like the sink: it can't fail a model call.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

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
    Reports each model call's usage to ``sink`` and logs an ``llm.call`` record per call.

    Args:
        sink: Receives ``(model, usage_metadata)`` per call.
        default_model: The name to report when a reply names no model.
    """

    raise_error = False

    def __init__(self, sink: UsageSink, default_model: str) -> None:
        self._sink = sink
        self._default_model = default_model
        # run_id -> (perf_counter at start, the model name the call was started with)
        self._started: dict[UUID, tuple[float, str]] = {}

    async def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        self._start(kwargs)

    async def on_llm_start(self, serialized: dict[str, Any], prompts: Any, **kwargs: Any) -> None:
        self._start(kwargs)

    async def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        try:
            started, model = self._finish(kwargs)
            self._log_call(model, started, None, None, "error")
        except Exception:  # noqa: BLE001 - logging must never fail the model call
            pass

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        await self._record_usage(response)
        try:
            started, model = self._finish(kwargs)
            input_tokens, output_tokens, reported = _usage_totals(response)
            self._log_call(reported or model, started, input_tokens, output_tokens, "ok")
        except Exception:  # noqa: BLE001 - logging must never fail the model call
            pass

    # ------------------------------------------------------------------
    # Timing and the llm.call record
    # ------------------------------------------------------------------

    def _start(self, kwargs: Mapping[str, Any]) -> None:
        try:
            run_id = kwargs.get("run_id")
            if run_id is None:
                return
            metadata = kwargs.get("metadata") or {}
            model = metadata.get("ls_model_name")
            self._started[run_id] = (time.perf_counter(), model if isinstance(model, str) and model else "")
        except Exception:  # noqa: BLE001 - logging must never fail the model call
            pass

    def _finish(self, kwargs: Mapping[str, Any]) -> tuple[float | None, str]:
        """The start time and model name recorded for this call's ``run_id``, forgetting them; None when unseen."""
        run_id = kwargs.get("run_id")
        entry = self._started.pop(run_id, None) if run_id is not None else None
        if entry is None:
            return None, self._default_model
        return entry[0], entry[1] or self._default_model

    def _log_call(
        self, model: str, started: float | None, input_tokens: int | None, output_tokens: int | None, outcome: str
    ) -> None:
        extra: dict[str, Any] = {"event": "llm.call", "model": model, "outcome": outcome}
        if started is not None:
            extra["duration_ms"] = round((time.perf_counter() - started) * 1000)
        if input_tokens is not None:
            extra["input_tokens"] = input_tokens
        if output_tokens is not None:
            extra["output_tokens"] = output_tokens
        duration = f" in {extra['duration_ms']} ms" if "duration_ms" in extra else ""
        logger.info("Model call %s %s%s", model, outcome, duration, extra=extra)

    # ------------------------------------------------------------------
    # Usage sink
    # ------------------------------------------------------------------

    async def _record_usage(self, response: LLMResult) -> None:
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


def _usage_totals(response: LLMResult) -> tuple[int | None, int | None, str]:
    """Summed input and output tokens over a response's replies (None when none reports usage) and the model named."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    model = ""
    for generations in response.generations:
        for generation in generations:
            if not isinstance(generation, ChatGeneration):
                continue
            message = generation.message
            usage = getattr(message, "usage_metadata", None)
            if usage:
                input_tokens = (input_tokens or 0) + int(usage.get("input_tokens") or 0)
                output_tokens = (output_tokens or 0) + int(usage.get("output_tokens") or 0)
            model = model or reported_model(getattr(message, "response_metadata", None), "")
    return input_tokens, output_tokens, model
