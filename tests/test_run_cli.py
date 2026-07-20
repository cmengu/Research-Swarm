"""run.py end to end, driven as a real process against the real repo.

These are the ticket's acceptance criteria expressed as behaviour: what an
operator (or the OS scheduler) actually observes.
"""

import json
import os
import re
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
        state edits, and one git commit. Research/synthesis/validation/critique are
        stubbed so the test isolates the stage-6 wiring; publish runs for real."""
        import run
        from researchswarm.critique import CritiqueStageResult, PUBLISHED_UNCRITIQUED
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
        # Stage 5 is isolated here (its own tests live in test_critic.py / a
        # dedicated wiring class); stub it to a not_run outcome so this test keeps
        # asserting the published_uncritiqued publish path end to end.
        monkeypatch.setattr(
            run, "run_critique_stage",
            lambda *a, **k: CritiqueStageResult(
                draft=draft, status=PUBLISHED_UNCRITIQUED, verdict="not_run", reason="stubbed"
            ),
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


class TestCritiqueStage:
    """Stage 5 → publish wiring: each critic outcome lands as the right published
    run.status and critic_report, and stage 4's validator_report is preserved."""

    def _drive(self, fake_repo, monkeypatch, critic_result, *, draft=None):
        import run
        from researchswarm.research import ResearchStage
        from researchswarm.validation import ValidationStageResult

        subprocess.run(["git", "init", "-q", str(fake_repo)], check=True)
        subprocess.run(["git", "-C", str(fake_repo), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(fake_repo), "config", "user.name", "T"], check=True)

        the_draft = draft if draft is not None else _valid_draft()
        # Stage 4 stamps a validator_report; publish must preserve it under the critic outcome.
        the_draft["critic_report"] = {
            "validator_report": {"passed": True, "retries_used": 1,
                                 "findings": [{"kind": "empty_section", "where": "x", "note": "fixed"}]}
        }

        monkeypatch.setattr(
            run, "run_research_stage",
            lambda *a, **k: ResearchStage(beats_run=["ma_dealmaking"], beats_failed=[]),
        )
        draft_path = fake_repo / "runs" / SYNTH_RUN_ID / "issue-draft.json"

        def fake_synthesis(root, **kwargs):
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(json.dumps(the_draft))
            return SimpleNamespace(draft=the_draft, num_turns=1, cost_usd=0.0, attempts=1), draft_path

        monkeypatch.setattr(run, "run_synthesis_stage", fake_synthesis)
        monkeypatch.setattr(
            run, "run_validation_stage",
            lambda **k: ValidationStageResult(draft=the_draft, retries_used=0, advisory=()),
        )
        # The stage always returns the (possibly edited) draft; these outcome tests
        # only care about status/verdict/findings, so inject the_draft here rather
        # than restate it in every case.
        import dataclasses
        monkeypatch.setattr(
            run, "run_critique_stage",
            lambda *a, **k: dataclasses.replace(critic_result, draft=the_draft),
        )

        code = run.main(["--today", "2026-07-16", "--root", str(fake_repo)])
        issue = json.loads((fake_repo / "issues" / "2026-07-16.json").read_text())
        manifest = json.loads((fake_repo / "issues" / "index.json").read_text())
        return code, issue, manifest

    @pytest.mark.parametrize(
        "verdict,status",
        [("pass", "published"),
         ("pass_with_advisories", "published"),
         ("blocked", "published_with_unresolved_findings"),
         ("not_run", "published_uncritiqued")],
    )
    def test_each_verdict_maps_to_its_status(self, fake_repo, monkeypatch, verdict, status):
        from researchswarm.critique import CritiqueStageResult

        # draft is a placeholder — _drive injects the real one the stage returns.
        result = CritiqueStageResult(draft={}, status=status, verdict=verdict)
        code, issue, manifest = self._drive(fake_repo, monkeypatch, result)
        assert code == 0
        assert issue["issue"]["run"]["status"] == status
        assert issue["issue"]["run"]["critic_verdict"] == verdict
        # critic_retries is orchestrator-owned too — 0 for now (#35's loop), never
        # a manager-authored value.
        assert issue["issue"]["run"]["critic_retries"] == 0
        assert manifest["issues"][0]["status"] == status

    def test_blocking_findings_publish_in_the_report(self, fake_repo, monkeypatch):
        from researchswarm.critique import CritiqueStageResult

        blocking = ({"kind": "overclaim", "where": "headline", "note": "too strong"},)
        result = CritiqueStageResult(draft={}, status="published_with_unresolved_findings",
                                     verdict="blocked", blocking_findings=blocking)
        _, issue, _ = self._drive(fake_repo, monkeypatch, result)
        assert issue["critic_report"]["blocking_findings"] == list(blocking)

    def test_validator_report_is_preserved_inside_critic_report(self, fake_repo, monkeypatch):
        from researchswarm.critique import CritiqueStageResult

        result = CritiqueStageResult(draft={}, status="published", verdict="pass")
        _, issue, _ = self._drive(fake_repo, monkeypatch, result)
        vr = issue["critic_report"]["validator_report"]
        assert vr["passed"] is True and vr["retries_used"] == 1
        assert vr["findings"][0]["kind"] == "empty_section"

    def test_not_run_reason_lands_in_the_report(self, fake_repo, monkeypatch):
        from researchswarm.critique import CritiqueStageResult

        result = CritiqueStageResult(draft={}, status="published_uncritiqued", verdict="not_run",
                                     reason="codex binary not found on PATH")
        _, issue, _ = self._drive(fake_repo, monkeypatch, result)
        assert issue["critic_report"]["verdict"] == "not_run"
        assert issue["critic_report"]["reason"] == "codex binary not found on PATH"

    def test_manager_authored_critic_catches_survive_to_publish_and_stats(self, fake_repo, monkeypatch):
        """A critic_catch is a manager-authored record of a rejected claim (its
        population is #35's job, but the plumbing must already carry it through).
        Drive a nonempty quiet_this_cycle.critic_catches through critique→publish
        and assert it survives the issue AND is counted in the derived stats."""
        from researchswarm.critique import CritiqueStageResult

        draft = _valid_draft()
        draft["quiet_this_cycle"]["critic_catches"] = [
            {"claim": "Zentalis raising $400M at a $2.1B valuation",
             "rejected_because": "provenance_stale",
             "detail": "Every repeat traces to a single 12 Mar Bloomberg piece.",
             "caught_by": "critic", "sources": []}
        ]
        result = CritiqueStageResult(draft={}, status="published", verdict="pass")
        _, issue, _ = self._drive(fake_repo, monkeypatch, result, draft=draft)

        catches = issue["quiet_this_cycle"]["critic_catches"]
        assert len(catches) == 1
        assert catches[0]["rejected_because"] == "provenance_stale"
        # Derived, never authored: the stat is recomputed from the array.
        assert issue["stats"]["critic_catches"] == 1


