"""
tests/unit/jobs/test_watch.py

The live report watch against a WCL stub whose report gains fights over time, on procrastinate's in-memory connector:
new boss fights are ingested once with the user's auth and the live flag, trash and unfinished fights are ignored,
the watch stops after a quiet period, at its maximum length, when the user stops it, when the sign-in or the report
is gone and after repeated failures; one watch exists per report; polls draw live points from the WCL budget.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from helpers.jobs.budget import FakeReader, budget_config
from helpers.jobs.synth_queue import synth_env
from helpers.jobs.watch import FAKE_TOKEN, SYNTH_META, api_error, fight, make_watch_env

from khadbot.config import WatchConfig
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.jobs import IngestReport, UserAuth
from khadbot.jobs.budget_store import BudgetManager, MemoryBudgetBackend
from khadbot.jobs.status import JobState
from khadbot.jobs.watch import finished_boss_fights
from khadbot.jobs.watch_store import StopReason, WatchState

CODE = "SYNTH01"


@pytest.fixture
def env(tmp_path):
    return synth_env(tmp_path)


def watch_of(w, watch_id):
    return w.watch_rows.rows[watch_id]


def watch_jobs(w):
    return [j for j in w.connector.jobs.values() if j["task_name"] == "khadbot.watch_report"]


def fights_of(w):
    return [IngestReport.model_validate(j.spec).fights for j in w.ingests()]


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------


async def test_a_watch_queues_its_first_poll_at_once(env):
    w = make_watch_env(env)
    async with w.queue.open():
        rec, created = await w.service.start(CODE, "u1")
    assert created and rec.active and rec.user_id == "u1"
    stored = watch_of(w, rec.id)
    assert stored.queue_job_id is not None
    job = w.connector.jobs[stored.queue_job_id]
    assert (job["task_name"], job["queue_name"]) == ("khadbot.watch_report", "watch")
    assert job["args"] == {"watch_id": rec.id}  # an ID, no token
    assert job["scheduled_at"] is None


async def test_a_second_watch_of_a_report_returns_the_first(env):
    w = make_watch_env(env)
    async with w.queue.open():
        first, created = await w.service.start(CODE, "u1")
        again, created_again = await w.service.start(CODE, "u1")
        other, created_other = await w.service.start(CODE, "u2")
    assert (created, created_again, created_other) == (True, False, False)
    assert again.id == other.id == first.id and other.user_id == "u1"
    assert len(w.watch_rows.rows) == 1
    assert len([j for j in w.connector.jobs.values() if j["task_name"] == "khadbot.watch_report"]) == 1


async def test_a_stopped_watch_can_be_started_again(env):
    w = make_watch_env(env)
    async with w.queue.open():
        first, _ = await w.service.start(CODE, "u1")
        await w.service.stop(CODE, "u1")
        second, created = await w.service.start(CODE, "u1")
    assert created and second.id != first.id


# ---------------------------------------------------------------------------
# New fights
# ---------------------------------------------------------------------------


async def test_new_boss_fights_are_ingested_once_on_the_users_live_auth(env):
    w = make_watch_env(env, fights=[fight(1), fight(2, start=100000)])
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
        await w.service.poll(rec.id)  # nothing new: no second ingest
        w.wcl.fights.append(fight(3, start=200000))
        await w.service.poll(rec.id)
        await w.service.poll(rec.id)

    first, second = w.ingests()
    assert IngestReport.model_validate(first.spec) == IngestReport(
        code=CODE, fights=[1, 2], auth=UserAuth(user_id="u1"), live=True
    )
    assert IngestReport.model_validate(second.spec).fights == [3]
    assert (first.requested_by, first.auth_kind) == ("u1", "user")
    stored = watch_of(w, rec.id)
    assert set(stored.fights) == {1, 2, 3} and stored.ingests == 2 and stored.polls == 4
    assert stored.last_job_id == second.id
    assert set(w.wcl.tokens) == {FAKE_TOKEN}  # every poll used the user's token
    assert set(w.resolver.asked) == {"u1"}


async def test_trash_and_a_fight_still_running_are_ignored_until_they_finish(env):
    w = make_watch_env(env, fights=[fight(1), fight(5, encounter=0, start=70000)])
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        w.wcl.fights.append(fight(2, start=100000, end=100000))
        await w.service.poll(rec.id)
        assert fights_of(w) == [[1]]
        w.wcl.fights[-1] = fight(2, start=100000, end=160000)  # the pull ended
        await w.service.poll(rec.id)
    assert fights_of(w) == [[1], [2]]
    assert 5 not in watch_of(w, rec.id).fights


async def test_the_poll_leaves_the_fetched_metadata_for_the_ingest(env):
    w = make_watch_env(env, fights=[fight(1)])
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        w.wcl.fights.append(fight(9, start=500000))
        await w.service.poll(rec.id)
    cached = env.raw.report_meta(CODE)
    assert cached is not None and [f["id"] for f in cached["fights"]] == [1, 9]
    assert env.raw.fetched_with(CODE) == "user"


async def test_the_next_poll_is_queued_one_interval_later(env):
    w = make_watch_env(env)
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
    stored = watch_of(w, rec.id)
    job = w.connector.jobs[stored.queue_job_id]
    assert job["status"] == "todo" and job["scheduled_at"] == w.clock() + timedelta(seconds=180)
    assert stored.next_poll_at == w.clock() + timedelta(seconds=180)
    assert job["queueing_lock"] == f"watch:{rec.id}"


async def test_a_running_whole_report_ingest_does_not_count_for_a_new_fight(env):
    w = make_watch_env(env, fights=[fight(1)])
    async with w.queue.open():
        mine = (await w.queue.enqueue(IngestReport(code=CODE), requested_by="u1")).job
        await w.queue.store.begin(mine.id)  # a whole-report ingest is running; it may predate fight 1
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
        assert watch_of(w, rec.id).fights == {}  # not handed over: tried again next poll
        await w.queue.store.finish(mine.id, JobState.DONE)
        await w.service.poll(rec.id)
    assert set(watch_of(w, rec.id).fights) == {1}


async def test_an_ingest_that_cannot_be_queued_is_tried_again(env):
    w = make_watch_env(env, fights=[fight(1)])
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        real = w.service._enqueue

        async def broken(spec, user_id):
            raise RuntimeError("queue is down")

        w.service._enqueue = broken  # type: ignore[assignment]
        await w.service.poll(rec.id)
        assert watch_of(w, rec.id).fights == {} and watch_of(w, rec.id).failures == 1
        w.service._enqueue = real
        await w.service.poll(rec.id)
    stored = watch_of(w, rec.id)
    assert set(stored.fights) == {1} and stored.failures == 0 and stored.error_message is None


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------


async def test_the_watch_stops_after_a_quiet_period_and_a_new_fight_resets_it(env):
    w = make_watch_env(env, fights=[fight(1)])
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
        w.clock.advance(minutes=44)
        await w.service.poll(rec.id)
        assert watch_of(w, rec.id).active
        w.wcl.fights.append(fight(2, start=100000))
        await w.service.poll(rec.id)  # a new fight at 44 minutes: the clock starts over
        w.clock.advance(minutes=44)
        await w.service.poll(rec.id)
        assert watch_of(w, rec.id).active
        w.clock.advance(minutes=1)
        polls_queued = len(watch_jobs(w))
        await w.service.poll(rec.id)

    stored = watch_of(w, rec.id)
    assert (stored.state, stored.stop_reason) == (WatchState.STOPPED, StopReason.QUIET)
    assert stored.next_poll_at is None and stored.stopped_at == w.clock()
    assert len(watch_jobs(w)) == polls_queued  # the stopping poll queued no next one


async def test_the_watch_stops_at_its_maximum_length_without_polling(env):
    w = make_watch_env(env, fights=[fight(1)], config=WatchConfig(quiet_period_s=10**6, max_length_s=3600))
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
        requests = w.wcl.requests
        w.clock.advance(hours=1)
        await w.service.poll(rec.id)
    stored = watch_of(w, rec.id)
    assert (stored.state, stored.stop_reason) == (WatchState.STOPPED, StopReason.MAX_LENGTH)
    assert w.wcl.requests == requests  # the expired watch spent nothing


async def test_the_user_stops_a_watch_and_its_queued_poll_is_dropped(env):
    w = make_watch_env(env)
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
        pending = watch_of(w, rec.id).queue_job_id
        stopped = await w.service.stop(CODE, "u1")
        assert stopped is not None and stopped.stop_reason is StopReason.USER
        assert w.connector.jobs[pending]["status"] == "cancelled"
        requests = w.wcl.requests
        await w.service.poll(rec.id)  # a poll that was already running when it was stopped
    assert w.wcl.requests == requests and watch_of(w, rec.id).state is WatchState.STOPPED


async def test_only_the_owner_stops_a_watch(env):
    w = make_watch_env(env)
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        other = await w.service.stop(CODE, "u2")
    assert other is None and watch_of(w, rec.id).active


async def test_a_lost_sign_in_stops_the_watch(env):
    w = make_watch_env(env, fights=[fight(1)])
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        w.resolver.signed_out = True
        await w.service.poll(rec.id)
    stored = watch_of(w, rec.id)
    assert stored.stop_reason is StopReason.SIGN_IN and w.wcl.requests == 0 and not w.ingests()


async def test_a_report_the_user_can_no_longer_see_stops_the_watch(env):
    w = make_watch_env(env)
    w.wcl.missing = True
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
    assert watch_of(w, rec.id).stop_reason is StopReason.REPORT_UNAVAILABLE


async def test_repeated_failed_polls_stop_the_watch_and_a_good_one_resets_the_count(env):
    w = make_watch_env(env, config=WatchConfig(max_failures=3))
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        w.wcl.error = api_error()
        await w.service.poll(rec.id)
        await w.service.poll(rec.id)
        assert watch_of(w, rec.id).failures == 2 and watch_of(w, rec.id).active
        w.wcl.error = None
        await w.service.poll(rec.id)
        assert watch_of(w, rec.id).failures == 0
        w.wcl.error = api_error()
        for _ in range(3):
            await w.service.poll(rec.id)
    stored = watch_of(w, rec.id)
    assert stored.stop_reason is StopReason.ERRORS and stored.error_message


# ---------------------------------------------------------------------------
# The WCL budget
# ---------------------------------------------------------------------------


def manager(**cfg):
    reader = FakeReader(limit=1000, spent=0, reset_in_s=1800)
    return BudgetManager(MemoryBudgetBackend(), budget_config(**cfg), reader=reader), reader


async def test_a_poll_asks_the_budget_as_a_live_request_and_settles_at_its_estimate(env):
    budget, _ = manager()
    w = make_watch_env(env, budget=budget)
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
    state = await budget.state()
    (reservation,) = state.reservations
    assert reservation.ask.live and reservation.ask.user_token and reservation.ask.user_id == "u1"
    assert reservation.ask.points == 1.0 and reservation.settled and reservation.actual == 1.0
    assert state.bucket.spent == 1.0 and state.waiting == ()
    assert watch_of(w, rec.id).points_spent == 1.0


async def test_a_poll_uses_the_headroom_ordinary_jobs_leave(env):
    budget, reader = manager()
    reader.reading = reader.reading.__class__(1000, 850, 1800)  # inside the 20% headroom
    await budget.sync()
    w = make_watch_env(env, budget=budget)
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
    assert w.wcl.requests == 1 and watch_of(w, rec.id).polls == 1


async def test_a_poll_the_budget_has_no_points_for_is_skipped_not_queued(env):
    budget, reader = manager()
    reader.reading = reader.reading.__class__(1000, 1000, 1800)  # nothing left, headroom included
    await budget.sync()
    w = make_watch_env(env, fights=[fight(1)], budget=budget)
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
    stored = watch_of(w, rec.id)
    assert w.wcl.requests == 0 and not w.ingests()
    assert (stored.polls, stored.polls_skipped) == (0, 1) and stored.active
    assert (await budget.state()).waiting == ()  # it did not wait in line
    assert stored.next_poll_at == w.clock() + timedelta(seconds=180)


async def test_the_ingest_a_poll_queues_asks_the_budget_as_live(env):
    budget, reader = manager()
    reader.reading = reader.reading.__class__(1000, 850, 1800)
    await budget.sync()
    w = make_watch_env(env, fights=[fight(1)], budget=budget)
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        await w.service.poll(rec.id)
        (ingest,) = w.ingests()
        await w.queue.run_worker(
            queues=["ingest", "process"], wait=False, listen_notify=False, install_signal_handlers=False
        )
    assert w.jobs.rows[ingest.id].state is not JobState.WAITING_BUDGET  # an ordinary ingest would wait here
    asks = [r.ask for r in (await budget.state()).reservations if r.ask.job_id == ingest.id]
    assert asks and asks[0].live


# ---------------------------------------------------------------------------
# Through a worker
# ---------------------------------------------------------------------------


async def test_a_worker_polls_and_ingests_the_new_pulls(env):
    w = make_watch_env(env, fights=[fight(1, start=100000, end=160000), fight(2, start=300000, end=380000)])
    async with w.queue.open():
        rec, _ = await w.service.start(CODE, "u1")
        for _ in range(6):
            await w.queue.run_worker(
                queues=["watch", "ingest", "process"], wait=False, listen_notify=False, install_signal_handlers=False
            )
    (ingest,) = w.ingests()
    assert ingest.state is JobState.DONE
    assert {p.fight_id for p in env.events.pulls(CODE)} == {1, 2}
    stored = watch_of(w, rec.id)
    assert stored.active and stored.polls == 1
    waiting = w.connector.jobs[stored.queue_job_id]
    assert waiting["status"] == "todo" and waiting["scheduled_at"] > w.clock()  # the next poll is not due


# ---------------------------------------------------------------------------
# Policy and configuration
# ---------------------------------------------------------------------------


def test_finished_boss_fights_drops_trash_and_running_fights():
    meta = {**SYNTH_META, "fights": [fight(1), fight(2, encounter=0), fight(3, start=5, end=5)]}
    pulls, _ = parse_report_meta(CODE, meta)
    assert [p.fight_id for p in finished_boss_fights(pulls)] == [1]


def test_the_watch_config_refuses_unusable_values():
    with pytest.raises(ValueError):
        WatchConfig(poll_points=0.5)
    with pytest.raises(ValueError):
        WatchConfig(poll_interval_s=0)
    with pytest.raises(ValueError):
        WatchConfig(max_failures=0)
