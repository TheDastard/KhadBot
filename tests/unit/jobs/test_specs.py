"""
tests/unit/jobs/test_specs.py

Job specs: defaults, JSON round trips through the discriminated union, and that no spec can carry a token.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from khadbot.jobs import AppAuth, IngestReport, JobSpec, ProcessPull, ReviewPull, UserAuth

SPECS = TypeAdapter(JobSpec)


class TestIngestReport:
    def test_defaults_to_app_auth_and_every_fight(self):
        spec = IngestReport(code="ABC")
        assert spec.auth == AppAuth()
        assert spec.fights is None
        assert (spec.offline, spec.refresh_meta) == (False, False)

    def test_user_auth_holds_a_user_id_only(self):
        spec = IngestReport(code="ABC", auth=UserAuth(user_id="u-1"))
        assert spec.model_dump(mode="json")["auth"] == {"kind": "user", "user_id": "u-1"}

    def test_user_id_is_required(self):
        with pytest.raises(ValidationError):
            UserAuth(user_id="")

    def test_refresh_meta_cannot_be_offline(self):
        with pytest.raises(ValidationError, match="cannot run offline"):
            IngestReport(code="ABC", refresh_meta=True, offline=True)

    def test_specs_are_immutable(self):
        with pytest.raises(ValidationError):
            IngestReport(code="ABC").code = "XYZ"


class TestRoundTrip:
    @pytest.mark.parametrize(
        "spec",
        [
            IngestReport(code="ABC", fights=[3, 4], auth=UserAuth(user_id="u-1"), offline=True),
            IngestReport(code="ABC"),
            ProcessPull(pull_key="ABC:3"),
            ReviewPull(pull_key="ABC:3", depth="deep", model="some-model"),
        ],
    )
    def test_json_round_trip_keeps_the_spec_type(self, spec):
        again = SPECS.validate_json(spec.model_dump_json())
        assert again == spec
        assert type(again) is type(spec)

    def test_unknown_job_is_rejected(self):
        with pytest.raises(ValidationError):
            SPECS.validate_python({"job": "mine_bitcoin"})

    def test_a_spec_has_no_token_field(self):
        for spec in (IngestReport(code="ABC", auth=UserAuth(user_id="u")), ProcessPull(pull_key="A:1")):
            assert "token" not in spec.model_dump_json().lower()


class TestReviewPull:
    def test_defaults(self):
        spec = ReviewPull(pull_key="ABC:3")
        assert (spec.depth, spec.model) == ("standard", None)

    def test_depth_is_checked(self):
        with pytest.raises(ValidationError):
            ReviewPull(pull_key="ABC:3", depth="huge")


def test_an_ingest_is_not_live_unless_a_watch_queued_it():
    assert IngestReport(code="abc").live is False
    assert IngestReport.model_validate_json(IngestReport(code="abc", live=True).model_dump_json()).live is True
