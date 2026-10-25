"""
jobs/budget.py

The WCL budget policy: pure functions over a ``BudgetState`` that decide which ingest jobs may spend the app's
WarcraftLogs points now, which wait, and until when. ``jobs/budget_store.py`` keeps the state in one place, applies
these functions under a lock, and reads WCL's ``rateLimitData``; nothing here does I/O or reads the clock.

How WCL charges (``docs/wcl-api.md``, "Rate limits and point types"):

- The limit belongs to the account that owns the API client, per 60-minute window (``pointsResetIn``).
- Current scheme: every request spends the application points, whoever's token sends it. The budget probe measured
  exactly that.
- New scheme: database points are a capped part of the application points; a signed-in user's requests spend that
  user's small pool first, then the application's. The pool's size is unknown until it is measured, so by default a
  user-token job still counts in full against the app (``BudgetConfig.user_tokens_spend_app_budget``).
- Every request needs 1 application point free, even one a user pays for, so a full bucket blocks user-token jobs.

The policy:

- **Reservation.** A job asks for its estimate (``estimate_points``) before it runs and holds it until it settles.
  Its share of the app bucket (``app_share``) is the estimate, or on the new scheme, with a known user pool and the
  switch off, the part the user's pool can't cover (at least 1 point).
- **Settlement.** A finished job is charged its measured spend (the estimate when none was measured); the next
  ``rateLimitData`` read (``sync``) replaces the bucket's count with WCL's own.
- **Allowances.** Within a window one user may use ``user_share`` and one WCL guild ``guild_share`` of the hourly
  points, counting held estimates and settled spend, user-token jobs included. A job bigger than an allowance needs
  that allowance unused.
- **Headroom.** Ordinary jobs leave ``headroom_share`` of the points free; only live raid ingest may use it.
- **Line.** A job that can't reserve waits. Waiting jobs are taken round-robin across groups (the WCL guild, else the
  user, else the report), the least recently served group first, oldest first within a group. A job blocked by the
  shared bucket stops later ordinary jobs from passing it, so a large job is not starved by small ones; a job blocked
  only by its own allowance does not hold up others.
- **ETA.** A waiting job's ETA comes from the window's reset time and the demand ahead of it in line, per constraint.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from khadbot.config import BudgetConfig, WclLimitScheme

WINDOW = timedelta(hours=1)  # WCL's limit window
RECHECK = timedelta(seconds=30)  # the soonest a waiting job is looked at again on its own
WAIT_TTL = timedelta(hours=24)  # a wait nobody picked up in this long is dropped (its job is gone)
_EPS = 1e-9
_NEVER = datetime.min.replace(tzinfo=UTC)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Block(StrEnum):
    """Why a job cannot reserve now."""

    USER_ALLOWANCE = "user_allowance"
    GUILD_ALLOWANCE = "guild_allowance"
    APP_POINTS = "app_points"
    DATABASE_POINTS = "database_points"
    LINE = "line"  # it fits, but a job ahead of it in line waits for the same points


class Bucket(BaseModel):
    """The app's points in the current window, as last read from ``rateLimitData`` plus settlements since."""

    model_config = ConfigDict(frozen=True)

    limit_per_hour: float
    spent: float = 0.0
    resets_at: datetime | None = None  # None until the first sync or use
    synced_at: datetime | None = None
    database_spent: float = 0.0  # new scheme only; rateLimitData is not known to report it, so counted locally


