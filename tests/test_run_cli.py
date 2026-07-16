"""run.py end to end, driven as a real process against the real repo.

These are the ticket's acceptance criteria expressed as behaviour: what an
operator (or the OS scheduler) actually observes.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_cli(*args, cwd=None, research=False):
    """Drive run.py as a real process.

    --dry-run by default: these tests are about the gate and prepare, and a run
    day now reaches stage 2, where a researcher would cost real money and real
    minutes. Pass research=True only with a stubbed model.
    """
    argv = [sys.executable, str(REPO_ROOT / "run.py"), *args]
    if not research and "--dry-run" not in args:
        argv.append("--dry-run")
    env = {**os.environ, "RESEARCHSWARM_OFFLINE": "1"}
    return subprocess.run(
        argv, capture_output=True, text=True, cwd=cwd or REPO_ROOT, env=env, timeout=60
    )


@pytest.fixture
def fake_repo(tmp_path):
    """A copy of the real config and state, so tests can write without touching the repo."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    shutil.copytree(REPO_ROOT / "state", tmp_path / "state")
    return tmp_path


class TestTheGate:
    def test_non_run_day_exits_zero(self):
        # 2026-07-14 is a Tuesday.
        result = run_cli("--today", "2026-07-14")
        assert result.returncode == 0
        assert "not a run day" in result.stderr

    def test_non_run_day_writes_nothing(self, fake_repo):
        """A skipped day is a no-op, not a run: no issue, no stub, no trace."""
        before = {p for p in fake_repo.rglob("*")}
        result = run_cli("--today", "2026-07-14", "--root", str(fake_repo))
        assert result.returncode == 0
        assert {p for p in fake_repo.rglob("*")} == before

    def test_monday_is_a_run_day(self):
        result = run_cli("--today", "2026-07-13")
        assert result.returncode == 0
        assert "not a run day" not in result.stderr
        assert "run_id=" in result.stderr

    def test_thursday_is_a_run_day(self):
        result = run_cli("--today", "2026-07-16")
        assert "not a run day" not in result.stderr

    @pytest.mark.parametrize(
        "day,name",
        [("2026-07-14", "tue"), ("2026-07-15", "wed"), ("2026-07-17", "fri"),
         ("2026-07-18", "sat"), ("2026-07-19", "sun")],
    )
    def test_every_other_weekday_is_a_no_op(self, day, name):
        assert "not a run day" in run_cli("--today", day).stderr

    def test_force_overrides_the_gate(self):
        result = run_cli("--today", "2026-07-14", "--force")
        assert result.returncode == 0
        assert "run_id=" in result.stderr

    def test_the_gate_is_driven_by_config_not_hardcode(self, fake_repo):
        """Flip the config, and Tuesday becomes a run day. This is the whole
        reason cadence lives in a file rather than a cron entry."""
        cadence = fake_repo / "config" / "cadence.toml"
        cadence.write_text(cadence.read_text().replace('days = ["mon", "thu"]', 'days = ["tue"]'))
        assert "run_id=" in run_cli("--today", "2026-07-14", "--root", str(fake_repo)).stderr
        assert "not a run day" in run_cli("--today", "2026-07-13", "--root", str(fake_repo)).stderr


class TestPrepare:
    def test_run_id_format_and_state_summary(self):
        result = run_cli("--today", "2026-07-13")
        assert "run_id=run_" in result.stderr
        assert "22 entities, 6 beliefs (v2), 4 queue items" in result.stderr

    def test_run_one_reports_no_previous_issue(self):
        """The repo has no issues/ yet — the backwards search is empty and that
        is tolerated, not an error."""
        result = run_cli("--today", "2026-07-13")
        assert result.returncode == 0
        assert "run #1" in result.stderr

    def test_joins_to_a_previous_issue(self, fake_repo):
        issues = fake_repo / "issues"
        issues.mkdir()
        (issues / "2026-07-09.json").write_text(
            json.dumps(
                {
                    "issue": {
                        "id": "2026-07-09",
                        "coverage_window": {"from": "2026-07-06", "to": "2026-07-09"},
                        "run": {"status": "published"},
                    }
                }
            )
        )
        result = run_cli("--today", "2026-07-13", "--root", str(fake_repo))
        assert "coverage 2026-07-09 → 2026-07-13 (joins 2026-07-09)" in result.stderr

    def test_walks_past_a_stub_to_the_last_real_issue(self, fake_repo):
        issues = fake_repo / "issues"
        issues.mkdir()
        (issues / "2026-07-06.json").write_text(
            json.dumps(
                {
                    "issue": {
                        "id": "2026-07-06",
                        "coverage_window": {"from": "2026-07-02", "to": "2026-07-06"},
                        "run": {"status": "published"},
                    }
                }
            )
        )
        (issues / "2026-07-09.json").write_text(
            json.dumps({"issue": {"id": "2026-07-09", "run": {"status": "failed"}}})
        )
        result = run_cli("--today", "2026-07-13", "--root", str(fake_repo))
        # The stub is transparent: coverage reclaims the days it never covered.
        assert "coverage 2026-07-06 → 2026-07-13 (joins 2026-07-06)" in result.stderr


class TestFailureModes:
    def test_dangling_entity_ref_refuses_the_run(self, fake_repo):
        """The spine is what links every file. If it has forked, an issue built
        on it is unsound — refuse rather than publish nonsense."""
        queue_path = fake_repo / "state" / "catalyst-queue.json"
        queue = json.loads(queue_path.read_text())
        queue["queue"][0]["entity_ids"] = ["ghost_pharma"]
        queue_path.write_text(json.dumps(queue))

        result = run_cli("--today", "2026-07-13", "--root", str(fake_repo))
        assert result.returncode == 2
        assert "ghost_pharma" in result.stderr
        assert "refusing to run" in result.stderr

    def test_bad_day_name_in_config_fails_loudly(self, fake_repo):
        """A typo'd day that silently never runs is the exact silent failure
        this system refuses."""
        cadence = fake_repo / "config" / "cadence.toml"
        cadence.write_text(cadence.read_text().replace('"thu"', '"thur"'))
        result = run_cli("--today", "2026-07-13", "--root", str(fake_repo))
        assert result.returncode == 2
        assert "thur" in result.stderr

    def test_missing_config_fails_loudly(self, tmp_path):
        result = run_cli("--today", "2026-07-13", "--root", str(tmp_path))
        assert result.returncode == 2
        assert "cadence config not found" in result.stderr

    def test_malformed_state_names_the_file(self, fake_repo):
        (fake_repo / "state" / "thesis.json").write_text("{not json")
        result = run_cli("--today", "2026-07-13", "--root", str(fake_repo))
        assert result.returncode == 2
        assert "thesis.json" in result.stderr
