"""
jobs/failures.py

How a queued job fails: the typed error codes stored on the ``jobs`` row, ``classify`` (an exception to a code, a
message safe to show and whether a retry can help), and ``JobCancelled``, which stops a running job.

Only WCL rate limits, network errors and a sign-in that could not be renewed just now are retryable. Everything else
fails the job once, with a code a client can act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from khadbot.encounter.analyst import ModelMismatchError
from khadbot.encounter.ingest.fetch import MissingFixtureError, UserTokenRejectedError
from khadbot.jobs.run import JobError, TransientJobError, UserAuthUnavailableError
from khadbot.wcl.client import (
    WarcraftLogsAPIError,
    WarcraftLogsNetworkError,
    WarcraftLogsPrivateReportError,
    WarcraftLogsRateLimitError,
)

# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------


class ErrorCode(StrEnum):
    SIGN_IN_REQUIRED = "wcl_sign_in_required"  # the user must sign in to Warcraft Logs again
    PRIVATE_REPORT = "wcl_private_report"  # the report needs a signed-in user's token
    RATE_LIMITED = "wcl_rate_limited"
    NETWORK = "wcl_network"
    WCL_ERROR = "wcl_error"  # any other answer from Warcraft Logs, including "report not found"
    NOT_IN_CACHE = "not_in_cache"  # an offline ingest asked for a page the raw cache does not hold
    NOT_INGESTED = "pull_not_ingested"
    MODEL_MISMATCH = "model_mismatch"
    PULLS_FAILED = "pulls_failed"  # an ingest finished, but some of its pulls failed
    INTERRUPTED = "interrupted"  # the worker stopped before the job finished
    INTERNAL = "internal_error"


@dataclass(frozen=True)
class Failure:
    code: ErrorCode
    message: str  # safe to show a user: never a token, URL or connection string
    retryable: bool = False


class JobCancelled(BaseException):  # noqa: N818 - not an error, and it must pass every ``except Exception``
    """
    Raised inside a running job once its cancellation was requested. It derives from ``BaseException`` because ingest
    catches ``Exception`` per pull so one bad pull cannot sink a report; a cancellation must stop the whole job.
    """


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(exc: BaseException) -> Failure:
    """The typed failure for an exception a job raised. Unknown exceptions give a generic message, never ``str(exc)``."""
    if isinstance(exc, UserAuthUnavailableError | UserTokenRejectedError):
        return Failure(ErrorCode.SIGN_IN_REQUIRED, "The Warcraft Logs sign-in is missing or no longer valid.")
    if isinstance(exc, TransientJobError):
        return Failure(ErrorCode.NETWORK, str(exc), retryable=True)
    if isinstance(exc, WarcraftLogsRateLimitError):
        return Failure(ErrorCode.RATE_LIMITED, "Warcraft Logs rate limited the requests.", retryable=True)
    if isinstance(exc, WarcraftLogsNetworkError):
        return Failure(ErrorCode.NETWORK, "Warcraft Logs could not be reached.", retryable=True)
    if isinstance(exc, WarcraftLogsPrivateReportError):
        return Failure(ErrorCode.PRIVATE_REPORT, "The report is private; sign in to Warcraft Logs to ingest it.")
    if isinstance(exc, WarcraftLogsAPIError):
        return Failure(ErrorCode.WCL_ERROR, str(exc))
    if isinstance(exc, MissingFixtureError):
        return Failure(ErrorCode.NOT_IN_CACHE, str(exc))
    if isinstance(exc, ModelMismatchError):
        return Failure(ErrorCode.MODEL_MISMATCH, str(exc))
    if isinstance(exc, KeyError):
        return Failure(ErrorCode.NOT_INGESTED, "The pull is not ingested.")
    if isinstance(exc, JobError):
        return Failure(ErrorCode.INTERNAL, str(exc))
    return Failure(ErrorCode.INTERNAL, f"The job failed unexpectedly ({type(exc).__name__}).")
