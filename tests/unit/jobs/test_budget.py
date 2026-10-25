"""
tests/unit/jobs/test_budget.py

The WCL budget policy (jobs/budget.py) with an injected clock: the estimate, reservation and settlement, every
allowance boundary, the headroom, round-robin fairness, ETAs, window resets, both limit schemes and the switch that
makes user-token jobs spend the app's points.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from khadbot.config import BudgetConfig, WclLimitScheme
from khadbot.jobs import budget
from khadbot.jobs.budget import Ask, Block, Bucket, BudgetState

T0 = datetime(2026, 10, 25, 20, 0, tzinfo=UTC)
RESET = T0 + timedelta(minutes=30)


def cfg(**kw) -> BudgetConfig:
    base = dict(
        scheme=WclLimitScheme.CURRENT,
        user_tokens_spend_app_budget=True,
        guild_share=0.25,
        user_share=0.10,
        headroom_share=0.20,
        points_per_pull=7.2,
        points_per_single_pull=14.0,
        points_per_job=4.0,
        default_pulls=25,
        database_points_per_hour=250.0,
        user_points_per_hour=None,
        reservation_ttl_s=7200,
    )
    return BudgetConfig(**{**base, **kw})


def state(limit: float = 1000.0, spent: float = 0.0) -> BudgetState:
    """A bucket of ``limit`` points: user allowance 100, guild 250, headroom 200, window resetting at RESET."""
    return BudgetState(bucket=Bucket(limit_per_hour=limit, spent=spent, resets_at=RESET))


def ask(job: str, points: float, *, user: str | None = None, guild: str | None = None, at: int = 0, **kw) -> Ask:
    return Ask(
        job_id=job,
        report_code=f"R{job}",
        user_id=user,
        guild_id=guild,
        points=points,
        asked_at=T0 + timedelta(seconds=at),
        **kw,
    )


def admit(s: BudgetState, a: Ask | None = None, *, c: BudgetConfig | None = None, now: datetime = T0):
    return budget.admit(s, now, c or cfg(), a)


# ---------------------------------------------------------------------------
# Estimate and config
# ---------------------------------------------------------------------------


class TestEstimate:
    def test_a_night_is_batched_pulls_plus_the_jobs_own_requests(self):
        assert budget.estimate_points(25, cfg()) == pytest.approx(25 * 7.2 + 4)

    def test_one_pull_is_fetched_alone_and_costs_more(self):
        assert budget.estimate_points(1, cfg()) == 18.0

    def test_a_metadata_refresh_costs_the_fixed_requests(self):
        assert budget.estimate_points(0, cfg()) == 4.0


class TestConfig:
    def test_defaults_are_the_documented_allowances(self, monkeypatch):
        for key in ("KHADBOT_BUDGET_GUILD_SHARE", "KHADBOT_BUDGET_USER_SHARE", "KHADBOT_BUDGET_HEADROOM_SHARE"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("KHADBOT_WCL_LIMIT_SCHEME", raising=False)
        monkeypatch.delenv("KHADBOT_BUDGET_USER_TOKENS_SPEND_APP", raising=False)
        c = BudgetConfig()
        assert (c.guild_share, c.user_share, c.headroom_share) == (0.25, 0.10, 0.20)
        assert c.scheme is WclLimitScheme.CURRENT and c.user_tokens_spend_app_budget
        assert c.documented_limit_per_hour == 3600.0

    def test_the_new_scheme_starts_at_its_documented_limit(self):
        assert cfg(scheme=WclLimitScheme.NEW).documented_limit_per_hour == 1000.0
        assert budget.initial_state(cfg(scheme=WclLimitScheme.NEW)).bucket.limit_per_hour == 1000.0

    def test_user_points_cannot_be_switched_on_under_the_current_scheme(self):
        with pytest.raises(ValueError, match="KHADBOT_WCL_LIMIT_SCHEME=new"):
            cfg(user_tokens_spend_app_budget=False)

    @pytest.mark.parametrize("field", ["guild_share", "user_share", "headroom_share"])
    def test_a_share_outside_zero_to_one_fails_at_load(self, field):
        with pytest.raises(ValueError, match=field):
            cfg(**{field: 1.5})

    def test_env_values_are_read(self, monkeypatch):
        monkeypatch.setenv("KHADBOT_WCL_LIMIT_SCHEME", "new")
        monkeypatch.setenv("KHADBOT_BUDGET_USER_TOKENS_SPEND_APP", "false")
        monkeypatch.setenv("KHADBOT_BUDGET_USER_POINTS_PER_HOUR", "50")
        c = BudgetConfig()
        assert (c.scheme, c.user_tokens_spend_app_budget, c.user_points_per_hour) == (WclLimitScheme.NEW, False, 50.0)


# ---------------------------------------------------------------------------
# Reservation and settlement
# ---------------------------------------------------------------------------


class TestReservation:
    def test_a_job_that_fits_is_granted_and_holds_its_estimate(self):
        out = admit(state(), ask("a", 90, user="u"))
        assert out.granted == ("a",)
        assert budget.held(out.state) == 90 and out.state.waiting == ()

    def test_asking_again_while_holding_grants_without_a_second_hold(self):
        first = admit(state(), ask("a", 90, user="u"))
        again = admit(first.state, ask("a", 90, user="u"), now=T0 + timedelta(seconds=5))
        assert again.granted == ("a",) and budget.held(again.state) == 90

    def test_settling_charges_the_measured_spend_and_frees_the_hold(self):
        s = admit(state(), ask("a", 90, user="u")).state
        s = budget.settle(s, "a", 61.5, T0 + timedelta(minutes=1), cfg())
        assert budget.held(s) == 0 and s.bucket.spent == 61.5
        assert s.reservations[0].actual == 61.5

    @pytest.mark.parametrize("actual", [None, -12.0])
    def test_settling_without_a_usable_measure_charges_the_estimate(self, actual):
        s = admit(state(), ask("a", 90)).state
        s = budget.settle(s, "a", actual, T0, cfg())
        assert s.bucket.spent == 90

    def test_settling_a_job_with_no_reservation_changes_nothing(self):
        s = state(spent=10)
        assert budget.settle(s, "nope", 5, T0, cfg()).bucket.spent == 10

    def test_release_drops_the_hold_uncharged(self):
        s = admit(state(), ask("a", 90)).state
        s = budget.release(s, "a")
        assert budget.held(s) == 0 and s.bucket.spent == 0

    def test_sync_takes_wcls_count_and_limit(self):
        s = admit(state(), ask("a", 90)).state
        s = budget.sync(s, limit_per_hour=10800, spent=400, reset_in_s=600, now=T0, cfg=cfg())
        assert s.bucket.limit_per_hour == 10800 and s.bucket.spent == 400
        assert s.bucket.resets_at == T0 + timedelta(seconds=600)
        assert budget.held(s) == 90  # held reservations survive a sync

    def test_an_unsettled_reservation_past_its_ttl_is_dropped(self):
        s = admit(state(), ask("a", 90)).state
        later = T0 + timedelta(seconds=7201)
        assert budget.held(budget.roll(s, later, cfg())) == 0


class TestWindow:
    def test_a_new_window_clears_spend_and_old_settlements(self):
        s = admit(state(spent=700), ask("a", 90, user="u")).state
        s = budget.settle(s, "a", 90, T0 + timedelta(minutes=1), cfg())
        rolled = budget.roll(s, RESET + timedelta(seconds=1), cfg())
        assert rolled.bucket.spent == 0 and rolled.reservations == ()
        assert rolled.bucket.resets_at == RESET + timedelta(hours=1)

    def test_several_missed_windows_advance_the_reset_past_now(self):
        rolled = budget.roll(state(spent=5), RESET + timedelta(hours=2, minutes=10), cfg())
        assert rolled.bucket.resets_at == RESET + timedelta(hours=3)


# ---------------------------------------------------------------------------
# Allowances and headroom
# ---------------------------------------------------------------------------


class TestAllowances:
    def test_a_user_may_use_exactly_their_allowance(self):
        s = admit(state(), ask("a", 60, user="u")).state
        out = admit(s, ask("b", 40, user="u", at=1))
        assert out.granted == ("b",)

    def test_one_point_over_the_user_allowance_waits(self):
        s = admit(state(), ask("a", 60, user="u")).state
        out = admit(s, ask("b", 41, user="u", at=1))
        assert out.granted == () and out.waits["b"].reason is Block.USER_ALLOWANCE

    def test_settled_spend_counts_toward_the_allowance(self):
        s = admit(state(), ask("a", 60, user="u")).state
        s = budget.settle(s, "a", 95, T0, cfg())
        assert admit(s, ask("b", 6, user="u", at=1)).waits["b"].reason is Block.USER_ALLOWANCE
        assert admit(s, ask("b", 5, user="u", at=1)).granted == ("b",)

    def test_another_user_is_not_held_up_by_a_full_allowance(self):
        s = admit(state(), ask("a", 100, user="u")).state
        out = admit(s, ask("b", 50, user="v", at=1))
        assert out.granted == ("b",)

    def test_a_guild_may_use_exactly_its_allowance_across_users(self):
        s = state()
        for i, user in enumerate(["u1", "u2", "u3"]):
            s = admit(s, ask(f"j{i}", 80, user=user, guild="g", at=i)).state
        assert admit(s, ask("j3", 10, user="u4", guild="g", at=5)).granted == ("j3",)  # 250 exactly
        s = admit(s, ask("j3", 10, user="u4", guild="g", at=5)).state
        assert admit(s, ask("j4", 1, user="u5", guild="g", at=6)).waits["j4"].reason is Block.GUILD_ALLOWANCE

    def test_allowances_apply_to_user_token_jobs_too(self):
        s = admit(state(), ask("a", 100, user="u", user_token=True)).state
        assert admit(s, ask("b", 1, user="u", user_token=True, at=1)).waits["b"].reason is Block.USER_ALLOWANCE

    def test_a_job_bigger_than_the_allowance_runs_when_the_allowance_is_unused(self):
        out = admit(state(), ask("big", 184, user="u"))
        assert out.granted == ("big",)
        s = budget.settle(out.state, "big", 180, T0, cfg())
        assert admit(s, ask("next", 1, user="u", at=1)).waits["next"].reason is Block.USER_ALLOWANCE

    def test_background_jobs_have_no_user_allowance(self):
        out = admit(state(), ask("bg", 500))
        assert out.granted == ("bg",)


class TestHeadroom:
    def test_an_ordinary_job_may_fill_the_bucket_down_to_the_headroom(self):
        out = admit(state(spent=700), ask("a", 100))
        assert out.granted == ("a",)  # 1000 - 700 - 100 = 200 left, the headroom

    def test_one_point_into_the_headroom_waits(self):
        out = admit(state(spent=700), ask("a", 101))
        assert out.waits["a"].reason is Block.APP_POINTS

    def test_live_ingest_may_use_the_headroom(self):
        out = admit(state(spent=700), ask("live", 250, live=True))
        assert out.granted == ("live",)

    def test_live_ingest_still_needs_the_points(self):
        out = admit(state(spent=900), ask("live", 101, live=True))
        assert out.waits["live"].reason is Block.APP_POINTS

    def test_a_full_bucket_blocks_user_token_jobs_too(self):
        out = admit(state(spent=1000), ask("a", 1, user="u", user_token=True, live=True))
        assert out.granted == ()

    def test_a_job_larger_than_the_usable_bucket_holds_all_of_it(self):
        out = admit(state(), ask("huge", 5000))
        assert out.granted == ("huge",) and budget.held(out.state) == 800


# ---------------------------------------------------------------------------
# Schemes and the user-token switch
# ---------------------------------------------------------------------------

NEW_USER_POOL = dict(scheme=WclLimitScheme.NEW, user_tokens_spend_app_budget=False, user_points_per_hour=50.0)


class TestSchemes:
    def test_current_scheme_charges_a_user_token_job_in_full(self):
        out = admit(state(), ask("a", 90, user="u", user_token=True))
        assert budget.held(out.state) == 90

    def test_new_scheme_with_the_switch_on_still_charges_in_full(self):
        c = cfg(scheme=WclLimitScheme.NEW, user_points_per_hour=50.0)
        assert budget.held(admit(state(), ask("a", 90, user="u", user_token=True), c=c).state) == 90

    def test_new_scheme_with_an_unknown_user_pool_charges_in_full(self):
        c = cfg(scheme=WclLimitScheme.NEW, user_tokens_spend_app_budget=False)
        assert budget.held(admit(state(), ask("a", 90, user="u", user_token=True), c=c).state) == 90

    def test_new_scheme_with_a_known_pool_holds_only_the_rest(self):
        c = cfg(**NEW_USER_POOL)
        out = admit(state(), ask("a", 90, user="u", user_token=True), c=c)
        assert budget.held(out.state) == 40

    def test_a_user_paid_job_still_holds_one_application_point(self):
        c = cfg(**NEW_USER_POOL)
        assert budget.held(admit(state(), ask("a", 10, user="u", user_token=True), c=c).state) == 1

    def test_app_token_jobs_never_draw_on_a_user_pool(self):
        c = cfg(**NEW_USER_POOL)
        assert budget.held(admit(state(), ask("a", 90, user="u"), c=c).state) == 90

    def test_settling_a_user_paid_job_charges_the_bucket_only_the_app_part(self):
        c = cfg(**NEW_USER_POOL)
        s = admit(state(), ask("a", 90, user="u", user_token=True), c=c).state
        s = budget.settle(s, "a", 80, T0, c)
        assert s.bucket.spent == 30  # 80 measured, 50 planned on the user's pool

    def test_the_pool_is_used_up_by_earlier_jobs(self):
        c = cfg(**NEW_USER_POOL)
        s = admit(state(), ask("a", 50, user="u", user_token=True), c=c).state
        s = budget.settle(s, "a", 50, T0, c)
        s = admit(s, ask("b", 30, user="u", user_token=True, at=1), c=c).state
        assert budget.held(s) == 30

    def test_database_points_have_their_own_cap_on_the_new_scheme(self):
        c = cfg(scheme=WclLimitScheme.NEW)
        s = admit(state(), ask("a", 60, database_points=200), c=c).state
        out = admit(s, ask("b", 60, database_points=51, at=1), c=c)
        assert out.waits["b"].reason is Block.DATABASE_POINTS
        assert admit(s, ask("b", 60, database_points=50, at=1), c=c).granted == ("b",)

    def test_database_points_are_not_tracked_on_the_current_scheme(self):
        s = admit(state(), ask("a", 60, database_points=200)).state
        assert admit(s, ask("b", 60, database_points=200, at=1)).granted == ("b",)


# ---------------------------------------------------------------------------
# The line: round-robin and ETAs
# ---------------------------------------------------------------------------


def full_bucket() -> BudgetState:
    """Spent to the headroom, so every ordinary job waits."""
    return state(spent=800)


class TestRoundRobin:
    def test_waiting_jobs_alternate_between_guilds_oldest_first(self):
        s = full_bucket()
        for job, guild, at in [("a1", "A", 0), ("a2", "A", 1), ("a3", "A", 2), ("b1", "B", 3)]:
            s = admit(s, ask(job, 10, guild=guild, at=at)).state
        assert [a.job_id for a in budget.line(s)] == ["a1", "b1", "a2", "a3"]

    def test_the_least_recently_served_group_goes_first(self):
        s = full_bucket().model_copy(update={"last_served": {"guild:A": T0 - timedelta(minutes=1)}})
        for job, guild, at in [("a1", "A", 0), ("b1", "B", 5)]:
            s = admit(s, ask(job, 10, guild=guild, at=at)).state
        assert [a.job_id for a in budget.line(s)] == ["b1", "a1"]

    def test_without_a_guild_users_and_reports_are_their_own_groups(self):
        s = full_bucket()
        for job, user, at in [("u1", "u", 0), ("u2", "u", 1), ("v1", "v", 2), ("bg", None, 3)]:
            s = admit(s, ask(job, 10, user=user, at=at)).state
        assert [a.job_id for a in budget.line(s)] == ["u1", "v1", "bg", "u2"]

    def test_freed_points_go_to_the_line_in_order_and_round_robin(self):
        s = full_bucket()
        for job, guild, at in [("a1", "A", 0), ("a2", "A", 1), ("b1", "B", 2)]:
            s = admit(s, ask(job, 100, guild=guild, at=at)).state
        s = s.model_copy(update={"bucket": s.bucket.model_copy(update={"spent": 600})})  # 200 usable now
        out = admit(s)
        assert out.granted == ("a1", "b1")
        assert out.waits["a2"].position == 1

    def test_a_job_blocked_by_the_bucket_is_not_passed_by_a_smaller_one(self):
        s = state(spent=750)  # 50 usable
        s = admit(s, ask("big", 100, guild="A", at=0)).state
        out = admit(s, ask("small", 10, guild="B", at=1))
        assert out.granted == () and out.waits["small"].reason is Block.LINE

    def test_a_job_blocked_by_its_own_allowance_does_not_hold_up_others(self):
        s = admit(state(), ask("a", 100, user="u")).state
        s = admit(s, ask("b", 10, user="u", at=1)).state
        out = admit(s, ask("c", 10, user="v", at=2))
        assert out.granted == ("c",) and "b" in out.waits

    def test_a_job_asking_again_keeps_its_place(self):
        s = full_bucket()
        s = admit(s, ask("a", 10, guild="A", at=0)).state
        s = admit(s, ask("b", 10, guild="A", at=5)).state
        s = admit(s, ask("a", 10, guild="A", at=60), now=T0 + timedelta(minutes=1)).state
        assert [a.job_id for a in budget.line(s)] == ["a", "b"]

    def test_live_ingest_passes_a_bucket_blocked_line(self):
        s = state(spent=750)
        s = admit(s, ask("big", 100, at=0)).state
        out = admit(s, ask("live", 100, live=True, at=1))
        assert out.granted == ("live",)


class TestEta:
    def test_a_job_waiting_on_the_bucket_starts_when_the_window_resets(self):
        out = admit(full_bucket(), ask("a", 100))
        wait = out.waits["a"]
        assert (wait.position, wait.eta, wait.reason) == (1, RESET, Block.APP_POINTS)

    def test_demand_ahead_beyond_one_window_pushes_the_eta_a_window_later(self):
        s = full_bucket()
        for i in range(9):  # 9 x 100 ahead: 800 fit in the next window
            s = admit(s, ask(f"j{i}", 100, at=i)).state
        out = admit(s, ask("last", 100, at=99))
        assert out.waits["j7"].eta == RESET
        assert out.waits["j8"].eta == RESET + timedelta(hours=1)
        assert out.waits["last"].eta == RESET + timedelta(hours=1)

    def test_a_user_over_their_allowance_waits_for_the_reset(self):
        s = admit(state(), ask("a", 100, user="u")).state
        out = admit(s, ask("b", 50, user="u", at=1))
        assert out.waits["b"].eta == RESET

    def test_the_users_own_queue_ahead_counts_against_their_allowance(self):
        s = admit(state(), ask("a", 100, user="u")).state
        for i in range(3):
            s = admit(s, ask(f"b{i}", 60, user="u", at=i + 1)).state
        waits = admit(s).waits
        assert [waits[f"b{i}"].eta for i in range(3)] == [RESET, RESET + timedelta(hours=1), RESET + timedelta(hours=1)]

    def test_a_waiting_job_is_rechecked_no_sooner_than_the_recheck_interval(self):
        s = state(spent=750)
        s = admit(s, ask("big", 100, at=0)).state
        out = admit(s, ask("small", 10, at=1))
        wait = out.waits["small"]
        assert wait.recheck_at >= T0 + budget.RECHECK and wait.recheck_at >= wait.eta

    def test_places_reports_waits_without_granting(self):
        s = admit(full_bucket(), ask("a", 100)).state
        freed = s.model_copy(update={"bucket": s.bucket.model_copy(update={"spent": 0})})
        assert budget.places(freed, T0, cfg())["a"].position == 1
        assert budget.held(freed) == 0


class TestView:
    def test_a_user_sees_their_allowance_left_and_no_wait(self):
        s = admit(state(), ask("a", 30, user="u")).state
        v = budget.view(s, T0, cfg(default_pulls=5), user_id="u", user_token=False)  # a 40-point ingest
        assert (v.user.limit, v.user.used, v.user.left) == (100, 30, 70)
        assert v.eta == T0 and v.waiting_jobs == 0 and v.held == 30

    def test_a_night_bigger_than_what_is_left_of_the_allowance_waits_for_the_reset(self):
        s = admit(state(), ask("a", 30, user="u")).state
        v = budget.view(s, T0, cfg(), user_id="u", user_token=False)  # 184 points, allowance 100 with 30 used
        assert v.eta == RESET

    def test_a_full_bucket_shows_the_wait_until_reset(self):
        v = budget.view(full_bucket(), T0, cfg(), user_id="u", user_token=False)
        assert v.eta == RESET and v.resets_at == RESET

    def test_a_guild_allowance_is_shown_when_the_guild_is_known(self):
        s = admit(state(), ask("a", 30, user="u", guild="g")).state
        v = budget.view(s, T0, cfg(), user_id="v", user_token=False, guild_id="g")
        assert v.guild is not None and v.guild.used == 30 and v.user.used == 0