class Ask(BaseModel):
    """What one job asks of the budget. ``points`` is the whole estimate in application points (database included)."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    report_code: str
    user_id: str | None = None  # who asked; None for background work
    guild_id: str | None = None  # the report's WCL guild, when known
    user_token: bool = False  # the job fetches on the user's own WCL token
    points: float = Field(ge=0)
    database_points: float = Field(default=0.0, ge=0)
    live: bool = False  # live raid ingest, which may use the headroom
    asked_at: datetime

    @property
    def group(self) -> str:
        """The round-robin group: the WCL guild, else the user, else the report."""
        if self.guild_id:
            return f"guild:{self.guild_id}"
        if self.user_id:
            return f"user:{self.user_id}"
        return f"report:{self.report_code}"


class Reservation(BaseModel):
    """Points held for a job until it settles; once settled, its measured spend counts toward the allowances."""

    model_config = ConfigDict(frozen=True)

    ask: Ask
    app_points: float  # held against the app bucket
    granted_at: datetime
    actual: float | None = None
    settled_at: datetime | None = None

    @property
    def settled(self) -> bool:
        return self.settled_at is not None

    @property
    def charge(self) -> float:
        """What it counts toward its user's and guild's allowances: the measured spend, or the estimate held."""
        return self.actual if self.actual is not None else self.ask.points


class BudgetState(BaseModel):
    """Everything the policy decides from: the bucket, reservations of this window, the waiting jobs."""

    model_config = ConfigDict(frozen=True)

    bucket: Bucket
    reservations: tuple[Reservation, ...] = ()
    waiting: tuple[Ask, ...] = ()
    last_served: dict[str, datetime] = Field(default_factory=dict)  # group -> when it was last granted

    def reservation(self, job_id: str) -> Reservation | None:
        return next((r for r in self.reservations if r.ask.job_id == job_id and not r.settled), None)


class Wait(BaseModel):
    """A waiting job's place: 1 is next. ``recheck_at`` is when it is looked at again if nothing frees points first."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    position: int
    eta: datetime
    recheck_at: datetime
    reason: Block


class Admission(BaseModel):
    """The result of ``admit``: the new state, the jobs granted now and where every waiting job stands."""

    model_config = ConfigDict(frozen=True)

    state: BudgetState
    granted: tuple[str, ...] = ()
    waits: dict[str, Wait] = Field(default_factory=dict)


class Allowance(BaseModel):
    """One allowance as a user sees it (``view``)."""

    model_config = ConfigDict(frozen=True)

    limit: float
    used: float

    @property
    def left(self) -> float:
        return max(0.0, self.limit - self.used)


class BudgetView(BaseModel):
    """What the budget looks like to one user right now."""

    model_config = ConfigDict(frozen=True)

    limit_per_hour: float
    spent: float
    held: float
    resets_at: datetime
    user: Allowance
    guild: Allowance | None
    waiting_jobs: int
    as_of: datetime
    eta: datetime  # when an ingest of a typical night asked for now would start; ``as_of`` when it would start at once


# ---------------------------------------------------------------------------
# Estimate and state
# ---------------------------------------------------------------------------


def estimate_points(pulls: int, cfg: BudgetConfig) -> float:
    """
    The points an online ingest of ``pulls`` pulls reserves. Several pulls are fetched batched; a single pull is
    fetched alone and costs more. A job with no pulls (a metadata refresh) costs its fixed requests only.
    """
    if pulls <= 0:
        return cfg.points_per_job
    per_pull = cfg.points_per_single_pull if pulls == 1 else cfg.points_per_pull
    return pulls * per_pull + cfg.points_per_job


def initial_state(cfg: BudgetConfig) -> BudgetState:
    """An empty bucket at the scheme's documented limit, until the first ``rateLimitData`` read."""
    return BudgetState(bucket=Bucket(limit_per_hour=cfg.documented_limit_per_hour))


def roll(state: BudgetState, now: datetime, cfg: BudgetConfig) -> BudgetState:
    """
    Bring the state to ``now``: start a new window once the old one has reset (spent goes to 0 and the old window's
    settled spend stops counting), and drop reservations nobody settled within the TTL and stale waits.
    """
    bucket = state.bucket
    resets = bucket.resets_at or now + WINDOW
    spent, db_spent = bucket.spent, bucket.database_spent
    if now >= resets:
        resets += WINDOW * (math.floor((now - resets) / WINDOW) + 1)
        spent, db_spent = 0.0, 0.0
    start = resets - WINDOW
    ttl = timedelta(seconds=cfg.reservation_ttl_s)
    reservations = tuple(
        r
        for r in state.reservations
        if (r.settled and r.settled_at is not None and r.settled_at >= start)
        or (not r.settled and r.granted_at >= now - ttl)
    )
    waiting = tuple(a for a in state.waiting if a.asked_at >= now - WAIT_TTL)
    bucket = bucket.model_copy(update={"resets_at": resets, "spent": spent, "database_spent": db_spent})
    return state.model_copy(update={"bucket": bucket, "reservations": reservations, "waiting": waiting})


