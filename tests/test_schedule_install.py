"""The heartbeat installer — the entire platform-specific surface.

The scheduler must stay dumb. These tests mostly guard that: it knows an hour,
and nothing about run days. If a test here ever needs to know about Mondays,
the design has drifted.
"""

import importlib.machinery
import importlib.util
import plistlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def si():
    """schedule-install has no .py extension, so import it by path."""
    loader = importlib.machinery.SourceFileLoader("si", str(REPO_ROOT / "schedule-install"))
    spec = importlib.util.spec_from_loader("si", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TestLaunchdPlist:
    def test_generated_plist_is_valid(self, si):
        """An invalid plist fails at install time on the target machine, which
        is exactly where nobody is watching."""
        parsed = plistlib.loads(si._launchd_plist(7, "/usr/bin/python3").encode())
        assert parsed["Label"] == "com.researchswarm.heartbeat"
        assert parsed["ProgramArguments"][1].endswith("run.py")

    def test_fires_daily_at_the_configured_hour(self, si):
        parsed = plistlib.loads(si._launchd_plist(7, "/usr/bin/python3").encode())
        # Hour only, no Weekday key: the scheduler fires every day, forever.
        assert parsed["StartCalendarInterval"] == {"Hour": 7, "Minute": 0}

    def test_hour_is_not_hardcoded(self, si):
        parsed = plistlib.loads(si._launchd_plist(23, "/usr/bin/python3").encode())
        assert parsed["StartCalendarInterval"]["Hour"] == 23

    def test_the_scheduler_knows_nothing_about_run_days(self, si):
        """Mon+Thu is a config fact, not a cron fact. If a day name leaks into
        the plist, cadence has escaped cadence.toml and is no longer reviewable
        in a diff or testable by faking the date."""
        xml = si._launchd_plist(7, "/usr/bin/python3").lower()
        for day in ["mon", "tue", "wed", "thu", "fri", "sat", "sun", "weekday"]:
            assert day not in xml
