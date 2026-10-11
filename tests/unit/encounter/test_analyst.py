"""
tests/unit/encounter/test_analyst.py

Verify rules on SYNTH01: hallucinated, malformed and out-of-scope refs; numbers absent from the
cited records; rounding tolerance; seconds versus milliseconds; counts of cited refs. Graph
scenarios with a scripted model live in test_analyst_scenarios.py.
"""

from __future__ import annotations

import pytest

from khadbot.encounter.analyst import check_finding, verify_report
from khadbot.encounter.models import AnalysisReport, Finding, ProducedBy

PRODUCED_BY = ProducedBy(
    model_id="fake", prompt_version="0" * 12, graph_version="1", encfile_hash=None, events_hashes={}
)


def finding(claim: str, *refs: str, n: int = 1) -> Finding:
    return Finding(id=f"req00000-{n}", claim=claim, refs=list(refs), confidence="high", scope="pull")


def report(*findings: Finding) -> AnalysisReport:
    return AnalysisReport(
        request_id="req00000", findings=list(findings), unknowns=[], partial=False, produced_by=PRODUCED_BY
    )


D1 = "ev:SYNTH01:1:death:1"  # Alphadps, 8000 ms, Synth Swirl, overkill 40,000, ttd 100 ms, window damage 114,000


@pytest.mark.parametrize(
    "claim",
    [
        "Alphadps died to Synth Swirl at 8.0s with 40,000 overkill.",
        "Alphadps died 8 seconds in after taking 114,000 damage in the last 10s.",
        "Alphadps died at 8000 ms.",
        "Alphadps died at 0:08.",
        "Alphadps went from healthy to dead in 0.1s.",
        "Alphadps took a 100k Synth Swirl hit.",
        "Alphadps died first, to Synth Swirl.",
    ],
)
def test_supported_claims_pass(synth_data, claim):
    assert check_finding(finding(claim, D1), synth_data) is None


@pytest.mark.parametrize(
    ("claim", "bad"),
    [
        ("Alphadps died with 41,000 overkill.", "41,000"),
        ("Alphadps died at 9.5s.", "9.5s"),
        ("Alphadps took 115,000 damage before dying.", "115,000"),
        ("Alphadps died at 8.04s.", "8.04s"),
        # A definition (time to die counts from 90% health) is not a recorded value.
        ("Alphadps went from 90% health to dead in 0.1s.", "90%"),
    ],
)
def test_unsupported_numbers_rejected(synth_data, claim, bad):
    assert check_finding(finding(claim, D1), synth_data) == f"unsupported number {bad}"


def test_rounding_tolerance(synth_data):
    # Boss at 35.0%: "35%" rounds to the claim's precision; "35.4%" does not match.
    assert check_finding(finding("The boss was at 35% when the raid wiped.", "ev:SYNTH01:1:pull"), synth_data) is None
    assert check_finding(finding("The boss was at 35.4%.", "ev:SYNTH01:1:pull"), synth_data) == (
        "unsupported number 35.4%"
    )


def test_numbers_from_any_cited_record(synth_data):
    claim = "Alphadps died at 8.0s and Gammadps at 23.0s."
    assert check_finding(finding(claim, D1), synth_data) == "unsupported number 23.0s"
    assert check_finding(finding(claim, D1, "ev:SYNTH01:1:death:2"), synth_data) is None


def test_count_of_cited_refs_is_supported(synth_data):
    deaths = [f"ev:SYNTH01:1:death:{n}" for n in (1, 2, 3)]
    assert check_finding(finding("3 players died before the cutoff.", *deaths), synth_data) is None
    assert check_finding(finding("9 players died before the cutoff.", *deaths), synth_data) == "unsupported number 9"


@pytest.mark.parametrize(
    ("ref", "reason"),
    [
        ("ev:SYNTH01:1:death:9", "unresolvable ref ev:SYNTH01:1:death:9"),
        ("ev:SYNTH01:1:mech:made_up", "unresolvable ref ev:SYNTH01:1:mech:made_up"),
        ("death:1", "unresolvable ref death:1"),
        ("ev:OTHER:1:pull", "unresolvable ref ev:OTHER:1:pull"),
    ],
)
def test_unresolvable_refs_rejected(synth_data, ref, reason):
    assert check_finding(finding("Something happened.", D1, ref), synth_data) == reason


def test_ref_outside_request_rejected(synth_data):
    f = finding("Gammadps died.", "ev:SYNTH01:2:death:1")
    assert check_finding(f, synth_data) is None
    assert check_finding(f, synth_data, pulls=["SYNTH01:1"]) == (
        "unresolvable ref ev:SYNTH01:2:death:1 (pull is not part of this request)"
    )


def test_verify_report_moves_failures_to_rejected(synth_data):
    good = finding("Alphadps died at 8.0s.", D1, n=1)
    bad = finding("Alphadps died at 9.0s.", D1, n=2)
    ghost = finding("Someone died.", "ev:SYNTH01:1:death:42", n=3)
    verified, reasons = verify_report(report(good, bad, ghost), synth_data, pulls=["SYNTH01:1"])
    assert verified.findings == [good]
    assert reasons == [
        'req00000-2 "Alphadps died at 9.0s.": unsupported number 9.0s',
        'req00000-3 "Someone died.": unresolvable ref ev:SYNTH01:1:death:42',
    ]
    assert verified.rejected == reasons
    assert (verified.request_id, verified.partial, verified.produced_by) == ("req00000", False, PRODUCED_BY)


def test_verify_report_keeps_earlier_rejections(synth_data):
    r = report(finding("Alphadps died at 9.0s.", D1)).model_copy(update={"rejected": ["earlier"]})
    verified, reasons = verify_report(r, synth_data)
    assert verified.rejected == ["earlier", *reasons] and len(reasons) == 1
