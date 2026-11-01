"""
jobs/steps.py

The three player-facing steps of opening one pull, derived from the stages a job reports. The worker reports
internal stages (``fetched <spec>``, ``normalizing``, ``records``, ``project``); a loading screen shows three steps:

    download  the fight's pages fetched from Warcraft Logs (the ingest job's ``fetched <spec>`` events)
    prepare   its events normalized into the event table (``normalizing``)
    analyse   annotation, records, digest and the read-model projection (``records``, ``project``)

One ingest job covers all three for a pull: the process job under it mirrors its stage onto the ingest's pull entry
(``jobs.status._progress``), so the ingest row alone says where each pull is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from khadbot.jobs.status import JobState, PullProgress

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class Step(StrEnum):
    DOWNLOAD = "download"
    PREPARE = "prepare"
    ANALYSE = "analyse"


class StepState(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"  # not run because an earlier step failed, or the job was stopped


@dataclass(frozen=True)
class StepStatus:
    step: Step
    state: StepState
    pct: float  # 0 to 100


ORDER = (Step.DOWNLOAD, Step.PREPARE, Step.ANALYSE)
_RECORDS_WEIGHT = 80.0  # of the analyse step: building the records is the slow part, projecting them the rest

# ---------------------------------------------------------------------------
# Stage to step
# ---------------------------------------------------------------------------


def locate(stage: str, fraction: float) -> tuple[int, float] | None:
    """
    The step a reported stage belongs to (an index into ``ORDER``) and how far through the step it is, 0 to 100.
    None for a stage that belongs to no step (a pull reports ``failed`` or ``cancelled`` when it never reported).
    """
    pct = min(max(fraction, 0.0), 1.0) * 100
    if stage.startswith("fetched") or stage == "ingest":
        return 0, pct
    if stage == "normalizing":
        return 1, pct
    if stage == "records":
        return 2, pct * _RECORDS_WEIGHT / 100
    if stage == "project":
        return 2, _RECORDS_WEIGHT + pct * (100 - _RECORDS_WEIGHT) / 100
    return None


# ---------------------------------------------------------------------------
# Steps of one pull
# ---------------------------------------------------------------------------


def _all(state: StepState, pct: float = 0.0) -> list[StepStatus]:
    return [StepStatus(step, state, pct) for step in ORDER]


def pull_steps(pull: PullProgress | None, job_state: JobState, *, fetched: bool = False) -> list[StepStatus]:
    """
    The three steps of one pull.

    Args:
        pull: The pull's entry in the ingest's progress, or None when it has not reported yet.
        job_state: The ingest job's state. A finished job settles a pull that never finished: a failed job fails
            it at its current step and a cancelled one skips it.
        fetched: The ingest has fetched its pages (it waits for, or has finished, its process jobs). Used only for a
            pull that has not reported.
    """
    if pull is None:
        if job_state is JobState.DONE:
            return _all(StepState.DONE, 100.0)
        if job_state is JobState.CANCELLED:
            return _all(StepState.SKIPPED)
        if job_state is JobState.FAILED:
            return [
                StepStatus(Step.DOWNLOAD, StepState.FAILED, 0.0),
                StepStatus(Step.PREPARE, StepState.SKIPPED, 0.0),
                StepStatus(Step.ANALYSE, StepState.SKIPPED, 0.0),
            ]
        if job_state is JobState.RUNNING:
            if fetched:
                return [StepStatus(Step.DOWNLOAD, StepState.DONE, 100.0), *_all(StepState.WAITING)[1:]]
            return [StepStatus(Step.DOWNLOAD, StepState.RUNNING, 0.0), *_all(StepState.WAITING)[1:]]
        return _all(StepState.WAITING)

    state = pull.state
    if state == "running":  # a finished job leaves a pull that never finished as it was; settle it by the job
        state = {JobState.FAILED: "failed", JobState.CANCELLED: "cancelled", JobState.DONE: "done"}.get(
            job_state, "running"
        )
    if state == "done":
        return _all(StepState.DONE, 100.0)

    at = locate(pull.stage, pull.fraction)
    if state == "running":
        if at is None:
            return _all(StepState.WAITING)
        index, pct = at
        steps = []
        for i, step in enumerate(ORDER):
            if i < index or (i == index and pct >= 100):
                steps.append(StepStatus(step, StepState.DONE, 100.0))
            elif i == index:
                steps.append(StepStatus(step, StepState.RUNNING, round(pct, 1)))
            else:
                steps.append(StepStatus(step, StepState.WAITING, 0.0))
        return steps

    # failed or cancelled: the steps before the current one are done, it ends, and the rest never ran.
    index, pct = at if at is not None else (1, 0.0)  # a process job that never reported failed while preparing
    ended = StepState.FAILED if state == "failed" else StepState.SKIPPED
    out = []
    for i, step in enumerate(ORDER):
        if i < index:
            out.append(StepStatus(step, StepState.DONE, 100.0))
        elif i == index:
            out.append(StepStatus(step, ended, round(min(pct, 99.9), 1)))
        else:
            out.append(StepStatus(step, StepState.SKIPPED, 0.0))
    return out
