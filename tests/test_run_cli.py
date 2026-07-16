"""run.py end to end, driven as a real process against the real repo.

These are the ticket's acceptance criteria expressed as behaviour: what an
operator (or the OS scheduler) actually observes.
"""

import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
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
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
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


class TestResearchStage:
    def test_dry_run_renders_all_six_beats(self, fake_repo):
        result = run_cli("--today", "2026-07-16", "--root", str(fake_repo))
        assert result.returncode == 0
        for beat_id in (
            "ma_dealmaking", "startup_frontier", "clinical_scientific",
            "policy_regulation", "incumbent_moves", "backstop",
        ):
            assert f"[dry-run] {beat_id}: rendered" in result.stderr

    def test_all_beats_dead_publishes_a_stub_and_fails(self, fake_repo):
        """End to end through the real process: with the offline guard set and
        no fake runner, every researcher dies — which IS the all-six-failed
        path. The run publishes a stub, not nothing."""
        result = run_cli("--today", "2026-07-16", "--root", str(fake_repo), research=True)
        assert result.returncode == 1
        assert "failed-run stub" in result.stderr

        stub = json.loads((fake_repo / "issues" / "2026-07-16.json").read_text())
        assert stub["issue"]["run"]["status"] == "failed"
        assert stub["issue"]["failure"]["stage"] == "research"
        assert len(stub["sources_and_method"]["beats_failed"]) == 6
        # No beat produced findings, so nothing was persisted.
        assert not list((fake_repo / "runs").rglob("findings/*.json"))


SYNTH_RUN_ID = "run_20260716_0700"


def _valid_draft(run_id=SYNTH_RUN_ID, thesis_version=2):
    """The minimal draft the seam accepts — enough keys, empty stats, a so_what,
    and a run block echoing the identifiers run.py handed the manager."""
    return {
        "schema_version": "1.0.0",
        "issue": {
            "id": "2026-07-16",
            "published_at": "2026-07-16T07:00:00+08:00",
            "coverage_window": {"from": "2026-07-13", "to": "2026-07-16"},
            "run": {
                "run_id": run_id, "status": "published_uncritiqued",
                "critic_verdict": "not_run", "critic_retries": 0,
                "thesis_version": thesis_version,
                "models": {"researchers": "sonnet", "manager": "claude-opus-4-8", "critic": None},
            },
        },
        "headline": {"title": "t", "summary": "s", "so_what": "matters today",
                     "entity_refs": [], "confidence": "high", "sources": []},
        "stats": {},
        "tldr_bullets": [],
        "catalyst_queue": {"snapshot_of": "state/catalyst-queue.json", "recut_at": None, "items": []},
        "watchlist": [],
        "quiet_this_cycle": {"no_news": [], "critic_catches": [], "open_threads": []},
        "new_on_radar": [],
        "themes_and_signals": [],
        "elsewhere_on_frontier": [],
        "thesis_updates": [],
        "critic_report": {"verdict": "not_run", "retries_used": 0, "blocking_findings": [],
                          "advisory_findings": [], "validator_report": None},
        "sources_and_method": {"beats_run": [], "beats_failed": [],
                               "source_tier_counts": {}, "paywalled_flagged": []},
    }


def _manager_runner(prompts=None, draft=None):
    """A fake subprocess.run for the manager: records the prompt it was handed
    and returns one valid-draft envelope."""
    payload = draft if draft is not None else _valid_draft()

    def runner(command, **kwargs):
        if prompts is not None:
            prompts.append(command[command.index("-p") + 1])
        envelope = json.dumps(
            {"is_error": False, "result": json.dumps(payload),
             "total_cost_usd": 0.42, "num_turns": 7}
        )
        return SimpleNamespace(returncode=0, stdout=envelope, stderr="")

    return runner


