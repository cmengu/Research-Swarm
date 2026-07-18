"""`run.py --program <id>` — the v2 stage machine, driven in process.

The v2 twin of test_run_cli.py, and its acceptance criteria expressed the same
way: what an operator (or the OS scheduler) actually observes when the per-program
detective runs. Same house rules —

  - **nothing calls a real model.** The offline guard is on (conftest), and every
    stage that would shell out is replaced with a recorder, exactly as the v1 CLI
    tests replace `run.run_synthesis_stage`. A test that reached a real researcher
    would cost minutes and money per run;
  - **the gate tests write nothing.** A skipped day is a no-op, and the test
    asserts the filesystem is byte-identical afterwards;
  - **the fixture repo is a copy** of the real config, state and prompts, so these
    exercise the actually-committed files rather than a hand-built mock of them.

The publisher is deliberately NOT exercised as a published artifact: stage 6's
emission (`issues/<program_id>/<date>.json` + the manifest) is blocked on the open
frontend-contract tickets, so what is tested here is the SEAM — that the run
completes without it, says so, and hands the issue to an injected writer when one
is supplied.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

import run
from researchswarm.cadence import program_surge_v2
from researchswarm.critique import CritiqueStageResult
from researchswarm.manager import ManagerResult
from researchswarm.research import ResearchStageV2
from researchswarm.validation import ValidationStageResult

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "docs" / "schema" / "sample-issue-hmbd-001-2026-07-18.json"

PROGRAM = "hmbd-001"
TODAY = "2026-07-18"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _capture_info(caplog):
    """run.py speaks at INFO; the gate's whole observable behaviour is a log line."""
    caplog.set_level("INFO", logger="researchswarm")


@pytest.fixture
def fake_repo(tmp_path):
    """A copy of the real config, state and prompts, writable without touching the repo."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    shutil.copytree(REPO_ROOT / "state", tmp_path / "state")
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")
    return tmp_path


def _load_sample() -> dict:
    issue = json.loads(SAMPLE.read_text())
    issue.pop("_comment", None)
    return issue


def _entity_refs(issue: dict) -> set[str]:
    """Every entity_id the issue names anywhere, discoveries included.

    Used only to seed `state/entities/` in the fixture so the dangling-reference
    check has something to resolve against. Deliberately broader than the
    validator's own harvest — over-seeding the fixture cannot mask a real failure
    of the code under test here, which is the orchestration, not the gate.
    """
    ids: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("entity_id"), str):
                ids.add(node["entity_id"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(issue)
    return ids


@pytest.fixture
def seeded_repo(fake_repo):
    """A fixture repo whose state can actually carry the sample issue.

    Three seedings, each closing a gap between the committed seed state (empty by
    design — the roster migration is a deferred human curation session) and what
    a REAL run of this issue would have found:

      - a `state/entities/` record per referenced entity, so entity references
        resolve;
      - a thesis slot the sample's `thesis_updates` actually targets, with a
        non-null stance, so the loop is allowed to revise it (a dormant slot is
        refused, which is a separate test);
      - the program's `seed_competitors` narrowed to the one entity the sample
        accounts for, so the roster coverage check has an honest set.
    """
    issue = _load_sample()
    entities_dir = fake_repo / "state" / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)
    for entity_id in _entity_refs(issue):
        (entities_dir / f"{entity_id}.json").write_text(
            json.dumps({"entity_id": entity_id, "facts": {}, "drift_log": []}, indent=2) + "\n"
        )

    thesis = fake_repo / "state" / "thesis.json"
    payload = json.loads(thesis.read_text())
    payload["beliefs"].append({
        "id": "her3-target-vs-mechanism",
        "title": "HER3 target vs mechanism",
        "stance": "Target and mechanism validation advance together.",
        "drift_log": [],
        "origin": "seed",
        "stance_provenance": "owner",
    })
    thesis.write_text(json.dumps(payload, indent=2) + "\n")

    config = fake_repo / "config" / "programs" / "hmbd-001.toml"
    config.write_text(
        config.read_text().replace(
            'seed_competitors = ["asset_her3_dxd", "asset_ivonescimab"]',
            'seed_competitors = ["asset_her3_dxd"]',
        )
    )
    return fake_repo


class _Recorder:
    """Records every call, returns a fixed value. The house's stage double."""

    def __init__(self, result=None):
        self.calls: list[dict] = []
        self.args: list[tuple] = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.args.append(args)
        self.calls.append(kwargs)
        return self.result

    @property
    def called(self) -> bool:
        return bool(self.calls)


