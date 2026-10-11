"""
tests/helpers/findings.py

Builder for PullFindings rows used by the findings store and review tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from khadbot.encounter.models import Finding, ProducedBy, PullFindings


def make(finding_id: str = "abcd0123-1", *, pull: str = "SYNTH01:1", key: str = "k1", minutes: int = 0) -> PullFindings:
    return PullFindings(
        pull_key=pull,
        findings=[
            Finding(id=finding_id, claim="Someone died", refs=[f"ev:{pull}:pull"], confidence="high", scope="pull")
        ],
        unknowns=["why"],
        produced_by=ProducedBy(
            model_id="model-a", prompt_version="p" * 12, graph_version="1", encfile_hash=None, events_hashes={}
        ),
        cache_key=key,
        created_at=datetime(2026, 10, 11, tzinfo=UTC) + timedelta(minutes=minutes),
    )
