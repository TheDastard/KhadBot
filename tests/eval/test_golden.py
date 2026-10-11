"""
tests/eval/test_golden.py

Golden-question eval for the analyst. Each question runs the analyst graph with the real
analyst-role model on recorded reports, then applies the deterministic checks from
tests/helpers/golden.py. Cited refs must always resolve; the pass rate per model is printed at
the end and gated by KHADBOT_EVAL_MIN_PASS_RATE when it is set.

Run deliberately: ``uv run pytest tests/eval -m eval`` (``make test-eval``).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from helpers.golden import check_report, load_questions, min_pass_rate, pass_rate

from khadbot.encounter.analyst import run_analysis
from khadbot.encounter.models import AnalysisRequest

QUESTIONS = load_questions()

pytestmark = pytest.mark.eval


@pytest.mark.skipif(not QUESTIONS, reason="tests/golden/questions.yaml has no questions yet")
@pytest.mark.parametrize("q", QUESTIONS, ids=[q.id for q in QUESTIONS])
async def test_golden_question(q, eval_data, record_outcome):
    data = eval_data[q.pulls[0].rpartition(":")[0]]
    req = AnalysisRequest(request_id=uuid4().hex, question=q.question, pulls=list(q.pulls), depth=q.depth)
    run_id = uuid4()
    report = await run_analysis(req, store=data, run_id=run_id)
    outcome = check_report(q, report, data)
    record_outcome(outcome, run_id)
    # Verify drops unresolvable refs, so this holds on every model; a failure means verify regressed.
    assert not outcome.unresolved_refs, outcome.reasons


@pytest.mark.skipif(not QUESTIONS, reason="tests/golden/questions.yaml has no questions yet")
def test_pass_rate(outcomes):
    """Runs after the questions (file order): gate each model's pass rate when a threshold is set."""
    threshold = min_pass_rate()
    if threshold is None:
        pytest.skip("KHADBOT_EVAL_MIN_PASS_RATE is not set; pass rate is reported only")
    if not outcomes:
        pytest.skip("no golden questions ran")
    for model_id, (passed, total) in pass_rate(outcomes).items():
        assert passed / total >= threshold, f"{model_id}: {passed}/{total} below {threshold:.0%}"