def sync(
    state: BudgetState, *, limit_per_hour: float, spent: float, reset_in_s: float, now: datetime, cfg: BudgetConfig
) -> BudgetState:
    """
    Take WCL's own count (``rateLimitData``): its limit (so a subscription tier or scheme change shows up without a
    deploy), what it says was spent this window, and when the window resets. Held reservations stay held.
    """
    bucket = state.bucket.model_copy(
        update={
            "limit_per_hour": float(limit_per_hour),
            "spent": max(0.0, float(spent)),
            "resets_at": now + timedelta(seconds=max(0.0, float(reset_in_s))),
            "synced_at": now,
        }
    )
    return roll(state.model_copy(update={"bucket": bucket}), now, cfg)


# ---------------------------------------------------------------------------
# What a job needs
# ---------------------------------------------------------------------------


def _open(state: BudgetState) -> list[Reservation]:
    return [r for r in state.reservations if not r.settled]


def held(state: BudgetState) -> float:
    """Application points held by unsettled reservations."""
    return sum(r.app_points for r in _open(state))


def app_free(state: BudgetState) -> float:
    """Application points neither spent nor held in this window."""
    return state.bucket.limit_per_hour - state.bucket.spent - held(state)


def _used(state: BudgetState, *, user_id: str | None = None, guild_id: str | None = None) -> float:
    return sum(
        r.charge
        for r in state.reservations
        if (user_id is not None and r.ask.user_id == user_id) or (guild_id is not None and r.ask.guild_id == guild_id)
    )


def _user_pool_left(ask: Ask, state: BudgetState, cfg: BudgetConfig) -> float:
    """What the user's own pool still covers this window (new scheme, switch off, size configured), else 0."""
    if (
        not ask.user_token
        or ask.user_id is None
        or cfg.scheme is not WclLimitScheme.NEW
        or cfg.user_tokens_spend_app_budget
        or cfg.user_points_per_hour is None
    ):
        return 0.0
    spent_by_user = sum(r.charge for r in state.reservations if r.ask.user_id == ask.user_id and r.ask.user_token)
    return max(0.0, cfg.user_points_per_hour - spent_by_user)


def app_share(ask: Ask, state: BudgetState, cfg: BudgetConfig) -> float:
    """
    The application points ``ask`` holds: its estimate, less what the user's own pool covers when that applies, and
    never under 1 (WCL holds a point before every request). Clamped to what the bucket could ever give it.
    """
    share = max(1.0, ask.points - _user_pool_left(ask, state, cfg))
    return min(share, _bucket_cap(ask, state, cfg))


def _bucket_cap(ask: Ask, state: BudgetState, cfg: BudgetConfig) -> float:
    limit = state.bucket.limit_per_hour
    return max(1.0, limit if ask.live else limit * (1.0 - cfg.headroom_share))


def _headroom(ask: Ask, state: BudgetState, cfg: BudgetConfig) -> float:
    return 0.0 if ask.live else state.bucket.limit_per_hour * cfg.headroom_share


def _db_tracked(ask: Ask, cfg: BudgetConfig) -> bool:
    return cfg.scheme is WclLimitScheme.NEW and ask.database_points > 0


def check(ask: Ask, state: BudgetState, cfg: BudgetConfig) -> Block | None:
    """Why ``ask`` can't reserve now, or None when it can. Allowances first, then the shared bucket."""
    limit = state.bucket.limit_per_hour
    if ask.user_id is not None:
        cap = cfg.user_share * limit
        if _used(state, user_id=ask.user_id) + min(ask.points, cap) > cap + _EPS:
            return Block.USER_ALLOWANCE
    if ask.guild_id is not None:
        cap = cfg.guild_share * limit
        if _used(state, guild_id=ask.guild_id) + min(ask.points, cap) > cap + _EPS:
            return Block.GUILD_ALLOWANCE
    if app_free(state) - app_share(ask, state, cfg) < _headroom(ask, state, cfg) - _EPS:
        return Block.APP_POINTS
    if _db_tracked(ask, cfg):
        cap = cfg.database_points_per_hour
        held_db = sum(r.ask.database_points for r in _open(state))
        if cap - state.bucket.database_spent - held_db < min(ask.database_points, cap) - _EPS:
            return Block.DATABASE_POINTS
    return None


# ---------------------------------------------------------------------------
# The line
# ---------------------------------------------------------------------------


