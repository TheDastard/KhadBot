"""
tests/unit/encounter/analyst/test_usage_callback.py

The usage callback: it reports each model call's ``usage_metadata`` and the model name the reply gives, skips replies
without usage, and never lets a failing sink break a review.
"""

from __future__ import annotations

import pytest
from helpers.encounter.review_fakes import respond
from helpers.encounter.usage_fake import USAGE, UsageFakeModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from khadbot.encounter.analyst import UsageCallback, review_pull
from khadbot.encounter.analyst.usage import reported_model
from khadbot.encounter.findings import SqliteFindingsStore


def result(message: AIMessage) -> LLMResult:
    return LLMResult(generations=[[ChatGeneration(message=message)]])


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"model_name": "a"}, "a"),
        ({"model": "b"}, "b"),
        ({"model_name": "a", "model": "b"}, "a"),
        ({}, "dflt"),
        (None, "dflt"),
    ],
)
def test_the_reported_model_name(meta, expected):
    assert reported_model(meta, "dflt") == expected


async def test_each_reply_with_usage_is_reported_with_its_model():
    seen: list = []

    async def sink(model, usage):
        seen.append((model, dict(usage)))

    cb = UsageCallback(sink, default_model="dflt")
    await cb.on_llm_end(result(AIMessage(content="x", usage_metadata=USAGE, response_metadata={"model_name": "m1"})))
    await cb.on_llm_end(result(AIMessage(content="y", usage_metadata=USAGE)))
    await cb.on_llm_end(result(AIMessage(content="no usage")))
    assert seen == [("m1", dict(USAGE)), ("dflt", dict(USAGE))]


async def test_a_failing_sink_is_swallowed():
    async def sink(model, usage):
        raise RuntimeError("ledger down")

    cb = UsageCallback(sink, default_model="m")
    await cb.on_llm_end(result(AIMessage(content="x", usage_metadata=USAGE)))  # does not raise


async def test_a_review_reports_every_model_call(role_fakes, synth_data, tmp_path):
    model = UsageFakeModel(respond=respond, model_name="test-analyst")
    role_fakes["analyst"] = model
    seen: list = []

    async def sink(name, usage):
        seen.append((name, usage["input_tokens"], usage["input_token_details"]["cache_read"]))

    out = await review_pull(
        "SYNTH01:1",
        store=synth_data,
        findings=SqliteFindingsStore(tmp_path / "f.sqlite"),
        callbacks=[UsageCallback(sink, default_model="x")],
    )
    assert not out.cached
    assert len(seen) == model.calls > 0
    assert set(seen) == {("test-analyst", 1000, 400)}
