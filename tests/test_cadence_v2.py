"""The v2 cadence gate — the per-program dial, the surge, and the push.

v1 asked "is today in the day-of-week list?"; v2 asks "has a month passed since
THIS program's last issue?". So these tests fake two things, not one: the date
AND the program's previous issue. Neither ever touches the real clock — a test
that passes in July and fails in February is worse than no test.

Spec: docs/spec/02-cadence-and-surge.md
"""

from datetime import date

import pytest

from researchswarm.cadence import (
    DEFAULT_BASELINE_V2,
    CadenceDecisionV2,
    is_run_day_v2,
    next_due_date_v2,
    program_surge_v2,
)
from researchswarm.calendar import SurgeState
from researchswarm.programs import Program, load_program

JAN_14 = date(2026, 1, 14)
FEB_13 = date(2026, 2, 13)
FEB_14 = date(2026, 2, 14)
FEB_15 = date(2026, 2, 15)


def make_program(
    baseline: str = "monthly", seed: tuple[str, ...] = ("asset_her3_dxd",)
) -> Program:
    """A minimal program — only the cadence dial and roster matter to the gate."""
    return Program(
        id="test-001",
        name="TEST-001",
        sponsor="Test Bio",
        modality="antibody",
        target="HER3",
        moa="signalling_blockade",
        indications=(),
        cadence_baseline=baseline,
        cold_start_lookback_days=7,
        seed_competitors=seed,
    )


def make_surge(day: int = 2, of: int = 5) -> SurgeState:
    return SurgeState(
        window="ASCO 2026",
        window_id="asco",
        day=day,
        of=of,
        starts="2026-05-29",
        ends="2026-06-02",
    )


class TestBaselineMonthly:
    def test_due_on_the_month_anniversary(self):
        decision = is_run_day_v2(make_program(), FEB_14, last_issue_date=JAN_14)
        assert decision.run
        assert decision.reason == "baseline_due"
        assert decision.cadence == "monthly"

    def test_not_due_the_day_before(self):
        decision = is_run_day_v2(make_program(), FEB_13, last_issue_date=JAN_14)
        assert not decision.run
        assert decision.reason == "not_due"

    def test_a_missed_day_stays_due_rather_than_being_skipped(self):
        """A skipped day is a no-op, not a lost issue: the window just widens.

        This is what makes spec/02's "coverage window runs from the program's
        previous issue to today" hold with no gaps."""
        assert is_run_day_v2(make_program(), FEB_15, last_issue_date=JAN_14).run

    def test_the_day_after_an_issue_is_not_a_run_day(self):
        assert not is_run_day_v2(
            make_program(), date(2026, 1, 15), last_issue_date=JAN_14
        ).run

    def test_no_previous_issue_is_always_due(self):
        """Run #1 has no interval to have elapsed. cold_start_lookback_days then
        sets the first window's width — the orchestrator's job, not the gate's."""
        decision = is_run_day_v2(make_program(), FEB_14, last_issue_date=None)
        assert decision.run
        assert decision.reason == "cold_start"

    def test_the_dial_comes_from_config_not_hardcode(self):
        """Monthly is a ⚑ default, not an invariant — flipping it flips behaviour."""
        daily = make_program(baseline="daily")
        assert is_run_day_v2(daily, date(2026, 1, 15), last_issue_date=JAN_14).run
        assert not is_run_day_v2(
            make_program(), date(2026, 1, 15), last_issue_date=JAN_14
        ).run

    def test_rejects_a_baseline_the_spec_never_named(self):
        """A typo'd dial that silently never runs is the quiet failure this
        system refuses — same principle as v1's day-name validation."""
        with pytest.raises(ValueError, match="weekly"):
            is_run_day_v2(make_program(baseline="weekly"), FEB_14, last_issue_date=JAN_14)

    def test_baseline_is_case_insensitive(self):
        assert is_run_day_v2(
            make_program(baseline="MONTHLY"), FEB_14, last_issue_date=JAN_14
        ).run


class TestNextDueDate:
    def test_monthly_is_the_calendar_anniversary(self):
        assert next_due_date_v2(JAN_14, "monthly") == FEB_14

    def test_month_end_clamps_down_rather_than_overflowing(self):
        """31 Jan + 1 month = 28 Feb, not 3 March: overflowing up would let a
        late-month program skip February's issue entirely."""
        assert next_due_date_v2(date(2026, 1, 31), "monthly") == date(2026, 2, 28)

    def test_month_end_clamp_respects_a_leap_year(self):
        assert next_due_date_v2(date(2028, 1, 31), "monthly") == date(2028, 2, 29)

    def test_december_rolls_the_year(self):
        assert next_due_date_v2(date(2026, 12, 14), "monthly") == date(2027, 1, 14)

    def test_daily_is_the_next_day(self):
        assert next_due_date_v2(JAN_14, "daily") == date(2026, 1, 15)

    def test_rejects_an_unknown_baseline(self):
        with pytest.raises(ValueError, match="quarterly"):
            next_due_date_v2(JAN_14, "quarterly")