@pytest.fixture
def wired(monkeypatch, seeded_repo):
    """Every model-calling stage replaced by a recorder; the sample as the draft.

    Stages 2-5 are exercised in their own test modules; what this file is for is
    the SPINE — that run.py calls them in order, threads the right values between
    them, and then does stage 6 (derive, edit state, commit). So each is a double
    returning a well-formed result, and the assertions are about what run.py did
    with them.
    """
    issue = _load_sample()
    findings = {"biology_scan": {"quiet": False, "findings": []}}

    research = _Recorder(ResearchStageV2(
        apertures_run=[{"aperture": "biology_scan", "scope": "x", "status": "ok"}],
        apertures_degraded=[],
        findings_by_aperture=findings,
    ))

    def synthesis(root, **kwargs):
        draft_path = Path(root) / "runs" / kwargs["identity"].ctx.run_id / "issue-draft.json"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(json.dumps(issue, indent=2) + "\n")
        synthesis.calls.append(kwargs)
        return ManagerResult(draft=issue, num_turns=1, cost_usd=0.0, attempts=1), draft_path

    synthesis.calls = []

    validation = _Recorder(ValidationStageResult(draft=issue, retries_used=0, advisory=()))
    critique = _Recorder(CritiqueStageResult(draft=issue, status="published", verdict="pass"))
    commit = _Recorder(True)

    monkeypatch.setattr(run, "run_research_stage_v2", research)
    monkeypatch.setattr(run, "run_synthesis_stage_v2", synthesis)
    monkeypatch.setattr(run, "run_validation_stage_v2", validation)
    monkeypatch.setattr(run, "run_critique_stage_v2", critique)
    monkeypatch.setattr(run, "git_commit_run", commit)

    return {
        "root": seeded_repo, "issue": issue, "research": research,
        "synthesis": synthesis, "validation": validation, "critique": critique,
        "commit": commit,
    }


def _run(root, *args, publisher=None) -> int:
    return run.main(
        ["--program", PROGRAM, "--today", TODAY, "--root", str(root), *args],
        publisher=publisher,
    )


def _write_issue(root: Path, issue_id: str, *, status="published", window=True):
    """Put a prior issue in issues/<program>/ — the join point the gate reads."""
    issues = Path(root) / "issues" / PROGRAM
    issues.mkdir(parents=True, exist_ok=True)
    payload = {"issue": {"id": issue_id, "run": {"status": status}}}
    if window:
        payload["issue"]["coverage_window"] = {"from": issue_id, "to": issue_id}
    (issues / f"{issue_id}.json").write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# Stage 0 — the gate
# ---------------------------------------------------------------------------