def _synthesis_args(fake_repo, *, beats_run, beats_failed):
    """Everything run_synthesis_stage needs, loaded from the fake repo, with the
    beats_run's findings pre-seeded on disk (run.py is the sole reader here)."""
    from researchswarm.beats import load_beats
    from researchswarm.manager import load_models
    from researchswarm.prompts import RunContext, load_template
    from researchswarm.research import ResearchStage
    from researchswarm.state import load_state
    from researchswarm.synthesis import IssueIdentity

    findings_dir = fake_repo / "runs" / SYNTH_RUN_ID / "findings"
    findings_dir.mkdir(parents=True)
    for beat_id in beats_run:
        (findings_dir / f"{beat_id}.json").write_text(
            json.dumps({"beat": beat_id, "findings": [], "quiet": True})
        )

    ctx = RunContext(run_id=SYNTH_RUN_ID, coverage_window_from="2026-07-13",
                     coverage_window_to="2026-07-16")
    return {
        "identity": IssueIdentity(ctx=ctx, issue_id="2026-07-16",
                                  published_at="2026-07-16T07:00:00+08:00"),
        "state": load_state(fake_repo / "state"),
        "beats": load_beats(fake_repo / "config" / "beats.toml"),
        "stage": ResearchStage(beats_run=beats_run, beats_failed=beats_failed),
        "models_config": load_models(fake_repo / "config" / "models.toml"),
        "manager_template": load_template(fake_repo / "prompts" / "manager.md"),
        "prior_quiet": {},
    }


class TestSynthesisStage:
    def test_manager_success_writes_the_draft(self, fake_repo):
        """run.py is the sole writer: the manager's draft lands at
        runs/<run_id>/issue-draft.json, no earlier and nowhere else."""
        from researchswarm.synthesis import run_synthesis_stage

        args = _synthesis_args(fake_repo, beats_run=["ma_dealmaking"], beats_failed=[])
        result, path = run_synthesis_stage(fake_repo, runner=_manager_runner(), **args)

        assert path == fake_repo / "runs" / SYNTH_RUN_ID / "issue-draft.json"
        written = json.loads(path.read_text())
        assert written["schema_version"] == "1.0.0"
        assert written["issue"]["run"]["run_id"] == SYNTH_RUN_ID
        assert result.cost_usd == 0.42

    def test_beats_failed_subset_flows_into_the_manager_prompt(self, fake_repo):
        """The manager must be told which beats died so it can mark their
        sections inline — a thin section read as a fact is the misled-reader bar."""
        from researchswarm.synthesis import run_synthesis_stage

        prompts = []
        args = _synthesis_args(
            fake_repo, beats_run=["ma_dealmaking", "backstop"], beats_failed=["policy_regulation"]
        )
        run_synthesis_stage(fake_repo, runner=_manager_runner(prompts), **args)

        assert len(prompts) == 1
        assert "policy_regulation" in prompts[0]
        assert "beat_failed" in prompts[0]  # the degradation duty is spelled out

    def test_manager_failure_stubs_synthesis_and_exits_nonzero(self, fake_repo, monkeypatch):
        """A dead manager is a synthesis stub, not a degradation: facts exist but
        no issue does. Driven through main() so the wiring — stub stage, exit
        code — is what an operator would observe."""
        import run
        from researchswarm.manager import ManagerFailed
        from researchswarm.research import ResearchStage

        # Research succeeds (so the run reaches stage 3); the manager then dies.
        monkeypatch.setattr(
            run, "run_research_stage",
            lambda *a, **k: ResearchStage(beats_run=["ma_dealmaking"], beats_failed=["backstop"]),
        )

        def dead_manager(*a, **k):
            raise ManagerFailed("manager: invalid output after 2 attempts: garbage")

        monkeypatch.setattr(run, "run_synthesis_stage", dead_manager)

        code = run.main(["--today", "2026-07-16", "--root", str(fake_repo)])
        assert code == 1

        stub = json.loads((fake_repo / "issues" / "2026-07-16.json").read_text())
        assert stub["issue"]["run"]["status"] == "failed"
        assert stub["issue"]["failure"]["stage"] == "synthesis"
        # The degradation record survives into the stub for the audit trail.
        assert stub["sources_and_method"]["beats_failed"] == ["backstop"]


