"""
tests/unit/jobs/test_failures.py

Which exceptions become which typed failure, and that only WCL rate limits, network errors and a sign-in that could
not be renewed are retryable.
"""

from __future__ import annotations

import pytest

from khadbot.encounter.analyst import ModelMismatchError
from khadbot.encounter.ingest.fetch import MissingFixtureError, UserTokenRejectedError
from khadbot.jobs import JobError, TransientJobError, UserAuthUnavailableError
from khadbot.jobs.failures import ErrorCode, classify
from khadbot.wcl.client import (
    WarcraftLogsAPIError,
    WarcraftLogsNetworkError,
    WarcraftLogsPrivateReportError,
    WarcraftLogsRateLimitError,
)


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (WarcraftLogsRateLimitError("429"), ErrorCode.RATE_LIMITED, True),
        (WarcraftLogsNetworkError("timeout"), ErrorCode.NETWORK, True),
        (TransientJobError("renew later"), ErrorCode.NETWORK, True),
        (UserAuthUnavailableError("u1 must sign in"), ErrorCode.SIGN_IN_REQUIRED, False),
        (UserTokenRejectedError("rejected"), ErrorCode.SIGN_IN_REQUIRED, False),
        (WarcraftLogsPrivateReportError("private"), ErrorCode.PRIVATE_REPORT, False),
        (WarcraftLogsAPIError("report not found"), ErrorCode.WCL_ERROR, False),
        (MissingFixtureError("page missing"), ErrorCode.NOT_IN_CACHE, False),
        (ModelMismatchError("other model"), ErrorCode.MODEL_MISMATCH, False),
        (KeyError("ABC:9"), ErrorCode.NOT_INGESTED, False),
        (JobError("explained"), ErrorCode.INTERNAL, False),
        (RuntimeError("secret-looking detail"), ErrorCode.INTERNAL, False),
    ],
)
def test_classify(exc, code, retryable):
    failure = classify(exc)
    assert (failure.code, failure.retryable) == (code, retryable)


def test_an_unknown_exception_never_leaks_its_message():
    assert "secret-looking detail" not in classify(RuntimeError("secret-looking detail")).message


def test_a_sign_in_failure_does_not_repeat_the_user_id():
    assert "u1" not in classify(UserAuthUnavailableError("u1 must sign in")).message