class TestTheGate:
    def test_cold_start_is_always_a_run_day(self, fake_repo, caplog):
        """Run #1 has no interval to have elapsed, so it is due by definition."""
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert "cold_start" in caplog.text

    def test_a_monthly_program_is_not_due_three_days_later(self, fake_repo, caplog):
        _write_issue(fake_repo, "2026-07-15")
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert "not a run day for hmbd-001" in caplog.text
        assert "next due 2026-08-15" in caplog.text

    def test_a_skipped_day_writes_nothing(self, fake_repo):
        """No issue, no stub, no findings dir, no trace — the whole point of self-gating."""
        _write_issue(fake_repo, "2026-07-15")
        before = {p for p in fake_repo.rglob("*")}
        assert _run(fake_repo) == run.EXIT_OK
        assert {p for p in fake_repo.rglob("*")} == before

    def test_a_month_later_is_due(self, fake_repo, caplog):
        _write_issue(fake_repo, "2026-06-18")
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert "baseline_due" in caplog.text

    def test_push_forces_an_out_of_cadence_run(self, fake_repo, caplog):
        """Spec/02's third trigger: a manual run for a NAMED program, gate bypassed."""
        _write_issue(fake_repo, "2026-07-15")
        assert _run(fake_repo, "--push", "--dry-run") == run.EXIT_OK
        assert "is a run day for hmbd-001: push" in caplog.text

    def test_push_without_a_program_is_a_config_error(self, fake_repo):
        """Push is per-program by definition — there is no global push."""
        assert run.main(["--push", "--root", str(fake_repo)]) == run.EXIT_CONFIG_ERROR

    def test_the_gate_binds_to_the_last_COVERING_issue_not_the_last_file(self, fake_repo, caplog):
        """A stub covered no days, so it cannot hold the program off the dial."""
        _write_issue(fake_repo, "2026-06-18")
        _write_issue(fake_repo, "2026-07-16", status="failed", window=False)
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert "baseline_due" in caplog.text

    def test_an_unknown_program_is_a_config_error(self, fake_repo):
        assert run.main(
            ["--program", "no-such-drug", "--root", str(fake_repo)]
        ) == run.EXIT_CONFIG_ERROR

    def test_a_models_toml_missing_a_subscripted_role_is_a_config_error(self, fake_repo):
        """Stage 0 proves every role it will LATER subscript (spec/09 stage 0).

        `models_config["manager"]` is not read until stages 4 and 5, so a missing
        role used to sail through the config gate and surface as a raw KeyError
        traceback mid-run — after the research fan-out had already been paid for.
        """
        models = fake_repo / "config" / "models.toml"
        for role in run.REQUIRED_MODEL_ROLES_V2:
            original = models.read_text()
            models.write_text(
                "\n".join(
                    line for line in original.splitlines() if not line.startswith(f"{role} ")
                )
            )
            assert _run(fake_repo, "--dry-run") == run.EXIT_CONFIG_ERROR, role
            models.write_text(original)

    def test_the_role_gate_names_the_role_and_the_file(self):
        """The config VOICE, not a KeyError — the operator is told what to add."""
        with pytest.raises(ValueError) as exc:
            run._require_model_roles_v2({"critic": "codex"})
        assert "[models].manager" in str(exc.value)
        assert "models.toml" in str(exc.value)

    def test_the_researcher_default_has_exactly_one_home(self):
        """run.py's fallback and the id the issue RECORDS are one value (#7)."""
        from researchswarm.synthesis import RESEARCHER_MODEL_DEFAULT

        assert run.RESEARCHER_MODEL_DEFAULT_V2 is RESEARCHER_MODEL_DEFAULT

    def test_the_v2_state_loader_is_the_shared_one(self):
        """`_load_state_json_v2` was a byte-copy of an UNMODIFIED v1 helper, so
        reusing it costs no v1 edit and the delete-later exemption does not apply."""
        from researchswarm.state import _load_json

        assert run.load_state_json is _load_json
        assert not hasattr(run, "_load_state_json_v2")

    def test_an_unknown_baseline_is_a_config_error_not_a_silent_no_op(self, fake_repo):
        """A typo that silently never runs is the failure this system refuses."""
        config = fake_repo / "config" / "programs" / "hmbd-001.toml"
        config.write_text(config.read_text().replace('baseline = "monthly"', 'baseline = "lunar"'))
        assert _run(fake_repo) == run.EXIT_CONFIG_ERROR

    def test_v1_still_runs_without_the_program_flag(self, fake_repo, caplog):
        """The dispatch is additive: no --program, no v2 — v1's gate is untouched."""
        assert run.main(
            ["--today", "2026-07-14", "--root", str(fake_repo), "--dry-run"]
        ) == run.EXIT_OK
        assert "is not a run day" in caplog.text


class TestSurgeCannotFireYet:
    """The known gap, asserted rather than hidden.

    Spec/02 surges "any program with a competitor in that window", but nothing
    defines the attendee set — calendar.toml has no competitor field and no state
    file maps entities to windows. run.py therefore passes an EMPTY set, and these
    tests pin that so the day someone wires an attendee source, the test that
    changes is the one describing the gap.
    """

    def test_the_attendee_set_is_empty(self):
        assert run.COMPETITORS_IN_WINDOW_V2 == frozenset()

    def test_an_empty_attendee_set_can_never_surge(self):
        surge = object()  # a live window; the intersection is what decides
        assert program_surge_v2(surge, {"asset_her3_dxd"}, run.COMPETITORS_IN_WINDOW_V2) is None

    def test_a_matching_attendee_would_surge(self):
        """The seam is live code, not a stub: only the data source is missing."""
        surge = object()
        assert program_surge_v2(surge, {"asset_her3_dxd"}, {"asset_her3_dxd"}) is surge


# ---------------------------------------------------------------------------
# Stage 1-2 — prepare and the aperture fan-out
# ---------------------------------------------------------------------------


