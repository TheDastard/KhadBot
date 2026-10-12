"""
tests/helpers/encounter/golden.py

Golden questions for the analyst eval: the question file schema, loading, and the deterministic
checks applied to each analyst report. Used by tests/eval (real models) and unit-tested on SYNTH01.

Question file (``tests/golden/questions.yaml``) is a YAML list::

    - id: q01
      question: Why did Healbot die at 2:14 on pull 7?
      pulls: ["abc123:7"]
      kind: single_pull        # single_pull | cross_pull | cooldown | plan | unknown_unknown | unanswerable
      expected_findings: ["Died to Swirling Flames while no defensive was active"]
      expected_refs_any: ["ev:abc123:7:death:3"]
      must_be_unknown: false
      depth: standard          # optional: quick | standard | deep

A report passes when every ref it cites resolves, and either (``must_be_unknown``) it has no
findings and at least one unknown, or it cites at least one of ``expected_refs_any``.
``expected_findings`` is for human review of the results; it is not checked automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from khadbot.encounter.evidence import EncounterData, UnresolvableRef, parse_ref
from khadbot.encounter.models import AnalysisReport, Depth

QUESTIONS_FILE = Path(__file__).resolve().parents[2] / "golden" / "questions.yaml"

QuestionKind = Literal["single_pull", "cross_pull", "cooldown", "plan", "unknown_unknown", "unanswerable"]


class GoldenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    question: str
    pulls: list[str] = Field(min_length=1)
    kind: QuestionKind
    expected_findings: list[str] = []
    expected_refs_any: list[str] = []
    must_be_unknown: bool = False
    depth: Depth = "standard"

    @model_validator(mode="after")
    def _consistent(self) -> GoldenQuestion:
        for pull in self.pulls:
            code, _, fight = pull.rpartition(":")
            if not code or not fight.isdigit():
                raise ValueError(f"{self.id}: pull {pull!r} is not a pull key like 'abc123:7'")
        for ref in self.expected_refs_any:
            try:
                parse_ref(ref)
            except UnresolvableRef as e:
                raise ValueError(f"{self.id}: {e}") from None
        if self.kind == "unanswerable" and not self.must_be_unknown:
            raise ValueError(f"{self.id}: unanswerable questions need must_be_unknown: true")
        if self.must_be_unknown and self.expected_refs_any:
            raise ValueError(f"{self.id}: must_be_unknown questions cannot expect refs")
        if not self.must_be_unknown and not self.expected_refs_any:
            raise ValueError(f"{self.id}: answerable questions need expected_refs_any")
        return self


def load_questions(path: Path | None = None) -> list[GoldenQuestion]:
    """
    Load and validate the question file; raises ``ValueError`` naming the first problem.

    Defaults to ``KHADBOT_GOLDEN_QUESTIONS`` if set (to run a subset or another file), else
    ``tests/golden/questions.yaml``.
    """
    path = path or Path(os.environ.get("KHADBOT_GOLDEN_QUESTIONS") or QUESTIONS_FILE)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a YAML list of questions")
    try:
        questions = [GoldenQuestion.model_validate(item) for item in raw]
    except ValidationError as e:
        raise ValueError(f"{path}: {e}") from None
    seen: set[str] = set()
    for q in questions:
        if q.id in seen:
            raise ValueError(f"{path}: duplicate question id {q.id!r}")
        seen.add(q.id)
    return questions


@dataclass
class GoldenOutcome:
    question_id: str
    model_id: str
    passed: bool
    cited_refs: list[str]
    unresolved_refs: list[str]
    reasons: list[str] = field(default_factory=list)


def check_report(q: GoldenQuestion, report: AnalysisReport, data: EncounterData) -> GoldenOutcome:
    """Apply the deterministic golden checks to one analyst report."""
    cited = list(dict.fromkeys(ref for f in report.findings for ref in f.refs))
    unresolved = []
    for ref in cited:
        try:
            data.resolve(ref)
        except UnresolvableRef:
            unresolved.append(ref)
    reasons = []
    if unresolved:
        reasons.append(f"unresolved refs: {', '.join(unresolved)}")
    if q.must_be_unknown:
        if report.findings:
            reasons.append(f"expected no findings, got {len(report.findings)}")
        if not report.unknowns:
            reasons.append("expected unknowns explaining why the data cannot answer")
    elif not set(cited) & set(q.expected_refs_any):
        reasons.append(f"cited none of {', '.join(q.expected_refs_any)}")
    return GoldenOutcome(
        question_id=q.id,
        model_id=report.produced_by.model_id,
        passed=not reasons,
        cited_refs=cited,
        unresolved_refs=unresolved,
        reasons=reasons,
    )


def pass_rate(outcomes: list[GoldenOutcome]) -> dict[str, tuple[int, int]]:
    """``{model_id: (passed, total)}``."""
    out: dict[str, tuple[int, int]] = {}
    for o in outcomes:
        passed, total = out.get(o.model_id, (0, 0))
        out[o.model_id] = (passed + o.passed, total + 1)
    return out


def min_pass_rate() -> float | None:
    """Gate from ``KHADBOT_EVAL_MIN_PASS_RATE`` (0-1), or None to report only."""
    value = os.environ.get("KHADBOT_EVAL_MIN_PASS_RATE")
    return float(value) if value else None