def line(state: BudgetState) -> list[Ask]:
    """
    The waiting jobs in the order they are served: round-robin across groups, the least recently served group
    first, oldest first within a group.
    """
    groups: dict[str, list[Ask]] = defaultdict(list)
    for ask in state.waiting:
        groups[ask.group].append(ask)
    entries = []
    for group, asks in groups.items():
        asks.sort(key=lambda a: (a.asked_at, a.job_id))
        served = state.last_served.get(group, _NEVER)
        entries += [((rank, served, a.asked_at, a.job_id), a) for rank, a in enumerate(asks)]
    return [a for _, a in sorted(entries, key=lambda e: e[0])]


def _grant(state: BudgetState, ask: Ask, now: datetime, cfg: BudgetConfig) -> BudgetState:
    reservation = Reservation(ask=ask, app_points=app_share(ask, state, cfg), granted_at=now)
    return state.model_copy(
        update={
            "reservations": (*state.reservations, reservation),
            "waiting": tuple(a for a in state.waiting if a.job_id != ask.job_id),
            "last_served": {**state.last_served, ask.group: now},
        }
    )


def admit(state: BudgetState, now: datetime, cfg: BudgetConfig, ask: Ask | None = None) -> Admission:
    """
    Put ``ask`` (if any) in line and grant every waiting job that may reserve now, in line order. A job that already
    holds a reservation (it was granted while waiting) is granted again without a second one. Returns the jobs
    granted, ``ask`` among them if it may run, and the place of every job still waiting.
    """
    state = roll(state, now, cfg)
    if ask is not None:
        if state.reservation(ask.job_id) is not None:
            return Admission(state=state, granted=(ask.job_id,), waits=_waits(state, now, cfg))
        if all(a.job_id != ask.job_id for a in state.waiting):  # a job asking again keeps its first place
            state = state.model_copy(update={"waiting": (*state.waiting, ask)})
    granted: list[str] = []
    bucket_blocked = False
    for waiting in line(state):
        if bucket_blocked and not waiting.live:
            continue
        block = check(waiting, state, cfg)
        if block is None:
            state = _grant(state, waiting, now, cfg)
            granted.append(waiting.job_id)
        elif block in (Block.APP_POINTS, Block.DATABASE_POINTS) and not waiting.live:
            bucket_blocked = True
    return Admission(state=state, granted=tuple(granted), waits=_waits(state, now, cfg))


def places(state: BudgetState, now: datetime, cfg: BudgetConfig) -> dict[str, Wait]:
    """Where every waiting job stands, without granting anything: its place in line, ETA and reason."""
    return _waits(roll(state, now, cfg), now, cfg)


def _waits(state: BudgetState, now: datetime, cfg: BudgetConfig) -> dict[str, Wait]:
    ordered = line(state)
    out: dict[str, Wait] = {}
    for i, ask in enumerate(ordered):
        eta = _eta(ask, ordered[:i], state, now, cfg)
        out[ask.job_id] = Wait(
            job_id=ask.job_id,
            position=i + 1,
            eta=eta,
            recheck_at=max(eta, now + RECHECK),
            reason=check(ask, state, cfg) or Block.LINE,
        )
    return out


def _eta(ask: Ask, ahead: list[Ask], state: BudgetState, now: datetime, cfg: BudgetConfig) -> datetime:
    """
    When ``ask`` should be able to start: for each constraint, the demand of the jobs ahead of it under that
    constraint plus its own, against what is free now and what each new window brings. The latest one wins.
    """
    resets = state.bucket.resets_at or now + WINDOW
    limit = state.bucket.limit_per_hour

    def when(cap: float, free_now: float, demand: float) -> datetime:
        if demand <= free_now + _EPS:
            return now
        if cap <= _EPS:
            return resets
        windows = math.ceil((demand - free_now) / cap - _EPS)
        return resets + WINDOW * (windows - 1)

    etas = [now]
    if ask.user_id is not None:
        cap = cfg.user_share * limit
        mine = [a for a in ahead if a.user_id == ask.user_id]
        demand = sum(min(a.points, cap) for a in [*mine, ask])
        etas.append(when(cap, cap - _used(state, user_id=ask.user_id), demand))
    if ask.guild_id is not None:
        cap = cfg.guild_share * limit
        ours = [a for a in ahead if a.guild_id == ask.guild_id]
        demand = sum(min(a.points, cap) for a in [*ours, ask])
        etas.append(when(cap, cap - _used(state, guild_id=ask.guild_id), demand))
    cap = _bucket_cap(ask, state, cfg)
    shared = [a for a in ahead if not a.live]
    demand = sum(app_share(a, state, cfg) for a in [*shared, ask])
    etas.append(when(cap, max(0.0, app_free(state) - _headroom(ask, state, cfg)), demand))
    if _db_tracked(ask, cfg):
        cap = cfg.database_points_per_hour
        held_db = sum(r.ask.database_points for r in _open(state))
        demand = sum(min(a.database_points, cap) for a in [*ahead, ask] if _db_tracked(a, cfg))
        etas.append(when(cap, cap - state.bucket.database_spent - held_db, demand))
    return max(etas)