class TestPrepareAndDryRun:
    def test_dry_run_renders_every_active_aperture(self, fake_repo, caplog):
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        for aperture_id in ("biology_scan", "arena_scan:squamous-nsclc", "house_sweep"):
            assert f"[dry-run] {aperture_id}: rendered" in caplog.text

    def test_a_dormant_arena_is_planned_but_not_run(self, fake_repo, caplog):
        """1 + N + 1 counts the roster; the cost is the ACTIVE subset (spec/04)."""
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert "apertures: 4 planned, 3 active" in caplog.text
        assert "[dry-run] arena_scan:nrg1-fusion-solid-tumors" not in caplog.text

    def test_dry_run_writes_nothing(self, fake_repo):
        before = {p for p in fake_repo.rglob("*")}
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert {p for p in fake_repo.rglob("*")} == before

    def test_the_cold_start_window_comes_from_the_program_config(self, fake_repo, caplog):
        """cold_start_lookback_days ⚑ is a per-program dial, not a constant."""
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert "cold-start window 2026-07-11 → 2026-07-18" in caplog.text

    def test_the_coverage_window_joins_this_programs_previous_issue(self, fake_repo, caplog):
        _write_issue(fake_repo, "2026-06-18")
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert "coverage 2026-06-18 → 2026-07-18 (joins 2026-06-18)" in caplog.text

    def test_the_registry_watch_declares_itself_unwired(self, fake_repo, caplog):
        """Spec/09 stage 1 polls ClinicalTrials.gov. It is not built, and the run
        says so rather than implying the apertures saw a registry diff."""
        assert _run(fake_repo, "--dry-run") == run.EXIT_OK
        assert "registry watch not wired" in caplog.text


class TestResearchFailure:
    def test_every_aperture_dead_fails_the_run(self, monkeypatch, seeded_repo, caplog):
        monkeypatch.setattr(run, "run_research_stage_v2", _Recorder(ResearchStageV2(
            apertures_run=[], apertures_degraded=["biology_scan"], findings_by_aperture={},
        )))
        assert _run(seeded_repo) == run.EXIT_RUN_FAILED
        assert "run failed at research" in caplog.text

    def test_no_stub_is_written_because_the_issue_shape_is_blocked(self, monkeypatch, seeded_repo, caplog):
        """A v2 stub is an EMITTED issue, so it rides the publisher seam. The
        failure is loud in the log and the exit code; it is not laundered."""
        monkeypatch.setattr(run, "run_research_stage_v2", _Recorder(ResearchStageV2(
            apertures_run=[], apertures_degraded=[], findings_by_aperture={},
        )))
        assert _run(seeded_repo) == run.EXIT_RUN_FAILED
        assert not list((seeded_repo / "issues").rglob("*.json"))
        assert "no stub written" in caplog.text


# ---------------------------------------------------------------------------
# The spine — stages 2-5 called in order, values threaded
# ---------------------------------------------------------------------------


class TestTheSpine:
    def test_every_stage_runs_in_order(self, wired):
        assert _run(wired["root"]) == run.EXIT_OK
        for stage in ("research", "synthesis", "validation", "critique"):
            assert wired[stage].called if stage != "synthesis" else wired[stage].calls

    def test_the_program_roster_reaches_the_validator(self, wired):
        """The roster is the coverage accountability set — promoted edges plus
        unpromoted seeds — and it exists only on the v2 path."""
        _run(wired["root"])
        assert wired["validation"].calls[0]["roster"] == {"asset_her3_dxd"}

    def test_the_validator_sees_this_programs_issue_directory(self, wired):
        _run(wired["root"])
        assert wired["validation"].calls[0]["issues_dir"].name == PROGRAM

    def test_the_findings_corpus_is_handed_to_the_critic_in_memory(self, wired):
        """Aperture ids carry a colon, so on-disk naming is the research stage's
        private concern — the corpus travels in memory (spec/04 transport)."""
        _run(wired["root"])
        assert wired["critique"].calls[0]["findings_by_aperture"] == {
            "biology_scan": {"quiet": False, "findings": []}
        }

    def test_the_stale_calendar_advisory_reaches_the_validator(self, wired):
        """A stale calendar is the one failure that would otherwise be silent."""
        _run(wired["root"])
        assert wired["validation"].calls[0]["calendar_stale"] is True

    def test_the_draft_that_comes_OUT_of_the_critic_is_the_one_stage_6_uses(self, wired, tmp_path):
        edited = dict(wired["issue"])
        edited["headline"] = {**edited["headline"], "title": "the edited one"}
        wired["critique"].result = CritiqueStageResult(
            draft=edited, status="published", verdict="pass"
        )
        _run(wired["root"])
        draft = json.loads(next((wired["root"] / "runs").rglob("issue-draft.json")).read_text())
        assert draft["headline"]["title"] == "the edited one"