def _seed_verified_window(fake_repo, window_id, starts, ends, verified_at="2026-07-10T07:00:00"):
    """Fill in a window's dates in the fake repo's calendar.toml, exactly as a
    prior run's verification would have — so surge resolves from real config."""
    from researchswarm.calendar import write_verified_dates

    write_verified_dates(
        fake_repo / "config" / "calendar.toml",
        verified_at,
        {window_id: {"starts": starts, "ends": ends}},
    )



def _unverify_calendar(root) -> None:
    """Blank every window's verification in a fake repo's calendar.

    The shipped calendar is runtime data: the loop resolves windows against the
    societies' own pages and writes the dates back. Any test asserting on the
    UNverified path has to say so explicitly, or it passes only until the
    verifier next succeeds.
    """
    path = root / "config" / "calendar.toml"
    text = path.read_text()
    for key in ("starts", "ends", "verified_at"):
        text = re.sub(rf'^{key}(\s*)= .*$', rf'{key}\1= ""', text, flags=re.M)
    path.write_text(text)


class TestSurge:
    def test_a_verified_window_makes_a_non_run_day_a_run_day(self, fake_repo):
        """The whole point: ASCO Monday must not read at the Tuesday rate. A
        verified window containing today switches to daily, so Tuesday runs."""
        # ASCO 2026-07-13 → 2026-07-17 covers Tuesday the 14th (a baseline no-op).
        _seed_verified_window(fake_repo, "asco", "2026-07-13", "2026-07-17")
        result = run_cli("--today", "2026-07-14", "--root", str(fake_repo))
        assert result.returncode == 0
        assert "not a run day" not in result.stderr
        assert "surge:" in result.stderr

    def test_stale_calendar_disables_a_live_verified_window(self, tmp_path):
        """A rotted calendar whose previously-verified window still CONTAINS today
        must surge nothing — behaviour matches the 'surge disabled' marker rather
        than lying about it (spec/02 staleness table)."""
        import run
        from datetime import date
        from researchswarm.cadence import load_cadence
        from researchswarm.calendar import Calendar, Window

        live = Window(
            id="asco", name="ASCO Annual Meeting", typical_window="", note="",
            source="https://asco.org", starts="2026-07-13", ends="2026-07-17",
            verified_at="2026-07-10T07:00:00",
        )
        cadence = load_cadence(REPO_ROOT / "config" / "cadence.toml")

        # A FRESH calendar surges on the 16th — the window is verified and live.
        fresh = Calendar(valid_through=date(2027, 1, 31), windows=(live,))
        surge, stale, _ = run._resolve_surge_and_staleness(cadence, fresh, date(2026, 7, 16), tmp_path)
        assert surge is not None and stale is False

        # Rot it (valid_through passed): no surge, and the marker fires.
        rotted = Calendar(valid_through=date(2026, 1, 31), windows=(live,))
        surge, stale, reason = run._resolve_surge_and_staleness(cadence, rotted, date(2026, 7, 16), tmp_path)
        assert surge is None
        assert stale is True and reason  # calendar_stale advisory will be filed

    def test_unverified_calendar_surges_nothing_and_says_so(self, fake_repo):
        """An unverified calendar surges nothing and the stale marker explains the
        gap (an honest gap beats a confident guess).

        The unverified state is written here rather than inherited. `fake_repo`
        copies the REAL config, and config/calendar.toml is loop-maintained — the
        first cycle that actually verified a window (ASH, against hematology.org)
        broke this test, because it had been relying on the shipped file staying
        forever unverified. A test about unverified behaviour must construct that
        state, not borrow it from a file the system rewrites."""
        _unverify_calendar(fake_repo)
        result = run_cli("--today", "2026-07-14", "--root", str(fake_repo))
        # Tuesday with an unverified calendar is still a no-op...
        assert "not a run day" in result.stderr
        # ...and a baseline day loudly reports the calendar stale.
        monday = run_cli("--today", "2026-07-13", "--root", str(fake_repo))
        assert "conference calendar stale" in monday.stderr

    def test_missing_verifier_model_is_a_config_error(self, fake_repo):
        """The verifier id has ONE home in config — a missing key fails loudly via
        the same _config_error path the other model ids get, never a shadow default."""
        models = fake_repo / "config" / "models.toml"
        models.write_text(models.read_text().replace('verifier = "sonnet"', ""))
        # A baseline run day reaches Stage 1 verification (not --dry-run).
        result = run_cli("--today", "2026-07-16", "--root", str(fake_repo), research=True)
        assert result.returncode == 2
        assert "verifier is required" in result.stderr

    def test_published_issue_carries_run_surge(self, fake_repo, monkeypatch):
        """Inside a verified window the published issue stamps run.surge =
        {window, day, of}, and the manifest carries it so the dropdown can group
        an ASCO week without opening five files."""
        import run
        from researchswarm.critique import CritiqueStageResult, PUBLISHED_UNCRITIQUED
        from researchswarm.research import ResearchStage
        from researchswarm.validation import ValidationStageResult

        subprocess.run(["git", "init", "-q", str(fake_repo)], check=True)
        subprocess.run(["git", "-C", str(fake_repo), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(fake_repo), "config", "user.name", "T"], check=True)

        # Window 2026-07-13 → 2026-07-17 (5 days); the run is Thursday the 16th → day 4.
        _seed_verified_window(fake_repo, "asco", "2026-07-13", "2026-07-17")

        draft = _valid_draft()
        draft_path = fake_repo / "runs" / SYNTH_RUN_ID / "issue-draft.json"

        def fake_synthesis(root, **kwargs):
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(json.dumps(draft))
            return SimpleNamespace(draft=draft, num_turns=1, cost_usd=0.0, attempts=1), draft_path

        monkeypatch.setattr(
            run, "run_research_stage",
            lambda *a, **k: ResearchStage(beats_run=["ma_dealmaking"], beats_failed=[]),
        )
        monkeypatch.setattr(run, "run_synthesis_stage", fake_synthesis)
        monkeypatch.setattr(
            run, "run_validation_stage",
            lambda **k: ValidationStageResult(draft=draft, retries_used=0, advisory=()),
        )
        monkeypatch.setattr(
            run, "run_critique_stage",
            lambda *a, **k: CritiqueStageResult(
                draft=draft, status=PUBLISHED_UNCRITIQUED, verdict="not_run", reason="stubbed"
            ),
        )

        code = run.main(["--today", "2026-07-16", "--root", str(fake_repo)])
        assert code == 0

        issue = json.loads((fake_repo / "issues" / "2026-07-16.json").read_text())
        assert issue["issue"]["run"]["surge"] == {
            "window": "ASCO Annual Meeting", "day": 4, "of": 5,
        }
        manifest = json.loads((fake_repo / "issues" / "index.json").read_text())
        assert manifest["issues"][0]["surge"]["window"] == "ASCO Annual Meeting"

    def test_baseline_run_has_no_surge_key(self, fake_repo, monkeypatch):
        """Absent, not null, on a baseline run — the manager is told to omit it and
        the orchestrator owns the field, so a baseline issue never carries surge."""
        import run
        from researchswarm.critique import CritiqueStageResult, PUBLISHED_UNCRITIQUED
        from researchswarm.research import ResearchStage
        from researchswarm.validation import ValidationStageResult

        subprocess.run(["git", "init", "-q", str(fake_repo)], check=True)
        subprocess.run(["git", "-C", str(fake_repo), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(fake_repo), "config", "user.name", "T"], check=True)

        draft = _valid_draft()
        # Even if the manager wrongly emitted a surge, the orchestrator strips it.
        draft["issue"]["run"]["surge"] = {"window": "bogus", "day": 1, "of": 1}
        draft_path = fake_repo / "runs" / SYNTH_RUN_ID / "issue-draft.json"

        def fake_synthesis(root, **kwargs):
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(json.dumps(draft))
            return SimpleNamespace(draft=draft, num_turns=1, cost_usd=0.0, attempts=1), draft_path

        monkeypatch.setattr(
            run, "run_research_stage",
            lambda *a, **k: ResearchStage(beats_run=["ma_dealmaking"], beats_failed=[]),
        )
        monkeypatch.setattr(run, "run_synthesis_stage", fake_synthesis)
        monkeypatch.setattr(
            run, "run_validation_stage",
            lambda **k: ValidationStageResult(draft=draft, retries_used=0, advisory=()),
        )
        monkeypatch.setattr(
            run, "run_critique_stage",
            lambda *a, **k: CritiqueStageResult(
                draft=draft, status=PUBLISHED_UNCRITIQUED, verdict="not_run", reason="stubbed"
            ),
        )

        run.main(["--today", "2026-07-16", "--root", str(fake_repo)])
        issue = json.loads((fake_repo / "issues" / "2026-07-16.json").read_text())
        assert "surge" not in issue["issue"]["run"]


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


class TestTheBackfillWindow:
    """`--since` overrides where coverage starts — the seeding-session lever.

    A healthy cadence binds the window to the last issue that covered days, which
    is two or three days wide. That is correct for a cycle and useless for a first
    look: every section reads empty because almost nothing happens in 72 hours.
    Overriding it re-reads days another issue may already cover, so it warns.
    """

    def test_since_widens_the_window_and_says_it_is_a_backfill(self, fake_repo):
        result = run_cli("--program", "hmbd-001", "--push", "--since", "2026-05-01",
                         "--today", "2026-07-20", "--root", str(fake_repo), "--dry-run")
        assert "BACKFILL" in result.stderr
        # The override applies whatever the join would have been — including the
        # cold-start fallback, which is the state a fresh install is actually in.
        assert "overridden to 2026-05-01" in result.stderr

    def test_without_since_the_join_is_untouched(self, fake_repo):
        """The override must not leak into a normal cadence run."""
        result = run_cli("--program", "hmbd-001", "--push", "--today", "2026-07-20",
                         "--root", str(fake_repo), "--dry-run")
        assert "BACKFILL" not in result.stderr