class TestValidationStage:
    def test_validation_exhaustion_stubs_the_run_with_stage_validation(self, fake_repo, monkeypatch):
        """Two retries and still structurally invalid: a validation stub, not a
        degradation. Driven through main() so the wiring — stub stage, exit code —
        is what an operator observes. Research and synthesis are stubbed to
        succeed so the run reaches stage 4."""
        import run
        from researchswarm.research import ResearchStage
        from researchswarm.validation import ValidationExhausted

        monkeypatch.setattr(
            run, "run_research_stage",
            lambda *a, **k: ResearchStage(beats_run=["ma_dealmaking"], beats_failed=[]),
        )

        draft_path = fake_repo / "runs" / SYNTH_RUN_ID / "issue-draft.json"

        def fake_synthesis(root, **kwargs):
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(json.dumps(_valid_draft()))
            return SimpleNamespace(draft=_valid_draft(), num_turns=1, cost_usd=0.0, attempts=1), draft_path

        monkeypatch.setattr(run, "run_synthesis_stage", fake_synthesis)

        def exhausted(*a, **k):
            raise ValidationExhausted("validation still blocking after 2 retries: empty_section@tldr_bullets")

        monkeypatch.setattr(run, "run_validation_stage", exhausted)

        code = run.main(["--today", "2026-07-16", "--root", str(fake_repo)])
        assert code == 1

        stub = json.loads((fake_repo / "issues" / "2026-07-16.json").read_text())
        assert stub["issue"]["run"]["status"] == "failed"
        assert stub["issue"]["failure"]["stage"] == "validation"


class TestPublishStage:
    def test_pipeline_publishes_and_commits(self, fake_repo, monkeypatch):
        """The whole point of the ticket: a run reaches disk as a published issue
        with derived stats, published_uncritiqued, a regenerated manifest, applied
        state edits, and one git commit. Research/synthesis/validation are stubbed
        so the test isolates the stage-6 wiring; publish runs for real."""
        import run
        from researchswarm.research import ResearchStage
        from researchswarm.validation import ValidationStageResult

        subprocess.run(["git", "init", "-q", str(fake_repo)], check=True)
        subprocess.run(["git", "-C", str(fake_repo), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(fake_repo), "config", "user.name", "T"], check=True)

        monkeypatch.setattr(
            run, "run_research_stage",
            lambda *a, **k: ResearchStage(beats_run=["ma_dealmaking"], beats_failed=[]),
        )

        draft = _valid_draft()
        # A promotion so the state-edit path is exercised end to end.
        draft["new_on_radar"] = [{
            "entity_id": "callio_tx", "name": "Callio Therapeutics", "type": "startup",
            "priority": "medium", "categories": ["funding"],
            "sources": [{"url": "https://ex.com/c", "publisher": "Endpoints",
                         "tier": "primary", "published_at": "2026-07-16"}],
            "promotion_proposal": {"promote_to_watchlist": True, "reason": "Dual-payload financing."},
        }]

        draft_path = fake_repo / "runs" / SYNTH_RUN_ID / "issue-draft.json"

        def fake_synthesis(root, **kwargs):
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(json.dumps(draft))
            return SimpleNamespace(draft=draft, num_turns=1, cost_usd=0.0, attempts=1), draft_path

        monkeypatch.setattr(run, "run_synthesis_stage", fake_synthesis)
        monkeypatch.setattr(
            run, "run_validation_stage",
            lambda **k: ValidationStageResult(draft=draft, retries_used=0, advisory=()),
        )

        code = run.main(["--today", "2026-07-16", "--root", str(fake_repo)])
        assert code == 0

        issue = json.loads((fake_repo / "issues" / "2026-07-16.json").read_text())
        assert issue["issue"]["run"]["status"] == "published_uncritiqued"
        assert issue["issue"]["run"]["critic_verdict"] == "not_run"
        assert issue["stats"]["new_on_radar"] == 1
        assert issue["stats"]["previous_issue"] is None  # run #1

        # The manifest was regenerated from disk, newest first, and includes it.
        manifest = json.loads((fake_repo / "issues" / "index.json").read_text())
        assert manifest["issues"][0]["id"] == "2026-07-16"

        # The promotion landed in state.
        watchlist = json.loads((fake_repo / "state" / "watchlist.json").read_text())
        assert any(e["entity_id"] == "callio_tx" for e in watchlist["entities"])

        # And the whole run is one commit citing the run_id.
        log = subprocess.run(
            ["git", "-C", str(fake_repo), "log", "--oneline"], capture_output=True, text=True
        ).stdout
        assert "publish 2026-07-16 (published_uncritiqued)" in log


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