# ---------------------------------------------------------------------------
# Stage 6 — derived stats, state edits, the commit
# ---------------------------------------------------------------------------


class TestDerivedStats:
    def test_stats_are_derived_from_the_arrays_not_trusted(self, wired):
        """Spec/07: stats is derived, never authored. The orchestrator overwrites
        whatever the manager put there."""
        wired["issue"]["stats"] = {"competitors_moved": 999}
        _run(wired["root"])
        draft = json.loads(next((wired["root"] / "runs").rglob("issue-draft.json")).read_text())
        # The v2 counts, derived by validator.derive_stats — which dispatches on
        # the issue's own schema_version, so a v2 issue is counted v2's way.
        assert draft["stats"]["competitors_moved"] == len(wired["issue"]["competitors"])
        assert draft["stats"]["previous_issue"] is None

    def test_the_run_status_is_the_critics_not_the_managers(self, wired):
        wired["issue"]["issue"]["run"]["status"] = "published_uncritiqued"
        wired["critique"].result = CritiqueStageResult(
            draft=wired["issue"], status="published_with_unresolved_findings", verdict="block"
        )
        _run(wired["root"])
        draft = json.loads(next((wired["root"] / "runs").rglob("issue-draft.json")).read_text())
        assert draft["issue"]["run"]["status"] == "published_with_unresolved_findings"


class TestStateEdits:
    def test_a_seed_competitor_earns_its_first_relation_edge(self, wired):
        """Promotion IS writing the edge (spec/03 discovery/promotion/the edge).
        The cold-start seed was untyped; the issue types it."""
        _run(wired["root"])
        edges = json.loads(
            (wired["root"] / "state" / "programs" / PROGRAM / "edges.json").read_text()
        )
        edge = next(e for e in edges["edges"] if e["entity_id"] == "asset_her3_dxd")
        assert edge["relation"] == "target_twin"
        assert edge["promoted_by"].startswith("run_")
        assert edge["drift_log"][0]["action"] == "promoted"

    def test_a_discovery_that_was_not_accepted_is_not_promoted(self, wired):
        """The sample's discovery proposes NOT to promote. A proposal that is
        refused must not write an edge — that is the whole point of a proposal."""
        _run(wired["root"])
        edges = json.loads(
            (wired["root"] / "state" / "programs" / PROGRAM / "edges.json").read_text()
        )
        assert "asset_her3_car_t" not in {e["entity_id"] for e in edges["edges"]}

    def test_entity_facts_lift_to_the_shared_layer_citing_the_run(self, wired):
        _run(wired["root"])
        record = json.loads(
            (wired["root"] / "state" / "entities" / "asset_her3_dxd.json").read_text()
        )
        assert record["facts"]["status"]["value"] == "developing"
        assert record["facts"]["status"]["established_by"].startswith("run_")
        assert record["facts"]["status"]["issue"] == TODAY

    def test_the_read_through_never_lifts_to_the_shared_layer(self, wired):
        """Facts are global, read-throughs are per-program. Lifting the judgment
        is exactly the silo-drift the split exists to kill, inverted."""
        _run(wired["root"])
        record = json.loads(
            (wired["root"] / "state" / "entities" / "asset_her3_dxd.json").read_text()
        )
        assert "read_through" not in record["facts"]
        assert "priority" not in record["facts"]

    def test_a_correction_appends_rather_than_overwriting(self, wired):
        _run(wired["root"])
        record = json.loads(
            (wired["root"] / "state" / "entities" / "asset_her3_dxd.json").read_text()
        )
        established = [e for e in record["drift_log"] if e["field"] == "status"]
        assert established and established[0]["action"] == "established"
        assert established[0]["run_id"].startswith("run_")

    def test_an_active_thesis_slot_is_revised_with_a_drift_log_entry(self, wired):
        _run(wired["root"])
        thesis = json.loads((wired["root"] / "state" / "thesis.json").read_text())
        slot = next(b for b in thesis["beliefs"] if b["id"] == "her3-target-vs-mechanism")
        assert slot["stance"].startswith("Target validation is running years ahead")
        assert slot["drift_log"][-1]["cycle_id"].startswith("run_")
        assert thesis["last_edited_by"] == "loop"

    def test_a_dormant_slot_is_never_authored_into(self, wired):
        """The loop may revise an active stance; it may never author one into a
        slot the owner never seeded (spec/03 clause 4)."""
        thesis_path = wired["root"] / "state" / "thesis.json"
        payload = json.loads(thesis_path.read_text())
        for belief in payload["beliefs"]:
            if belief["id"] == "her3-target-vs-mechanism":
                belief["stance"] = None
        thesis_path.write_text(json.dumps(payload, indent=2) + "\n")

        _run(wired["root"])
        after = json.loads(thesis_path.read_text())
        slot = next(b for b in after["beliefs"] if b["id"] == "her3-target-vs-mechanism")
        assert slot["stance"] is None

    def test_interests_toml_is_never_machine_written(self, wired):
        """Governance clause 4: the steering wheel is human-owned. The sample's
        discovery PROPOSES an interest, and the run must refuse to write it."""
        path = wired["root"] / "config" / "interests.toml"
        before = path.read_bytes()
        _run(wired["root"])
        assert path.read_bytes() == before

    def test_the_program_config_is_never_machine_written(self, wired):
        path = wired["root"] / "config" / "programs" / "hmbd-001.toml"
        before = path.read_bytes()
        _run(wired["root"])
        assert path.read_bytes() == before


