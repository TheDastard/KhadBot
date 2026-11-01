"""
tests/unit/jobs/test_steps.py

The stage-to-step mapping: every stage the worker reports lands in one of the three player-facing steps, and a
pull's steps follow its progress entry and its job's state (running, done, failed in each step, stopped, queued).
"""

from __future__ import annotations

import pytest

from khadbot.jobs.status import JobState, PullProgress
from khadbot.jobs.steps import ORDER, Step, StepState, locate, pull_steps

D, P, A = ORDER
W, R, OK, F, S = StepState.WAITING, StepState.RUNNING, StepState.DONE, StepState.FAILED, StepState.SKIPPED


def states(steps) -> list[StepState]:
    return [s.state for s in steps]


@pytest.mark.parametrize(
    ("stage", "fraction", "expected"),
    [
        ("ingest", 0.0, (0, 0.0)),
        ("fetched Deaths", 0.5, (0, 50.0)),
        ("fetched Casts:enemy", 1.0, (0, 100.0)),
        ("normalizing", 0.25, (1, 25.0)),
        ("records", 0.5, (2, 40.0)),
        ("records", 1.0, (2, 80.0)),
        ("project", 0.0, (2, 80.0)),
        ("project", 1.0, (2, 100.0)),
        ("failed", 0.0, None),
        ("cancelled", 0.0, None),
        ("review", 0.5, None),
    ],
)
def test_every_stage_the_worker_reports_belongs_to_a_step(stage, fraction, expected):
    assert locate(stage, fraction) == expected


def test_a_fetching_pull_is_downloading():
    steps = pull_steps(PullProgress(stage="fetched Deaths", fraction=0.4), JobState.RUNNING)
    assert [s.step for s in steps] == [D, P, A] and states(steps) == [R, W, W] and steps[0].pct == 40.0


def test_a_fetched_pull_is_done_downloading_while_it_waits_for_its_process_job():
    steps = pull_steps(PullProgress(stage="fetched Deaths", fraction=1.0), JobState.RUNNING)
    assert states(steps) == [OK, W, W]


@pytest.mark.parametrize(
    ("stage", "fraction", "expected"),
    [
        ("normalizing", 0.6, [OK, R, W]),
        ("normalizing", 1.0, [OK, OK, W]),
        ("records", 0.5, [OK, OK, R]),
        ("project", 0.5, [OK, OK, R]),
        ("project", 1.0, [OK, OK, OK]),
    ],
)
def test_a_processing_pull_moves_through_prepare_and_analyse(stage, fraction, expected):
    assert states(pull_steps(PullProgress(stage=stage, fraction=fraction), JobState.RUNNING)) == expected


def test_a_finished_pull_has_three_done_steps():
    steps = pull_steps(PullProgress(stage="project", fraction=1.0, state="done"), JobState.RUNNING)
    assert states(steps) == [OK, OK, OK] and {s.pct for s in steps} == {100.0}


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("ingest", [F, S, S]),  # the fetch failed
        ("fetched Deaths", [F, S, S]),
        ("normalizing", [OK, F, S]),
        ("records", [OK, OK, F]),
        ("project", [OK, OK, F]),
        ("failed", [OK, F, S]),  # a process job that failed before it reported
    ],
)
def test_a_failure_in_each_step_fails_it_and_skips_the_rest(stage, expected):
    entry = PullProgress(stage=stage, fraction=0.5, state="failed", error="why")
    assert states(pull_steps(entry, JobState.RUNNING)) == expected


def test_a_stopped_pull_keeps_what_was_done_and_skips_the_rest():
    entry = PullProgress(stage="normalizing", fraction=0.4, state="cancelled")
    assert states(pull_steps(entry, JobState.CANCELLED)) == [OK, S, S]


def test_a_finished_job_settles_a_pull_that_was_left_running():
    entry = PullProgress(stage="records", fraction=0.3)
    assert states(pull_steps(entry, JobState.FAILED)) == [OK, OK, F]
    assert states(pull_steps(entry, JobState.CANCELLED)) == [OK, OK, S]


@pytest.mark.parametrize(
    ("job", "fetched", "expected"),
    [
        (JobState.QUEUED, False, [W, W, W]),
        (JobState.WAITING_BUDGET, False, [W, W, W]),
        (JobState.RUNNING, False, [R, W, W]),
        (JobState.RUNNING, True, [OK, W, W]),
        (JobState.FAILED, False, [F, S, S]),
        (JobState.CANCELLED, False, [S, S, S]),
        (JobState.DONE, False, [OK, OK, OK]),
    ],
)
def test_a_pull_that_has_not_reported_follows_its_job(job, fetched, expected):
    assert states(pull_steps(None, job, fetched=fetched)) == expected


def test_step_names_are_the_players_three():
    assert [s.value for s in Step] == ["download", "prepare", "analyse"]
