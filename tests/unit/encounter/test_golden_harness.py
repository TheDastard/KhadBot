"""
tests/unit/encounter/test_golden_harness.py

The golden-question harness used by tests/eval: question schema validation, the committed
question file, the deterministic pass checks, pass-rate aggregation, and one end-to-end run of a
SYNTH01 question through the analyst with a scripted model.
"""

from __future__ import annotations

import pytest
import yaml
from helpers.encounter.golden import (
    QUESTIONS_FILE,
    GoldenOutcome,
    GoldenQuestion,
    check_report,
    load_questions,
    min_pass_rate,
    pass_rate,
)
from helpers.fake_llm import make_fake
from langchain_core.messages import AIMessage

from khadbot.encounter.analyst import run_analysis
from khadbot.encounter.models import AnalysisReport, AnalysisRequest, Finding, ProducedBy

D1 = "ev:SYNTH01:1:death:1"
ANSWERABLE = {
    "id": "q01",
    "question": "Why did Alphadps die first on pull 1?",
    "pulls": ["SYNTH01:1"],
    "kind": "single_pull",
    "expected_findings": ["Died to Synth Swirl"],
    "expected_refs_any": [D1],
}
UNANSWERABLE = {
    "id": "q02",
    "question": "Did the tanks swap on time?",
    "pulls": ["SYNTH01:1"],
    "kind": "unanswerable",
    "must_be_unknown": True,
}


def write_questions(tmp_path, items) -> object:
    path = tmp_path / "questions.yaml"
    path.write_text(yaml.safe_dump(items), encoding="utf-8")
    return path


def report(*findings: Finding, unknowns: list[str] | None = None) -> AnalysisReport:
    return AnalysisReport(
        request_id="r",
        findings=list(findings),
        unknowns=unknowns or [],
        partial=False,
        produced_by=ProducedBy(
            model_id="m", prompt_version="p", graph_version="1", encfile_hash=None, events_hashes={}
        ),
    )


def finding(*refs: str) -> Finding:
    return Finding(id="r-1", claim="x", refs=list(refs), confidence="high", scope="pull")


# ===========================================================================
# Schema
# ===========================================================================


def test_committed_question_file_is_valid():
    assert isinstance(load_questions(QUESTIONS_FILE), list)


def test_load_valid_questions(tmp_path):
    questions = load_questions(write_questions(tmp_path, [ANSWERABLE, UNANSWERABLE]))
    assert [q.id for q in questions] == ["q01", "q02"]
    assert questions[0].depth == "standard" and questions[1].must_be_unknown


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "unanswerable", "must_be_unknown": False}, "need must_be_unknown"),
        ({"pulls": ["SYNTH01"]}, "is not a pull key"),
        ({"pulls": []}, "at least 1"),
        ({"expected_refs_any": ["death:1"]}, "malformed ref"),
        ({"expected_refs_any": []}, "need expected_refs_any"),
        ({"must_be_unknown": True}, "cannot expect refs"),
        ({"kind": "trivia"}, "kind"),
        ({"surprise": 1}, "surprise"),
        ({"depth": "bottomless"}, "depth"),
    ],
)
def test_invalid_questions(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        load_questions(write_questions(tmp_path, [{**ANSWERABLE, **change}]))


def test_duplicate_ids(tmp_path):
    with pytest.raises(ValueError, match="duplicate question id 'q01'"):
        load_questions(write_questions(tmp_path, [ANSWERABLE, ANSWERABLE]))


def test_question_file_must_be_a_list(tmp_path):
    with pytest.raises(ValueError, match="expected a YAML list"):
        load_questions(write_questions(tmp_path, {"questions": []}))


# ===========================================================================
# Checks
# ===========================================================================


def test_answerable_passes_when_an_expected_ref_is_cited(synth_data):
    q = GoldenQuestion.model_validate(ANSWERABLE)
    outcome = check_report(q, report(finding(D1, "ev:SYNTH01:1:pull")), synth_data)
    assert (outcome.passed, outcome.cited_refs, outcome.reasons) == (True, [D1, "ev:SYNTH01:1:pull"], [])


def test_answerable_fails_without_expected_ref(synth_data):
    q = GoldenQuestion.model_validate(ANSWERABLE)
    outcome = check_report(q, report(finding("ev:SYNTH01:1:death:2")), synth_data)
    assert not outcome.passed and outcome.reasons == [f"cited none of {D1}"]
    assert not check_report(q, report(unknowns=["no idea"]), synth_data).passed


def test_unresolved_refs_fail(synth_data):
    q = GoldenQuestion.model_validate(ANSWERABLE)
    outcome = check_report(q, report(finding(D1, "ev:SYNTH01:1:death:99")), synth_data)
    assert outcome.unresolved_refs == ["ev:SYNTH01:1:death:99"] and not outcome.passed


def test_must_be_unknown(synth_data):
    q = GoldenQuestion.model_validate(UNANSWERABLE)
    assert check_report(q, report(unknowns=["no tank swap mechanic"]), synth_data).passed
    with_finding = check_report(q, report(finding(D1), unknowns=["n/a"]), synth_data)
    assert with_finding.reasons == ["expected no findings, got 1"]
    assert check_report(q, report(), synth_data).reasons == ["expected unknowns explaining why the data cannot answer"]


def test_pass_rate_per_model():
    outcomes = [
        GoldenOutcome("q1", "groq", True, [], []),
        GoldenOutcome("q2", "groq", False, [], []),
        GoldenOutcome("q1", "sonnet", True, [], []),
    ]
    assert pass_rate(outcomes) == {"groq": (1, 2), "sonnet": (1, 1)}


def test_min_pass_rate(monkeypatch):
    monkeypatch.delenv("KHADBOT_EVAL_MIN_PASS_RATE", raising=False)
    assert min_pass_rate() is None
    monkeypatch.setenv("KHADBOT_EVAL_MIN_PASS_RATE", "0.7")
    assert min_pass_rate() == 0.7


# ===========================================================================
# End to end with a scripted analyst
# ===========================================================================


async def test_question_through_scripted_analyst(synth_data, role_fakes):
    q = GoldenQuestion.model_validate(ANSWERABLE)
    submit = {
        "name": "submit_report",
        "args": {"findings": [{"claim": "Alphadps died to Synth Swirl at 8.0s.", "refs": [D1], "confidence": "high"}]},
        "id": "s1",
        "type": "tool_call",
    }
    role_fakes["analyst"] = make_fake([AIMessage(content="", tool_calls=[submit])])
    req = AnalysisRequest(request_id="0" * 32, question=q.question, pulls=q.pulls, depth=q.depth)
    outcome = check_report(q, await run_analysis(req, store=synth_data), synth_data)
    assert outcome.passed and outcome.model_id == "FakeToolModel"
