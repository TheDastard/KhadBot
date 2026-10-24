"""
jobs/__init__.py

Job specs and the functions that run them. A spec (``specs``) is the serializable description of one unit of work;
a job function (``run``) does it and reports through ``progress``. The CLI runs them in-process; the hosted queue
runs the same functions in workers.
"""

from __future__ import annotations

from khadbot.jobs.progress import LineProgress, NullProgress, Progress, RecordingProgress
from khadbot.jobs.run import (
    JobContext,
    JobError,
    LocalTokenResolver,
    ModelMismatchError,
    ProcessPullResult,
    ReviewPullResult,
    TokenResolver,
    UserAuthUnavailableError,
    default_filter_kwargs,
    process_specs_for,
    run_ingest_report,
    run_job,
    run_process_pull,
    run_review_pull,
)
from khadbot.jobs.specs import AppAuth, Auth, IngestReport, JobSpec, ProcessPull, ReviewPull, UserAuth

__all__ = [
    "AppAuth",
    "Auth",
    "IngestReport",
    "JobContext",
    "JobError",
    "JobSpec",
    "LineProgress",
    "LocalTokenResolver",
    "ModelMismatchError",
    "NullProgress",
    "ProcessPull",
    "ProcessPullResult",
    "Progress",
    "RecordingProgress",
    "ReviewPull",
    "ReviewPullResult",
    "TokenResolver",
    "UserAuth",
    "UserAuthUnavailableError",
    "default_filter_kwargs",
    "process_specs_for",
    "run_ingest_report",
    "run_job",
    "run_process_pull",
    "run_review_pull",
]