class TestSurge:
    def test_a_competitor_in_the_window_surges_this_program(self):
        program = make_program(seed=("asset_her3_dxd",))
        surge = program_surge_v2(
            make_surge(), program.seed_competitors, ["asset_her3_dxd"]
        )
        assert surge is not None
        assert surge.window == "ASCO 2026"

    def test_a_window_with_none_of_our_competitors_does_not_surge_us(self):
        """Surge is per-program: an ASH window is a real surge for a heme program
        and a dead week for an anti-HER3 one (spec/02 'any program WITH a
        competitor in that window')."""
        program = make_program(seed=("asset_her3_dxd",))
        assert (
            program_surge_v2(make_surge(), program.seed_competitors, ["asset_car_t"])
            is None
        )

    def test_no_live_window_never_surges(self):
        """resolve_surge already applied require_verified_dates and
        max_surge_days; None is its answer for 'no window may surge today'."""
        assert program_surge_v2(None, ["asset_her3_dxd"], ["asset_her3_dxd"]) is None

    def test_an_empty_roster_never_surges(self):
        assert program_surge_v2(make_surge(), [], ["asset_her3_dxd"]) is None

    def test_surge_overrides_a_not_due_baseline(self):
        decision = is_run_day_v2(
            make_program(), FEB_13, last_issue_date=JAN_14, surge_state=make_surge()
        )
        assert decision.run
        assert decision.reason == "surge"
        assert decision.cadence == "daily"
        assert decision.surge is not None

    def test_surge_runs_every_day_of_the_window(self):
        for day in range(29, 32):  # 29-31 May, inside the seeded ASCO window
            decision = is_run_day_v2(
                make_program(),
                date(2026, 5, day),
                last_issue_date=date(2026, 5, 28),
                surge_state=make_surge(),
            )
            assert decision.run and decision.cadence == "daily"

    def test_the_issues_surge_block_is_the_calendars(self):
        """The gate carries resolve_surge's state through untouched — {window,
        day, of} has exactly one home (spec/02 'Surge is marked in the issue')."""
        decision = is_run_day_v2(
            make_program(), FEB_13, last_issue_date=JAN_14, surge_state=make_surge()
        )
        assert decision.surge.run_block == {"window": "ASCO 2026", "day": 2, "of": 5}


class TestManualPush:
    def test_push_forces_a_run_that_the_dial_refuses(self):
        decision = is_run_day_v2(
            make_program(), FEB_13, last_issue_date=JAN_14, push=True
        )
        assert decision.run
        assert decision.reason == "push"

    def test_push_does_not_change_the_cadence_on_record(self):
        """Spec/02: a push produces 'a normal, dated program issue — same
        apertures, same gates, same rubric'. It is not a fourth mode."""
        decision = is_run_day_v2(
            make_program(), FEB_13, last_issue_date=JAN_14, push=True
        )
        assert decision.cadence == "monthly"

    def test_push_works_at_cold_start(self):
        assert is_run_day_v2(make_program(), FEB_13, last_issue_date=None, push=True).run

    def test_push_still_rejects_a_broken_dial(self):
        """The gate validates config before it obeys the human — a push must not
        be the thing that hides a typo'd baseline."""
        with pytest.raises(ValueError, match="weekly"):
            is_run_day_v2(
                make_program(baseline="weekly"), FEB_13, last_issue_date=None, push=True
            )


class TestPilotConfig:
    def test_the_real_pilot_program_carries_a_monthly_dial(self, repo_root):
        """The ⚑ default in spec/02 is what the committed pilot actually says."""
        program = load_program(repo_root / "config", "hmbd-001")
        assert program.cadence_baseline == DEFAULT_BASELINE_V2 == "monthly"

    def test_the_pilots_dial_drives_the_gate(self, repo_root):
        program = load_program(repo_root / "config", "hmbd-001")
        assert not is_run_day_v2(program, FEB_13, last_issue_date=JAN_14).run
        assert is_run_day_v2(program, FEB_14, last_issue_date=JAN_14).run


class TestDecisionShape:
    def test_a_skipped_day_reports_why(self):
        """A skipped day leaves no issue, no stub, no trace (spec/02), so the
        reason is the only thing an operator gets — it must be there."""
        decision = is_run_day_v2(make_program(), FEB_13, last_issue_date=JAN_14)
        assert isinstance(decision, CadenceDecisionV2)
        assert decision.reason == "not_due"
        assert decision.surge is None
