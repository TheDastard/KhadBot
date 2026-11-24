"""
tests/unit/encounter/analyst/test_usage_callback.py

The usage callback: it reports each model call's ``usage_metadata`` and the model name the reply gives, skips replies
without usage, and never lets a failing sink break a review.
"""

from __future__ import annotations

import logging
from uuid import uuid4

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


# ---------------------------------------------------------------------------
# llm.call records
# ---------------------------------------------------------------------------


def llm_calls(caplog) -> list:
    return [r for r in caplog.records if getattr(r, "event", None) == "llm.call"]


async def test_a_timed_call_logs_its_model_duration_and_tokens(caplog):
    caplog.set_level(logging.INFO, logger="khadbot.encounter.analyst.usage")
    seen: list = []

    async def sink(model, usage):
        seen.append(model)

    cb = UsageCallback(sink, default_model="dflt")
    run_id = uuid4()
    await cb.on_chat_model_start({}, [[]], run_id=run_id, metadata={"ls_model_name": "started-as"})
    message = AIMessage(content="secret reply", usage_metadata=USAGE, response_metadata={"model_name": "m1"})
    await cb.on_llm_end(result(message), run_id=run_id)
    (record,) = llm_calls(caplog)
    assert record.levelno == logging.INFO
    assert (record.model, record.input_tokens, record.output_tokens, record.outcome) == ("m1", 1000, 200, "ok")
    assert isinstance(record.duration_ms, int) and record.duration_ms >= 0
    assert "secret reply" not in record.getMessage()
    assert seen == ["m1"]  # the sink still sees the call


async def test_a_failed_call_logs_an_error_outcome(caplog):
    caplog.set_level(logging.INFO, logger="khadbot.encounter.analyst.usage")

    async def sink(model, usage):
        raise AssertionError("no usage on an error")

    cb = UsageCallback(sink, default_model="dflt")
    run_id = uuid4()
    await cb.on_llm_start({}, ["prompt"], run_id=run_id, metadata={"ls_model_name": "started-as"})
    await cb.on_llm_error(RuntimeError("provider down"), run_id=run_id)
    (record,) = llm_calls(caplog)
    assert (record.model, record.outcome) == ("started-as", "error")
    assert isinstance(record.duration_ms, int)
    assert not hasattr(record, "input_tokens")


async def test_an_unstarted_call_logs_without_a_duration(caplog):
    caplog.set_level(logging.INFO, logger="khadbot.encounter.analyst.usage")

    async def sink(model, usage):
        pass

    await UsageCallback(sink, default_model="dflt").on_llm_end(result(AIMessage(content="x")))
    (record,) = llm_calls(caplog)
    assert (record.model, record.outcome) == ("dflt", "ok")
    assert not hasattr(record, "duration_ms")


async def test_a_review_logs_one_llm_call_per_model_call(role_fakes, synth_data, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="khadbot.encounter.analyst.usage")
    model = UsageFakeModel(respond=respond, model_name="test-analyst")
    role_fakes["analyst"] = model

    async def sink(name, usage):
        pass

    await review_pull(
        "SYNTH01:1",
        store=synth_data,
        findings=SqliteFindingsStore(tmp_path / "f.sqlite"),
        callbacks=[UsageCallback(sink, default_model="x")],
    )
    records = llm_calls(caplog)
    assert len(records) == model.calls > 0
    assert all(r.outcome == "ok" and r.model == "test-analyst" and r.input_tokens == 1000 for r in records)
    assert all(isinstance(r.duration_ms, int) for r in records)
