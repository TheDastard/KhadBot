"""
tests/eval/conftest.py

Fixtures for the golden-question eval: the question set, the recorded reports it needs ingested
offline into a session store, and an outcome collector that prints the pass rate per model.

These tests call a real LLM (the analyst role from ``llm_factory``) and cost money on hosted
providers, so they run only when selected: ``uv run pytest tests/eval -m eval`` (``make test-eval``).

Environment:
    KHADBOT_EVAL_MIN_PASS_RATE   Fail the run below this pass rate (0-1). Unset: report only.
    KHADBOT_GOLDEN_QUESTIONS     Question file to use instead of tests/golden/questions.yaml.
    LANGSMITH_API_KEY            When set, each question's pass/fail is attached to its traced run
                                 as LangSmith feedback (key ``golden_pass``).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict
from pathlib import Path

import orjson
import pytest
from helpers.golden import GoldenOutcome, GoldenQuestion, load_questions, pass_rate
from helpers.synth import (
    FIXTURES,
    SYNTH_ENCOUNTER_FILE,
    SYNTH_ENCOUNTER_ID,
    SYNTH_ENEMY_CAST_IDS,
    SYNTH_REGISTRY_FILE,
    SYNTH_REGISTRY_IDS,
)

from khadbot.config import EncounterConfig
from khadbot.encounter.config_files import load_registry
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.fetch import RawPageCache
from khadbot.encounter.ingest import ingest_report
from khadbot.encounter.store import ParquetEventStore

_OUTCOMES: list[GoldenOutcome] = []


@pytest.fixture(scope="session")
def golden_questions() -> list[GoldenQuestion]:
    return load_questions()


def _ingest(code: str, store: ParquetEventStore) -> None:
    if not (FIXTURES / "wcl" / code).is_dir():
        pytest.fail(f"golden questions need report {code}, but tests/fixtures/wcl/{code} is not recorded")
    if code == "SYNTH01":
        filters = {"registry_spell_ids": SYNTH_REGISTRY_IDS, "enemy_cast_ids": SYNTH_ENEMY_CAST_IDS}
    else:
        from khadbot.encounter.commands import default_filter_kwargs

        filters = default_filter_kwargs()
    result = asyncio.run(
        ingest_report(code, store=store, cache=RawPageCache(FIXTURES / "wcl"), offline=True, **filters)
    )
    if result.failed:
        pytest.fail(f"ingesting {code} failed: {result.failed}")


@pytest.fixture(scope="session")
def eval_data(golden_questions, tmp_path_factory) -> dict[str, EncounterData]:
    """``{report_code: EncounterData}`` for every report the questions mention, ingested offline."""
    root = tmp_path_factory.mktemp("eval")
    store = ParquetEventStore(root / "events")
    codes = sorted({p.rpartition(":")[0] for q in golden_questions for p in q.pulls})
    data: dict[str, EncounterData] = {}
    for code in codes:
        _ingest(code, store)
        if code == "SYNTH01":  # synthetic report: its own encounter file and registry
            enc_root = root / "synth_encounters"
            enc_root.mkdir(exist_ok=True)
            (enc_root / f"{SYNTH_ENCOUNTER_ID}.yaml").write_bytes(SYNTH_ENCOUNTER_FILE.read_bytes())
            data[code] = EncounterData(store, enc_root=enc_root, registry=load_registry(SYNTH_REGISTRY_FILE))
        else:
            data[code] = EncounterData(store)
    return data


@pytest.fixture(scope="session")
def record_outcome():
    def record(outcome: GoldenOutcome, run_id) -> None:
        _OUTCOMES.append(outcome)
        if os.environ.get("LANGSMITH_API_KEY"):
            _send_feedback(outcome, run_id)

    return record


@pytest.fixture(scope="session")
def outcomes() -> list[GoldenOutcome]:
    return _OUTCOMES


def _send_feedback(outcome: GoldenOutcome, run_id) -> None:
    try:
        from langsmith import Client

        Client().create_feedback(
            run_id,
            key="golden_pass",
            score=1.0 if outcome.passed else 0.0,
            comment=f"{outcome.question_id}: {'; '.join(outcome.reasons) or 'pass'}",
        )
    except Exception as e:  # feedback is best effort; the eval result stands without it
        print(f"LangSmith feedback for {outcome.question_id} failed: {e}")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _OUTCOMES:
        return
    terminalreporter.section("golden eval")
    for model_id, (passed, total) in sorted(pass_rate(_OUTCOMES).items()):
        unresolved = sum(len(o.unresolved_refs) for o in _OUTCOMES if o.model_id == model_id)
        cited = sum(len(o.cited_refs) for o in _OUTCOMES if o.model_id == model_id)
        terminalreporter.write_line(
            f"{model_id}: {passed}/{total} passed ({passed / total:.0%}); refs resolved {cited - unresolved}/{cited}"
        )
    for o in _OUTCOMES:
        if not o.passed:
            terminalreporter.write_line(f"  FAIL {o.question_id} [{o.model_id}]: {'; '.join(o.reasons)}")
    out = Path(EncounterConfig().data_dir) / "eval" / f"golden-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(orjson.dumps([asdict(o) for o in _OUTCOMES], option=orjson.OPT_INDENT_2))
    terminalreporter.write_line(f"results written to {out}")
