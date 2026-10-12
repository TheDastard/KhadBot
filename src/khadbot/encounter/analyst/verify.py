"""
encounter/analyst/verify.py

Verify rules for analyst findings (``VERIFY_RULES_VERSION`` in versioning.py; bump it on any rule change):

1. Every ref in a finding resolves, to a pull inside the request. Otherwise the finding is rejected
   with ``unresolvable ref <ref>``.
2. Every number in the claim matches a value in the resolved records at the claim's precision (times
   may be written in seconds). The number of refs cited, overall and per kind, also counts as a value,
   so "3 players died" citing three death refs passes. Otherwise ``unsupported number <n>``.

Pure functions over ``EncounterData``: the analyst graph applies them before accepting a report.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection

from khadbot.encounter.evidence import (
    EncounterData,
    UnresolvableRef,
    claim_numbers,
    number_supported,
    numeric_values,
    parse_ref,
)
from khadbot.encounter.models import (
    AnalysisReport,
    Finding,
)


def check_finding(finding: Finding, data: EncounterData, pulls: Collection[str] | None = None) -> str | None:
    """The reason ``finding`` fails verification, or None if it passes."""
    values: set[float] = set()
    kinds: Counter[str] = Counter()
    for ref in finding.refs:
        try:
            parsed = parse_ref(ref)
            if pulls is not None and parsed.pull_key not in pulls:
                return f"unresolvable ref {ref} (pull is not part of this request)"
            values |= numeric_values(data.resolve(ref))
        except UnresolvableRef:
            return f"unresolvable ref {ref}"
        kinds[parsed.kind] += 1
    values |= {float(len(finding.refs)), *(float(n) for n in kinds.values())}
    for number in claim_numbers(finding.claim):
        if not number_supported(number, values):
            return f"unsupported number {number.text}"
    return None


def verify_report(
    report: AnalysisReport, data: EncounterData, pulls: Collection[str] | None = None
) -> tuple[AnalysisReport, list[str]]:
    """
    Apply the verify rules to every finding.

    Returns the report with failing findings moved to ``rejected`` (as ``<id> "<claim>": <reason>``)
    and the list of new rejection reasons. ``pulls`` limits refs to the request's pulls.
    """
    kept: list[Finding] = []
    reasons: list[str] = []
    for finding in report.findings:
        reason = check_finding(finding, data, pulls)
        if reason is None:
            kept.append(finding)
        else:
            reasons.append(f'{finding.id} "{finding.claim}": {reason}')
    return report.model_copy(update={"findings": kept, "rejected": [*report.rejected, *reasons]}), reasons