# ---------------------------------------------------------------------------
# Settling
# ---------------------------------------------------------------------------


def settle(state: BudgetState, job_id: str, actual: float | None, now: datetime, cfg: BudgetConfig) -> BudgetState:
    """
    Close a job's reservation: charge its measured spend (``actual``; the estimate when it is None or negative, as
    when WCL's window reset mid-job) to the bucket and its allowances. The part the user's own pool was planned to
    cover is not charged to the bucket. Nothing happens for a job with no open reservation.
    """
    state = roll(state, now, cfg)
    reservation = state.reservation(job_id)
    if reservation is None:
        return state
    charge = actual if actual is not None and actual >= 0 else reservation.ask.points
    user_part = max(0.0, reservation.ask.points - reservation.app_points) if reservation.ask.user_token else 0.0
    app_charge = max(1.0, charge - user_part) if user_part > 0 else charge
    bucket = state.bucket.model_copy(
        update={
            "spent": state.bucket.spent + app_charge,
            "database_spent": state.bucket.database_spent + reservation.ask.database_points,
        }
    )
    closed = reservation.model_copy(update={"actual": charge, "settled_at": now})
    reservations = tuple(closed if r is reservation else r for r in state.reservations)
    return state.model_copy(update={"bucket": bucket, "reservations": reservations})


def release(state: BudgetState, job_id: str) -> BudgetState:
    """Drop a job's open reservation without charging it: the job never ran (it was cancelled or is gone)."""
    return state.model_copy(
        update={"reservations": tuple(r for r in state.reservations if r.settled or r.ask.job_id != job_id)}
    )


def forget(state: BudgetState, job_id: str) -> BudgetState:
    """Take a job out of line (it was cancelled, or no longer needs points)."""
    return state.model_copy(update={"waiting": tuple(a for a in state.waiting if a.job_id != job_id)})


# ---------------------------------------------------------------------------
# A user's view
# ---------------------------------------------------------------------------


def view(
    state: BudgetState,
    now: datetime,
    cfg: BudgetConfig,
    *,
    user_id: str,
    user_token: bool,
    guild_id: str | None = None,
) -> BudgetView:
    """The budget as ``user_id`` sees it: allowances left and when an ingest of a typical night would start."""
    state = roll(state, now, cfg)
    limit = state.bucket.limit_per_hour
    probe = Ask(
        job_id="",
        report_code="",
        user_id=user_id,
        guild_id=guild_id,
        user_token=user_token,
        points=estimate_points(cfg.default_pulls, cfg),
        asked_at=now,
    )
    ahead = line(state)
    blocked = ahead and any(check(a, state, cfg) in (Block.APP_POINTS, Block.DATABASE_POINTS) for a in ahead)
    eta = now if check(probe, state, cfg) is None and not blocked else _eta(probe, ahead, state, now, cfg)
    return BudgetView(
        limit_per_hour=limit,
        spent=state.bucket.spent,
        held=held(state),
        resets_at=state.bucket.resets_at or now + WINDOW,
        user=Allowance(limit=cfg.user_share * limit, used=_used(state, user_id=user_id)),
        guild=(
            Allowance(limit=cfg.guild_share * limit, used=_used(state, guild_id=guild_id))
            if guild_id is not None
            else None
        ),
        waiting_jobs=len(ahead),
        as_of=now,
        eta=max(eta, now),
    )


__all__ = [
    "RECHECK",
    "WINDOW",
    "Admission",
    "Allowance",
    "Ask",
    "Block",
    "Bucket",
    "BudgetState",
    "BudgetView",
    "Reservation",
    "Wait",
    "admit",
    "app_free",
    "app_share",
    "check",
    "estimate_points",
    "forget",
    "held",
    "initial_state",
    "line",
    "places",
    "release",
    "roll",
    "settle",
    "sync",
    "view",
]
