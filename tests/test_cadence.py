"""The cadence gate — the whole scheduler story.

The OS scheduler is a dumb daily heartbeat that fires every day forever and is
never rewritten; this file is the only thing that decides whether today is a
run day. That is why these tests fake the date instead of waiting a week.
"""

from datetime import date

import pytest

from researchswarm.cadence import Cadence, is_run_day, load_cadence

# 2026-07-13 is a Monday, so this week gives us every weekday by offset.
MONDAY = date(2026, 7, 13)
TUESDAY = date(2026, 7, 14)
WEDNESDAY = date(2026, 7, 15)
THURSDAY = date(2026, 7, 16)
FRIDAY = date(2026, 7, 17)
SATURDAY = date(2026, 7, 18)
SUNDAY = date(2026, 7, 19)


class TestIsRunDay:
    def test_baseline_days_run(self):
        cadence = Cadence(days=["mon", "thu"], hour=7)
        assert is_run_day(cadence, MONDAY)
        assert is_run_day(cadence, THURSDAY)

    @pytest.mark.parametrize(
        "day", [TUESDAY, WEDNESDAY, FRIDAY, SATURDAY, SUNDAY]
    )
    def test_other_days_do_not_run(self, day):
        cadence = Cadence(days=["mon", "thu"], hour=7)
        assert not is_run_day(cadence, day)

    def test_cadence_comes_from_config_not_hardcode(self):
        """Flipping config flips behaviour — Mon+Thu is a default, not an invariant."""
        weekends = Cadence(days=["sat", "sun"], hour=7)
        assert is_run_day(weekends, SATURDAY)
        assert not is_run_day(weekends, MONDAY)

    def test_daily_cadence_runs_every_day(self):
        """What a surge window will switch to (wired in the surge ticket)."""
        daily = Cadence(days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"], hour=7)
        for day in [MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY]:
            assert is_run_day(daily, day)

    def test_empty_days_never_runs(self):
        assert not is_run_day(Cadence(days=[], hour=7), MONDAY)

    def test_day_names_are_case_insensitive(self):
        assert is_run_day(Cadence(days=["MON"], hour=7), MONDAY)


class TestLoadCadence:
    def test_loads_the_real_seeded_config(self, repo_root):
        """The config committed to this repo must actually parse."""
        cadence = load_cadence(repo_root / "config" / "cadence.toml")
        assert cadence.days == ["mon", "thu"]
        assert cadence.hour == 7

    def test_rejects_an_unknown_day_name(self, tmp_path):
        """A typo'd day silently never running is exactly the silent failure
        this system refuses. Fail loudly at load."""
        path = tmp_path / "cadence.toml"
        path.write_text('[baseline]\ndays = ["mon", "thur"]\nhour = 7\n')
        with pytest.raises(ValueError, match="thur"):
            load_cadence(path)

    def test_rejects_an_out_of_range_hour(self, tmp_path):
        path = tmp_path / "cadence.toml"
        path.write_text('[baseline]\ndays = ["mon"]\nhour = 25\n')
        with pytest.raises(ValueError, match="hour"):
            load_cadence(path)

    def test_missing_file_is_an_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_cadence(tmp_path / "nope.toml")