class TestTheCommit:
    def test_one_commit_cites_the_run_id_and_the_program(self, wired):
        _run(wired["root"])
        assert len(wired["commit"].calls) == 1
        assert wired["commit"].calls[0]["message"].startswith("run run_")
        assert PROGRAM in wired["commit"].calls[0]["message"]

    def test_a_failed_commit_is_not_a_failed_run(self, wired):
        """The artifacts are on disk either way; the commit is the review trail."""
        wired["commit"].result = False
        assert _run(wired["root"]) == run.EXIT_OK


# ---------------------------------------------------------------------------
# The publisher seam — wired to the real emitter by #83
# ---------------------------------------------------------------------------


class TestThePublisherSeam:
    def test_the_run_publishes_all_three_files_by_default(self, wired):
        """#83 unblocked the seam: an unconfigured run now emits the issue, its
        per-program manifest, and the cross-program registry (spec/08)."""
        assert _run(wired["root"]) == run.EXIT_OK
        issues = wired["root"] / "issues"
        assert (issues / PROGRAM / f"{TODAY}.json").exists()
        assert (issues / PROGRAM / "index.json").exists()
        assert (issues / "index.json").exists()

    def test_state_edits_and_the_commit_still_happen(self, wired):
        """Stage 6's state half stays run.py's — publish.py never calls git."""
        _run(wired["root"])
        assert (wired["root"] / "state" / "programs" / PROGRAM / "edges.json").exists()
        assert wired["commit"].called

    def test_an_injected_publisher_receives_the_finished_issue(self, wired):
        seen = {}

        def publisher(*, issue, program_id, root, run_id):
            seen.update(issue=issue, program_id=program_id, run_id=run_id)
            path = Path(root) / "issues" / program_id / f"{TODAY}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(issue, indent=2) + "\n")
            return path

        assert _run(wired["root"], publisher=publisher) == run.EXIT_OK
        assert seen["program_id"] == PROGRAM
        assert seen["run_id"].startswith("run_")
        # The issue reaching the publisher is the FINISHED one: stats derived,
        # run fields stamped — never the manager's draft.
        assert seen["issue"]["stats"]["previous_issue"] is None
        assert seen["issue"]["issue"]["run"]["status"] == "published"

    def test_the_published_path_is_staged_for_the_commit(self, wired):
        def publisher(*, issue, program_id, root, run_id):
            path = Path(root) / "issues" / program_id / f"{TODAY}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
            return path

        _run(wired["root"], publisher=publisher)
        # git_commit_run(root, run_id, paths, *, message=...)
        staged = wired["commit"].args[0][2]
        assert any(str(p).endswith(f"{TODAY}.json") for p in staged)


def test_the_run_id_and_the_window_share_the_faked_date(fake_repo, caplog):
    """A faked run must not stamp a real-dated findings dir onto a fake window."""
    assert _run(fake_repo, "--dry-run") == run.EXIT_OK
    assert f"run_id=run_{date.fromisoformat(TODAY):%Y%m%d}" in caplog.text
