"""
jobs/watch.py

Live report watch: while a raid is still being logged, a watch reads the report's metadata every few minutes on the
requesting user's own WCL token and queues an ingest of each boss fight it has not handed over yet.

How a watch behaves:

- **One poll is one queue job.** The watch task (``khadbot.watch_report``, queue ``watch``) polls once and queues the
  next poll for ``poll_interval_s`` later, so no worker slot is held between polls and a restart loses nothing: the
  next poll is a row in the queue. The first poll runs at once, so the boss fights already in the report are
  ingested when the watch starts.
- **New boss fights.** A poll fetches ``GET_REPORT_META`` (the query ingest uses) and writes the answer to the raw
  page cache, so the ingest it queues reads the same metadata. A fight is new when it has a boss encounter (trash
  has encounter ID 0), has ended (``end_ms`` after ``start_ms``) and is not in the watch's ``fights``. All new fights
  of one poll go into one ``IngestReport`` with ``live`` set, on the user's auth. A fight is marked handed over only
  after the ingest was queued, so a failed queueing is tried again by the next poll; an ingest found already
  covering a fight counts, except a running whole-report ingest, which may have read its metadata before the fight
  existed.
- **Stopping.** A watch stops when the user stops it, when a poll finds no new boss fight for ``quiet_period_s``
  (measured from the last new fight, or the start), when it has run ``max_length_s``, when the user's WCL sign-in is
  gone or rejected, when WCL no longer shows the user the report, or after ``max_failures`` failed polls in a row.
  The quiet check runs after a poll, so the last poll before it always happens; the maximum length is checked before
  one, so an expired watch spends nothing.
- **Budget.** Each poll asks the WCL budget for ``poll_points`` as a live request (it may use the headroom) and
  settles at that estimate. A poll the budget cannot grant is skipped and counted, never queued behind other jobs:
  the next one comes a full interval later. The ingests a watch queues carry ``live`` too (see ``jobs/queue.py``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from khadbot.config import WatchConfig, get_config
from khadbot.encounter.ingest.fetch import FETCHED_WITH_KEY, UserTokenRejectedError, query_wcl
from khadbot.encounter.ingest.normalize import parse_report_meta
from khadbot.encounter.ingest.wcl_queries import GET_REPORT_META
from khadbot.encounter.models import Pull
from khadbot.jobs.budget import Admission, Ask
from khadbot.jobs.run import JobContext, TransientJobError, UserAuthUnavailableError
from khadbot.jobs.specs import IngestReport, UserAuth
from khadbot.jobs.status import JobRecord, JobState
from khadbot.jobs.watch_store import StopReason, WatchRecord, WatchState, WatchStore
from khadbot.wcl.client import WarcraftLogsAPIError, WarcraftLogsClient, WarcraftLogsPrivateReportError

if TYPE_CHECKING:
    from khadbot.jobs.budget_store import BudgetManager

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def finished_boss_fights(pulls: list[Pull]) -> list[Pull]:
    """The boss fights of a report that have ended. Trash (encounter ID 0) and a fight still running are left out."""
    return [p for p in pulls if p.encounter_id > 0 and p.end_ms > p.start_ms]


def new_fights(rec: WatchRecord, pulls: list[Pull]) -> list[Pull]:
    """The finished boss fights the watch has not handed to ingest yet, in fight order."""
    return sorted((p for p in finished_boss_fights(pulls) if p.fight_id not in rec.fights), key=lambda p: p.fight_id)


def expired(rec: WatchRecord, now: datetime) -> bool:
    """Whether the watch has run its maximum length."""
    return now >= rec.expires_at


def quiet(rec: WatchRecord, now: datetime, cfg: WatchConfig) -> bool:
    """Whether no new boss fight turned up for the quiet period."""
    return now - rec.quiet_since >= timedelta(seconds=cfg.quiet_period_s)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class QueuedIngest(Protocol):
    """What ``JobQueue.enqueue`` returns."""

    @property
    def job(self) -> JobRecord: ...

    @property
    def created(self) -> bool: ...


EnqueueIngest = Callable[[IngestReport, str], Awaitable[QueuedIngest]]
SchedulePoll = Callable[[WatchRecord, datetime | None], Awaitable[int]]
CancelPoll = Callable[[int], Awaitable[None]]
Wake = Callable[..., Awaitable[None]]


class _Stop(Exception):  # noqa: N818 - control flow inside one poll, not an error
    def __init__(self, reason: StopReason, message: str | None = None) -> None:
        self.reason = reason
        self.message = message


@dataclass
class _Outcome:
    skipped: bool = False
    failed: str | None = None  # a message safe to show
    handed: dict[int, int] | None = None  # fights handed to ingest by this poll
    queued_job: str | None = None
    new_ingest: bool = False


class WatchService:
    """
    Starts, stops and runs live report watches.

    Args:
        store: The ``watches`` table.
        enqueue: Queues an ingest for a user: ``(spec, requested_by)``; ``JobQueue.enqueue``.
        schedule: Queues the next poll of a watch at a time (None: now) and returns the queue job's ID.
        cancel_poll: Drops a queued poll.
        config: Intervals, limits and the poll's point estimate.
        context_factory: Builds the ``JobContext`` a poll uses for the token resolver, the raw page cache and, in
            tests, the WCL client.
        budget: The WCL budget the polls draw from; None polls without one.
        wake: Queues again the waiting jobs a budget request just admitted (``JobQueue._wake``).
        clock: The time; tests inject one.
    """

    def __init__(
        self,
        store: WatchStore,
        *,
        enqueue: EnqueueIngest,
        schedule: SchedulePoll,
        cancel_poll: CancelPoll,
        config: WatchConfig | None = None,
        context_factory: Callable[[], JobContext] = JobContext,
        budget: BudgetManager | None = None,
        wake: Wake | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self.store = store
        self.config = config or WatchConfig()
        self.budget = budget
        self._enqueue = enqueue
        self._schedule = schedule
        self._cancel_poll = cancel_poll
        self._context_factory = context_factory
        self._wake = wake
        self.clock = clock

    # -- start and stop -------------------------------------------------------

    async def start(self, report_code: str, user_id: str) -> tuple[WatchRecord, bool]:
        """
        Watch a report on ``user_id``'s token. The report's active watch is returned as ``(watch, False)`` whoever
        owns it; the caller decides whether that is theirs. A new watch has its first poll queued at once.
        """
        now = self.clock()
        fresh = WatchRecord(
            id=uuid.uuid4().hex,
            report_code=report_code,
            user_id=user_id,
            started_at=now,
            expires_at=now + timedelta(seconds=self.config.max_length_s),
            quiet_since=now,
            next_poll_at=now,
            updated_at=now,
        )
        rec, created = await self.store.start(fresh)
        if created:
            try:
                queue_job_id = await self._schedule(rec, None)
            except Exception:
                await self._stop(rec.id, StopReason.ERRORS, "The first poll could not be queued.")
                raise
            updated, _ = await self.store.update(rec.id, lambda r: r.model_copy(update={"queue_job_id": queue_job_id}))
            rec = updated or rec
        return rec, created

    async def stop(self, report_code: str, user_id: str) -> WatchRecord | None:
        """
        Stop the user's watch of the report. Returns the watch as it now stands (already stopped ones unchanged), or
        None when the user has no watch of this report.
        """
        active = await self.store.active_for(report_code)
        if active is not None and active.user_id == user_id:
            stopped = await self._stop(active.id, StopReason.USER)
            if active.queue_job_id is not None:
                try:
                    await self._cancel_poll(active.queue_job_id)
                except Exception:  # noqa: BLE001 - a poll that already ran finds the watch stopped and does nothing
                    logger.debug("Could not drop the queued poll of watch %s", active.id, exc_info=True)
            return stopped or await self.store.get(active.id)
        return await self.store.latest_for(report_code, user_id)

    async def _stop(self, watch_id: str, reason: StopReason, message: str | None = None) -> WatchRecord | None:
        now = self.clock()

        def change(rec: WatchRecord) -> WatchRecord:
            return rec.model_copy(
                update={
                    "state": WatchState.STOPPED,
                    "stop_reason": reason,
                    "stopped_at": now,
                    "next_poll_at": None,
                    "error_message": message or rec.error_message,
                }
            )

        rec, _ = await self.store.update(watch_id, change)
        logger.info("Watch %s stopped: %s", watch_id, reason.value)
        return rec

    # -- one poll -------------------------------------------------------------

    async def poll(self, watch_id: str) -> None:
        """
        Run one poll of a watch and queue the next. Does nothing for a watch that is gone or stopped. Never raises
        for a failed poll: that is counted on the watch, which stops after ``max_failures`` in a row.
        """
        rec = await self.store.get(watch_id)
        if rec is None or not rec.active:
            return
        now = self.clock()
        if expired(rec, now):
            await self._stop(watch_id, StopReason.MAX_LENGTH)
            return
        try:
            outcome = await self._poll_once(rec, now)
        except _Stop as stop:
            await self._stop(watch_id, stop.reason, stop.message)
            return
        except Exception:  # noqa: BLE001 - a bug in one poll must not end the watch silently
            logger.exception("Watch %s: unexpected error in a poll", watch_id)
            outcome = _Outcome(failed="The poll failed unexpectedly.")
        await self._record(rec, outcome)

    async def _record(self, before: WatchRecord, outcome: _Outcome) -> None:
        """Save a poll's result, stop the watch if it is done, else queue the next poll."""
        now = self.clock()
        cfg = self.config
        stop_reason: StopReason | None = None

        def change(rec: WatchRecord) -> WatchRecord:
            nonlocal stop_reason
            update: dict[str, object] = {"last_polled_at": now}
            if outcome.skipped:
                update["polls_skipped"] = rec.polls_skipped + 1
            elif outcome.failed is not None:
                update.update(failures=rec.failures + 1, error_message=outcome.failed)
                if rec.failures + 1 >= cfg.max_failures:
                    stop_reason = StopReason.ERRORS
            else:
                update.update(
                    polls=rec.polls + 1,
                    failures=0,
                    error_message=None,
                    points_spent=rec.points_spent + cfg.poll_points,
                )
            if outcome.handed:
                update.update(
                    fights={**rec.fights, **outcome.handed},
                    quiet_since=now,
                    ingests=rec.ingests + (1 if outcome.new_ingest else 0),
                    last_job_id=outcome.queued_job or rec.last_job_id,
                )
            moved = rec.model_copy(update=update)
            if stop_reason is None and outcome.failed is None and not outcome.skipped and quiet(moved, now, cfg):
                stop_reason = StopReason.QUIET
            if stop_reason is not None:
                return moved.model_copy(
                    update={
                        "state": WatchState.STOPPED,
                        "stop_reason": stop_reason,
                        "stopped_at": now,
                        "next_poll_at": None,
                    }
                )
            return moved.model_copy(update={"next_poll_at": now + timedelta(seconds=cfg.poll_interval_s)})

        saved, applied = await self.store.update(before.id, change)
        if not applied or saved is None or not saved.active:
            if applied and saved is not None:
                logger.info("Watch %s stopped: %s", saved.id, saved.stop_reason)
            return
        try:
            queue_job_id = await self._schedule(saved, saved.next_poll_at)
        except Exception:
            logger.exception("Watch %s: could not queue the next poll", saved.id)
            await self._stop(saved.id, StopReason.ERRORS, "The next poll could not be queued.")
            return
        await self.store.update(saved.id, lambda r: r.model_copy(update={"queue_job_id": queue_job_id}))

    async def _poll_once(self, rec: WatchRecord, now: datetime) -> _Outcome:
        """Reserve the poll's points, read the metadata and queue the ingest of the new boss fights."""
        ask = await self._reserve(rec, now)
        if ask is False:
            return _Outcome(skipped=True)
        try:
            pulls = await self._read_pulls(rec)
        except _Stop:
            raise
        except (UserAuthUnavailableError, UserTokenRejectedError):
            raise _Stop(StopReason.SIGN_IN, "The Warcraft Logs sign-in is missing or no longer valid.") from None
        except WarcraftLogsPrivateReportError:
            raise _Stop(StopReason.REPORT_UNAVAILABLE, "Warcraft Logs no longer shows you this report.") from None
        except TransientJobError as exc:
            return _Outcome(failed=str(exc))
        except WarcraftLogsAPIError as exc:
            logger.info("Watch %s: Warcraft Logs failed a poll: %s", rec.id, type(exc).__name__)
            return _Outcome(failed="Warcraft Logs could not answer the poll.")
        finally:
            await self._settle(ask)
        fresh = new_fights(rec, pulls)
        if not fresh:
            return _Outcome()
        return await self._hand_over(rec, fresh)

    async def _hand_over(self, rec: WatchRecord, fresh: list[Pull]) -> _Outcome:
        spec = IngestReport(
            code=rec.report_code, fights=[p.fight_id for p in fresh], auth=UserAuth(user_id=rec.user_id), live=True
        )
        try:
            queued = await self._enqueue(spec, rec.user_id)
        except Exception:  # noqa: BLE001 - the fights stay new, so the next poll tries again
            logger.exception("Watch %s: could not queue an ingest", rec.id)
            return _Outcome(failed="The ingest of the new fights could not be queued.")
        job = queued.job
        if (
            not queued.created
            and job.state is JobState.RUNNING
            and IngestReport.model_validate(job.spec).fights is None
        ):
            # A whole-report ingest already running may have read the metadata before these fights existed.
            return _Outcome()
        return _Outcome(handed={p.fight_id: p.end_ms for p in fresh}, queued_job=job.id, new_ingest=queued.created)

    async def _read_pulls(self, rec: WatchRecord) -> list[Pull]:
        """Read the report's metadata on the user's token, cache it for the ingest and return its pulls."""
        ctx = self._context_factory()
        provider = await ctx.resolver.provider_for(UserAuth(user_id=rec.user_id))
        async with AsyncExitStack() as stack:
            client = ctx.client
            if client is None:
                wcl = get_config().warcraftlogs
                client = await stack.enter_async_context(WarcraftLogsClient(wcl.client_id, wcl.client_secret))
            data = await query_wcl(client, GET_REPORT_META, {"code": rec.report_code}, provider)
        report = (data.get("reportData") or {}).get("report")
        if report is None:
            raise _Stop(StopReason.REPORT_UNAVAILABLE, "Warcraft Logs no longer shows you this report.")
        ctx.raw_cache().write_report_meta(rec.report_code, {**report, FETCHED_WITH_KEY: "user"})
        pulls, _ = parse_report_meta(rec.report_code, report)
        return pulls

    # -- the budget -----------------------------------------------------------

    async def _reserve(self, rec: WatchRecord, now: datetime) -> Ask | bool | None:
        """
        Reserve the poll's points as a live request. The reservation (``Ask``) when granted, None when there is no
        budget, False when it cannot be granted now: the poll is dropped from the budget's line and skipped.
        """
        if self.budget is None:
            return None
        ask = Ask(
            job_id=f"poll:{rec.id}:{rec.polls + rec.polls_skipped + rec.failures}:{int(now.timestamp())}",
            report_code=rec.report_code,
            user_id=rec.user_id,
            guild_id=await self.budget.guild_of(rec.report_code),
            user_token=True,
            points=self.config.poll_points,
            live=True,
            asked_at=now,
        )
        admission: Admission = await self.budget.request(ask)
        if self._wake is not None:
            await self._wake(admission, skip=ask.job_id)
        if ask.job_id in admission.granted:
            return ask
        await self.budget.release(ask.job_id)
        return False

    async def _settle(self, ask: Ask | bool | None) -> None:
        """Charge a poll its estimate (WCL reports no cost per request; the next rate-limit read corrects the bucket)."""
        if self.budget is None or not isinstance(ask, Ask):
            return
        try:
            await self.budget.settle(ask.job_id, None)
        except Exception:  # noqa: BLE001 - settling must never change how the poll ended
            logger.warning("Could not settle the WCL budget for %s", ask.job_id, exc_info=True)


__all__ = [
    "WatchService",
    "expired",
    "finished_boss_fights",
    "new_fights",
    "quiet",
]
