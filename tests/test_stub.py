"""The failed-run stub: same schema, empty sections, failure.stage names where.

A degradation explains an absence inside a valid issue; a stub says there is
no valid issue. The two invariants that matter here: the dashboard needs no
separate stub renderer (same schema), and a stub can never become a coverage
join point (the backwards search must walk past it).
"""

import json
from datetime import date, datetime

import pytest

from researchswarm.stub import FAILURE_STAGES, PublishedIssueExists, write_failed_stub

NOW = datetime(2026, 7, 16, 7, 0, 12)
WINDOW = {"from": "2026-07-13", "to": "2026-07-16"}


def write(tmp_path, **overrides):
    kwargs = {
        "run_id": "run_20260716_0700",
        "now": NOW,
        "window": WINDOW,
        "stage": "research",
        "detail": "all 6 beats failed validation",
        "thesis_version": 2,
        "beats_failed": ["ma_dealmaking"],
        **overrides,
    }
    return write_failed_stub(tmp_path, **kwargs)


class TestStubShape:
    def test_stub_lands_in_issues_named_by_date(self, tmp_path):
        path = write(tmp_path)
        assert path == tmp_path / "issues" / "2026-07-16.json"
        assert path.exists()

    def test_status_failed_and_failure_stage(self, tmp_path):
        issue = json.loads(write(tmp_path).read_text())["issue"]
        assert issue["run"]["status"] == "failed"
        assert issue["run"]["critic_verdict"] == "not_run"
        assert issue["failure"] == {
            "stage": "research",
            "detail": "all 6 beats failed validation",
        }

    def test_same_schema_empty_sections(self, tmp_path):
        """Every top-level key of a real issue is present, so the dashboard
        renders a stub with the renderer it already has."""
        stub = json.loads(write(tmp_path).read_text())
        assert stub["schema_version"] == "1.0.0"
        assert stub["headline"] is None
        for key in (
            "tldr_bullets",
            "watchlist",
            "new_on_radar",
            "themes_and_signals",
            "elsewhere_on_frontier",
            "thesis_updates",
        ):
            assert stub[key] == [], key
        assert stub["sources_and_method"]["beats_failed"] == ["ma_dealmaking"]

    def test_unknown_stage_refused(self, tmp_path):
        with pytest.raises(ValueError, match="parsing"):
            write(tmp_path, stage="parsing")

    def test_stage_vocabulary_matches_the_spec(self):
        assert FAILURE_STAGES == (
            "research",
            "synthesis",
            "validation",
            "critique",
            "publish",
        )


class TestPublishedIssuesAreImmutable:
    def test_a_stub_never_overwrites_a_published_issue(self, tmp_path):
        """A forced rerun that fails on a day that already published must not
        replace the real issue with a stub claiming the whole day failed."""
        issues = tmp_path / "issues"
        issues.mkdir()
        published = {
            "issue": {
                "id": "2026-07-16",
                "coverage_window": WINDOW,
                "run": {"status": "published"},
            }
        }
        (issues / "2026-07-16.json").write_text(json.dumps(published))

        with pytest.raises(PublishedIssueExists):
            write(tmp_path)
        assert json.loads((issues / "2026-07-16.json").read_text()) == published

    def test_a_rerun_may_replace_its_own_earlier_stub(self, tmp_path):
        first = write(tmp_path, detail="first failure")
        second = write(tmp_path, detail="second failure")
        assert first == second
        issue = json.loads(second.read_text())["issue"]
        assert issue["failure"]["detail"] == "second failure"


class TestStubIsTransparentToContinuity:
    def test_stub_is_not_a_coverage_join_point(self, tmp_path):
        """A stub covered no days. If the next run joined to it, the days the
        failed run should have covered would be reported by no one."""
        from researchswarm.runs import resolve_coverage_window

        write(tmp_path)
        window = resolve_coverage_window(tmp_path / "issues", today=date(2026, 7, 20))
        assert window.previous_issue is None

    def test_next_real_issue_still_joins_past_the_stub(self, tmp_path):
        from researchswarm.runs import resolve_coverage_window

        issues = tmp_path / "issues"
        issues.mkdir()
        (issues / "2026-07-13.json").write_text(
            json.dumps(
                {
                    "issue": {
                        "id": "2026-07-13",
                        "coverage_window": {"from": "2026-07-09", "to": "2026-07-13"},
                        "run": {"status": "published"},
                    }
                }
            )
        )
        write(tmp_path)  # stub dated 2026-07-16, newer than the real issue

        window = resolve_coverage_window(issues, today=date(2026, 7, 20))
        # Coverage reclaims the days the stubbed run never covered.
        assert window.previous_issue == "2026-07-13"
        assert window.from_ == date(2026, 7, 13)
