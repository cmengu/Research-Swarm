"""Run identity and the coverage window.

The window's `from` binds to the most recent issue that actually COVERED a
window — never the positionally-previous one. A stub published no window and
covered no days, so it cannot be a join point. If "previous" meant positionally
previous, a single failed run would leave a gap in coverage that nothing
announces; the backwards search closes that.

Run #1 is the empty case of that search and needs no special handling.
"""

import json
from datetime import date, datetime

import pytest

from researchswarm.runs import (
    LOOKBACK_FLOOR,
    resolve_coverage_window,
    resolve_run_id,
)


def _issues(tmp_path, *specs):
    """specs: (issue_id, status, window_to or None) newest-last."""
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)
    for issue_id, status, window_to in specs:
        body = {"schema_version": "1.0.0", "issue": {"id": issue_id, "run": {"status": status}}}
        if window_to is not None:
            body["issue"]["coverage_window"] = {"from": "2026-01-01", "to": window_to}
        (issues_dir / f"{issue_id}.json").write_text(json.dumps(body))
    return issues_dir


class TestResolveRunId:
    def test_format_is_run_yyyymmdd_hhmm(self):
        assert resolve_run_id(datetime(2026, 7, 16, 7, 0)) == "run_20260716_0700"

    def test_pads_single_digits(self):
        assert resolve_run_id(datetime(2026, 1, 5, 9, 3)) == "run_20260105_0903"

    def test_is_stable_for_the_same_instant(self):
        now = datetime(2026, 7, 16, 7, 0)
        assert resolve_run_id(now) == resolve_run_id(now)


class TestCoverageWindow:
    def test_run_one_has_no_prior_issue(self, tmp_path):
        """The backwards search returns empty and is TOLERATED, not an error.
        Run #1 creates the values later checks guard; there is nothing to protect."""
        window = resolve_coverage_window(tmp_path / "issues", today=date(2026, 7, 16))
        assert window.previous_issue is None
        assert window.to == date(2026, 7, 16)
        assert not window.baseline_expired

    def test_missing_issues_dir_is_run_one_not_an_error(self, tmp_path):
        window = resolve_coverage_window(tmp_path / "nonexistent", today=date(2026, 7, 16))
        assert window.previous_issue is None

    def test_binds_from_to_the_previous_issues_window_end(self, tmp_path):
        issues = _issues(tmp_path, ("2026-07-13", "published", "2026-07-13"))
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert window.from_ == date(2026, 7, 13)
        assert window.to == date(2026, 7, 16)
        assert window.previous_issue == "2026-07-13"

    def test_walks_back_past_a_stub(self, tmp_path):
        """A stub is transparent to continuity: it published no window, so the
        next run joins to the last issue that actually covered days. Without
        this the stub's days would silently vanish from coverage."""
        issues = _issues(
            tmp_path,
            ("2026-07-09", "published", "2026-07-09"),
            ("2026-07-13", "failed", None),
        )
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert window.from_ == date(2026, 7, 9)
        assert window.previous_issue == "2026-07-09"

    def test_walks_back_past_consecutive_stubs(self, tmp_path):
        issues = _issues(
            tmp_path,
            ("2026-07-02", "published", "2026-07-02"),
            ("2026-07-06", "failed", None),
            ("2026-07-09", "failed", None),
            ("2026-07-13", "failed", None),
        )
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert window.from_ == date(2026, 7, 2)
        assert window.previous_issue == "2026-07-02"

    def test_a_widened_window_reclaims_the_days_a_stub_missed(self, tmp_path):
        """The next successful run widens to include the missed days —
        automatic, because the window binds to the last issue that covered one."""
        issues = _issues(
            tmp_path,
            ("2026-07-09", "published", "2026-07-09"),
            ("2026-07-13", "failed", None),
        )
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert (window.to - window.from_).days == 7  # not the usual 3

    def test_uncritiqued_issues_are_valid_join_points(self, tmp_path):
        """published_uncritiqued is a real published issue — it covered a window."""
        issues = _issues(tmp_path, ("2026-07-13", "published_uncritiqued", "2026-07-13"))
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert window.previous_issue == "2026-07-13"

    def test_issues_with_unresolved_findings_are_valid_join_points(self, tmp_path):
        issues = _issues(
            tmp_path, ("2026-07-13", "published_with_unresolved_findings", "2026-07-13")
        )
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert window.previous_issue == "2026-07-13"

    def test_search_stops_at_the_lookback_floor(self, tmp_path):
        """Unbounded in principle if runs stub repeatedly. Twelve consecutive
        issues without the compared field means a louder problem than tampering,
        and an unbounded scan would hide it behind a slow check."""
        specs = [("2026-01-01", "published", "2026-01-01")]
        specs += [(f"2026-07-{d:02d}", "failed", None) for d in range(1, LOOKBACK_FLOOR + 2)]
        issues = _issues(tmp_path, *specs)
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert window.baseline_expired
        assert window.previous_issue is None

    def test_finding_a_baseline_inside_the_floor_does_not_expire(self, tmp_path):
        specs = [(f"2026-06-{d:02d}", "failed", None) for d in range(1, LOOKBACK_FLOOR)]
        specs.insert(0, ("2026-05-01", "published", "2026-05-01"))
        issues = _issues(tmp_path, *specs)
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert not window.baseline_expired
        assert window.previous_issue == "2026-05-01"

    def test_ignores_the_manifest(self, tmp_path):
        """index.json lives alongside the issues and is not one."""
        issues = _issues(tmp_path, ("2026-07-13", "published", "2026-07-13"))
        (issues / "index.json").write_text(json.dumps({"issues": []}))
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert window.previous_issue == "2026-07-13"

    def test_cold_start_window_uses_the_configured_lookback(self, tmp_path):
        window = resolve_coverage_window(
            tmp_path / "issues", today=date(2026, 7, 16), cold_start_days=7
        )
        assert window.from_ == date(2026, 7, 9)

    def test_to_is_always_today(self, tmp_path):
        issues = _issues(tmp_path, ("2026-07-13", "published", "2026-07-13"))
        window = resolve_coverage_window(issues, today=date(2026, 7, 16))
        assert window.to == date(2026, 7, 16)
