"""`run.py --program <id>` with the fourth aperture kind wired in (spec #92).

The end-to-end companion to test_run_v2.py, and the same acceptance criteria:
what an operator observes when a cycle also gathers background. Where that file
doubles every model-calling stage, this one leaves the DOSSIER path real and
injects the subprocess runner underneath it — so what is exercised here is the
whole chain the wiring is responsible for:

    plan → render → call → validate at the findings seam → merge with provenance
         → write through the state-edit path → stage into the run's one commit

House rules, unchanged and load-bearing:

  - **nothing calls a real model.** The offline guard (conftest) is on, and
    `researcher.run_researcher_v2` refuses `subprocess.run` outright while it is
    set — so a test that forgot to inject would fail loudly rather than dial out.
  - **every runner is injected**, through `run.main(runner=...)`, which is the
    seam the wiring added for exactly this.
  - **fully deterministic.** The canned envelope echoes the run's own id, read
    back off the prompt the run rendered, so there is no clock in the assertions.

Spec: docs/spec/03-state-and-governance.md, docs/spec/04-researchers.md,
      https://github.com/cmengu/Research-Swarm/issues/92
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

import run
from researchswarm.apertures import DOSSIER_REFRESH_DAYS
from researchswarm.critique import CritiqueStageResult
from researchswarm.manager import ManagerResult
from researchswarm.research import ResearchStageV2
from researchswarm.validation import ValidationStageResult

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "docs" / "schema" / "sample-issue-hmbd-001-2026-07-18.json"

PROGRAM = "hmbd-001"
TODAY = "2026-07-18"
COMPANY = "co_remegen"
APERTURE = f"dossier_scan:{COMPANY}"

RUN_ID_RE = re.compile(r"run_\d{8}_\d{4}")
PROMPT_RUN_ID_RE = re.compile(r'"run_id":\s*"(run_\d{8}_\d{4})"')


# ---------------------------------------------------------------------------
# The canned scan
# ---------------------------------------------------------------------------


def dossier_payload(run_id: str, *, quiet: bool = False, **overrides) -> dict:
    """One well-formed `dossier_scan` envelope — the shape the seam accepts.

    Written out in full rather than trimmed to what this file asserts on, because
    the point of an end-to-end test is that the REAL gate passes it: every
    required key, the null coverage window that declares the exemption, per-field
    provenance citing this run, and `pivots`/`setbacks` present-but-empty (a claim
    that we looked, which is not the same claim as silence).
    """
    source = {
        "url": "https://www.hkexnews.hk/remegen-2024-annual-report",
        "publisher": "HKEXnews",
        "tier": "primary",
        "published_at": "2026-03-28",
        "paywalled": False,
    }
    provenance = {"established_by": run_id, "sources": [source]}
    dossier = None if quiet else {
        "entity_id": COMPANY,
        "kind": "company",
        "as_of": TODAY,
        "identity": {
            "legal_name": "RemeGen Co., Ltd.",
            "aliases": ["RemeGen"],
            "founded": "2008",
            "hq": "Yantai, China",
            "status": "public",
            "listings": [{"exchange": "HKEX", "ticker": "9995"}],
            "provenance": provenance,
        },
        "pivots": [],
        "setbacks": [
            {
                "date": "2025-11-04",
                "kind": "discontinuation",
                "detail": "Discontinued the RC48 gastric monotherapy expansion.",
                "program": "RC48",
                "provenance": provenance,
            }
        ],
        "coverage": {"thin_sections": ["funding"]},
    }
    payload = {
        "aperture": APERTURE,
        "program_id": PROGRAM,
        "run_id": run_id,
        # Null, not absent: the window exemption is a DECLARED fact in the
        # payload, so it cannot be confused with a model that simply forgot.
        "coverage_window": None,
        "quiet": quiet,
        "findings": [],
        "dossier": dossier,
        "coverage_notes": {
            "scope_run": "RemeGen company history",
            "entities_checked": [COMPANY],
            "notes": "HKEX filings read; no US filings exist for this issuer.",
        },
        "errors": [],
    }
    payload.update(overrides)
    return payload


class FakeRunner:
    """A `subprocess.run` double that answers the dossier researcher.

    Reads the run_id back off the rendered prompt so the echo checks in the
    findings gate pass without the test knowing what time it is — the run's own
    identity is the only nondeterministic value in the loop, and this closes over
    it rather than freezing the clock.
    """

    def __init__(self, payload=dossier_payload, *, returncode: int = 0):
        self.prompts: list[str] = []
        self.payload = payload
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        prompt = command[2]
        self.prompts.append(prompt)
        # The run's OWN id, read off the output-contract line — not the first
        # run_id in the prompt, which on a refresh is the PRIOR run's provenance
        # rendered into the extend-don't-restate block.
        match = PROMPT_RUN_ID_RE.search(prompt)
        result = json.dumps(self.payload(match.group(1) if match else "run_unknown"))
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=json.dumps({"result": result}), stderr=""
        )


# ---------------------------------------------------------------------------
# Fixtures — the v2 spine doubled, the dossier path real
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _capture_info(caplog):
    caplog.set_level("INFO", logger="researchswarm")


def _load_sample() -> dict:
    issue = json.loads(SAMPLE.read_text())
    issue.pop("_comment", None)
    return issue


def _entity_refs(issue: dict) -> set[str]:
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
def repo(tmp_path):
    """The real config, state and prompts, plus one COMPANY in the entity layer.

    The company record is what makes this a discovery test rather than a
    hand-seeded one: `state/entities/` is where a competitor sighted in an earlier
    cycle lands, so a record with `kind: company` and no dossier beside it is
    exactly the state a newly discovered competitor leaves behind (story 40).
    """
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    shutil.copytree(REPO_ROOT / "state", tmp_path / "state")
    shutil.copytree(REPO_ROOT / "prompts", tmp_path / "prompts")

    issue = _load_sample()
    entities = tmp_path / "state" / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    for entity_id in _entity_refs(issue):
        (entities / f"{entity_id}.json").write_text(
            json.dumps({"entity_id": entity_id, "facts": {}, "drift_log": []}, indent=2) + "\n"
        )
    (entities / f"{COMPANY}.json").write_text(
        json.dumps(
            {"entity_id": COMPANY, "kind": "company", "facts": {}, "drift_log": []}, indent=2
        )
        + "\n"
    )

    thesis = tmp_path / "state" / "thesis.json"
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

    config = tmp_path / "config" / "programs" / "hmbd-001.toml"
    config.write_text(
        config.read_text().replace(
            'seed_competitors = ["asset_her3_dxd", "asset_ivonescimab"]',
            'seed_competitors = ["asset_her3_dxd"]',
        )
    )
    return tmp_path


class _Recorder:
    def __init__(self, result=None):
        self.calls: list[dict] = []
        self.args: list[tuple] = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.args.append(args)
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def wired(monkeypatch, repo):
    """Stages 2-5 doubled; the dossier fan-out and the dossier writer left REAL.

    The cycle's own apertures are somebody else's test (test_run_v2.py). What must
    stay real here is everything #92 wired: the planner, the renderer, the
    researcher seam, the findings gate, the merge, the state-edit path and the
    commit staging.
    """
    issue = _load_sample()

    research = _Recorder(ResearchStageV2(
        apertures_run=[{"aperture": "biology_scan", "scope": "x", "status": "ok"}],
        apertures_degraded=[],
        findings_by_aperture={"biology_scan": {"quiet": False, "findings": []}},
    ))

    def synthesis(root, **kwargs):
        draft_path = Path(root) / "runs" / kwargs["identity"].ctx.run_id / "issue-draft.json"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(json.dumps(issue, indent=2) + "\n")
        return ManagerResult(draft=issue, num_turns=1, cost_usd=0.0, attempts=1), draft_path

    commit = _Recorder(True)

    monkeypatch.setattr(run, "run_research_stage_v2", research)
    monkeypatch.setattr(run, "run_synthesis_stage_v2", synthesis)
    monkeypatch.setattr(
        run, "run_validation_stage_v2",
        _Recorder(ValidationStageResult(draft=issue, retries_used=0, advisory=())),
    )
    monkeypatch.setattr(
        run, "run_critique_stage_v2",
        _Recorder(CritiqueStageResult(draft=issue, status="published", verdict="pass")),
    )
    monkeypatch.setattr(run, "git_commit_run", commit)
    return {"root": repo, "commit": commit, "research": research}


def _run(root, *args, runner=None, today: str = TODAY, publisher=None) -> int:
    return run.main(
        ["--program", PROGRAM, "--today", today, "--root", str(root), *args],
        runner=runner, publisher=publisher,
    )


def _no_publish(*, issue, program_id, root, run_id):
    """A publisher that emits nothing — for the second run of a two-run test.

    The doubled manager returns the same sample issue every time, so a second
    emission would collide with the immutable issue the first one published. That
    guard is publish's subject, not this file's; here the emission is stubbed out
    so the SECOND CYCLE'S dossier behaviour is what the test observes."""
    return None


def _dossier(root: Path) -> dict:
    return json.loads(
        (root / "state" / "entities" / "companies" / f"{COMPANY}.json").read_text()
    )


def _staged(commit) -> list[str]:
    return [str(p) for p in commit.args[0][2]]


# ---------------------------------------------------------------------------
# The end-to-end run
# ---------------------------------------------------------------------------


class TestAFullRunGathersADossier:
    """One cycle, one company, from planning to the commit."""

    def test_the_run_completes_and_writes_the_dossier(self, wired):
        runner = FakeRunner()
        assert _run(wired["root"], runner=runner) == run.EXIT_OK
        record = _dossier(wired["root"])
        assert record["entity_id"] == COMPANY
        assert record["kind"] == "company"
        assert record["facts"]["identity"]["value"]["legal_name"] == "RemeGen Co., Ltd."

    def test_the_scan_was_planned_as_a_first_sighting(self, wired, caplog):
        """A company in the entity layer with no dossier beside it is story 40:
        discovery widened the roster, and the planner deepens it."""
        _run(wired["root"], runner=FakeRunner())
        assert f"dossier scans: 1 planned ({COMPANY} (first_sighting))" in caplog.text

    def test_every_field_carries_the_run_that_established_it(self, wired):
        """Story 13 — any claim is auditable back to its origin."""
        _run(wired["root"], runner=FakeRunner())
        fact = _dossier(wired["root"])["facts"]["identity"]
        assert RUN_ID_RE.fullmatch(fact["established_by"])
        assert fact["issue"] == TODAY

    def test_establishing_a_section_appends_a_drift_entry(self, wired):
        """Corrections append, and so does the first establishment (story 14)."""
        _run(wired["root"], runner=FakeRunner())
        entries = _dossier(wired["root"])["drift_log"]
        # Only the sections the scan actually SPOKE ABOUT are written: an absent
        # section is silence, never a deletion of what an earlier scan established.
        assert {e["field"] for e in entries} == {"identity", "pivots", "setbacks"}
        assert all(e["action"] == "established" for e in entries)
        assert all(RUN_ID_RE.fullmatch(e["run_id"]) for e in entries)

    def test_the_thin_sections_are_marked_from_what_we_now_hold(self, wired):
        """Story 27, and the China-coverage decision: a sparse dossier reads as
        UNMEASURED, at the point of the gap, not as a small company."""
        _run(wired["root"], runner=FakeRunner())
        thin = _dossier(wired["root"])["coverage"]["thin_sections"]
        assert "funding" in thin and "origin" in thin
        assert "identity" not in thin

    def test_the_record_is_stamped_as_of_the_run_date(self, wired):
        """Story 15: freshness is legible without reading the drift log."""
        _run(wired["root"], runner=FakeRunner())
        assert _dossier(wired["root"])["as_of"] == TODAY

    def test_the_dossier_is_staged_into_the_runs_single_commit(self, wired):
        """Story 36: the write goes through the state-edit path, and run.py — the
        sole writer — stages it with the issue rather than in a commit of its own."""
        _run(wired["root"], runner=FakeRunner())
        assert len(wired["commit"].calls) == 1
        assert any(p.endswith(f"companies/{COMPANY}.json") for p in _staged(wired["commit"]))

    def test_the_findings_envelope_is_persisted_under_the_run(self, wired):
        """Same transport as every other aperture — the corpus is the receipt pool."""
        found = list((wired["root"] / "runs").rglob("dossier_scan_*.json"))
        assert not found
        _run(wired["root"], runner=FakeRunner())
        found = list((wired["root"] / "runs").rglob("*.json"))
        assert any(COMPANY in p.name for p in found)

    def test_the_prompt_carries_no_coverage_window(self, wired):
        """The aperture is window-exempt and the PROMPT proves it: interpolating a
        window would re-import the rule the exemption exists to repeal."""
        runner = FakeRunner()
        _run(wired["root"], runner=runner)
        prompt = runner.prompts[0]
        assert COMPANY in prompt
        # Neither window date is interpolated anywhere. Asserted on the DATES
        # rather than the word, because the template does say "coverage_window" —
        # in the sentence forbidding one.
        assert "2026-04-19" not in prompt and TODAY not in prompt.split("as_of")[0]
        assert "(no dossier held — first scan)" in prompt


class TestTheDossierNeverFailsTheRun:
    """Background gathering is subordinate to the cycle's intelligence (#92)."""

    def test_a_failed_scan_degrades_and_the_run_continues(self, wired, caplog):
        runner = FakeRunner(returncode=1)
        assert _run(wired["root"], runner=runner) == run.EXIT_OK
        assert "DOSSIER SCAN FAILED" in caplog.text
        assert not (wired["root"] / "state" / "entities" / "companies").exists()
        assert (wired["root"] / "issues" / PROGRAM / f"{TODAY}.json").exists()

    def test_an_adversarial_payload_degrades_rather_than_crashing(self, wired, caplog):
        """Null where an object belongs, prose where a list belongs, wrong depth —
        the gate must refuse it, and refusing must cost the run nothing."""
        def hostile(run_id):
            return {
                "aperture": APERTURE, "program_id": PROGRAM, "run_id": run_id,
                "coverage_window": {"from": TODAY, "to": TODAY},
                "quiet": "no", "findings": "several", "dossier": ["a", ["b", ["c"]]],
                "coverage_notes": None, "errors": None,
            }

        assert _run(wired["root"], runner=FakeRunner(hostile)) == run.EXIT_OK
        assert "DOSSIER SCAN FAILED" in caplog.text

    def test_a_quiet_scan_is_distinguishable_from_one_that_did_not_run(self, wired, caplog):
        """Story 38. A quiet scan RAN and says so; a scan that did not run leaves
        no envelope at all — and neither writes a record."""
        runner = FakeRunner(lambda run_id: dossier_payload(run_id, quiet=True))
        assert _run(wired["root"], runner=runner) == run.EXIT_OK
        assert "scanned, nothing to record (quiet)" in caplog.text
        assert "DOSSIER SCAN FAILED" not in caplog.text
        assert not (wired["root"] / "state" / "entities" / "companies").exists()

    def test_a_capped_scan_degrades_with_a_receipt_on_the_record(self, wired, caplog):
        """Story 23: exceeding the cap degrades with a receipt rather than
        truncating silently, and the receipt lands where a reader will see it."""
        runner = FakeRunner(
            lambda run_id: dossier_payload(run_id, spend={"searches": 999, "sources": 999})
        )
        assert _run(wired["root"], runner=runner) == run.EXIT_OK
        assert "cost cap exceeded" in caplog.text
        assert "dossier_scan_cost_capped" in _dossier(wired["root"])["coverage"]["degradation"]

    def test_a_missing_dossier_template_costs_only_the_dossier(self, wired, caplog):
        (wired["root"] / "prompts" / "dossier-scan.md").unlink()
        assert _run(wired["root"], runner=FakeRunner()) == run.EXIT_OK
        assert "dossier scans skipped" in caplog.text


class TestTheRefreshDial:
    """It is not scheduled per cycle — that is the whole point of the fourth kind."""

    def _hold(self, root: Path, as_of: str):
        path = root / "state" / "entities" / "companies" / f"{COMPANY}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "entity_id": COMPANY, "kind": "company", "as_of": as_of,
            "facts": {"identity": {"value": {"legal_name": "RemeGen Co., Ltd."},
                                   "established_by": "run_20260101_0700", "issue": as_of}},
            "drift_log": [], "coverage": {"thin_sections": []},
        }, indent=2) + "\n")

    def test_a_fresh_dossier_is_not_rescanned(self, wired, caplog):
        self._hold(wired["root"], TODAY)
        runner = FakeRunner()
        assert _run(wired["root"], runner=runner) == run.EXIT_OK
        assert "dossier scans: none due" in caplog.text
        assert runner.prompts == []

    def test_a_dossier_past_the_dial_is_refreshed(self, wired, caplog):
        stale = date.fromisoformat(TODAY).toordinal() - DOSSIER_REFRESH_DAYS - 1
        self._hold(wired["root"], date.fromordinal(stale).isoformat())
        assert _run(wired["root"], runner=FakeRunner()) == run.EXIT_OK
        assert "(refresh_due)" in caplog.text

    def test_a_refresh_that_finds_the_same_facts_stages_nothing(self, wired):
        """An unchanged refresh is a clean no-op: the drift log is a history, and a
        quarterly rewrite of identical facts would make it unreadable as one."""
        _run(wired["root"], runner=FakeRunner())
        before = _dossier(wired["root"])

        wired["commit"].args.clear()
        wired["commit"].calls.clear()
        # A quarter later: the dial fires, the scan runs again, and it finds
        # exactly what we already hold.
        later = date.fromordinal(
            date.fromisoformat(TODAY).toordinal() + DOSSIER_REFRESH_DAYS + 1
        ).isoformat()
        assert _run(
            wired["root"], runner=FakeRunner(), today=later, publisher=_no_publish
        ) == run.EXIT_OK
        assert _dossier(wired["root"])["version"] == before["version"]
        assert not any(f"companies/{COMPANY}.json" in p for p in _staged(wired["commit"]))


class TestTheKnownGap:
    """The material-event trigger, asserted rather than hidden — as the attendee
    set already is. The seam is live code; only the data source is undecided."""

    def test_the_material_event_set_is_empty(self):
        assert run.MATERIAL_EVENT_IDS_V2 == frozenset()

    def test_a_named_event_would_fire_the_trigger(self):
        from researchswarm.apertures import dossier_trigger

        held = {COMPANY: {"as_of": TODAY}}
        assert dossier_trigger(
            COMPANY, dossiers=held, today=date.fromisoformat(TODAY),
            material_events={COMPANY},
        ) == "material_event"
        assert dossier_trigger(
            COMPANY, dossiers=held, today=date.fromisoformat(TODAY),
            material_events=run.MATERIAL_EVENT_IDS_V2,
        ) is None


class TestTheCycleIsUnaffected:
    def test_a_program_with_no_companies_plans_no_dossier_scans(self, wired, caplog):
        (wired["root"] / "state" / "entities" / f"{COMPANY}.json").unlink()
        assert _run(wired["root"], runner=FakeRunner()) == run.EXIT_OK
        assert "dossier scans: none due" in caplog.text

    def test_dossier_findings_never_reach_the_manager_or_the_critic(self, wired):
        """Out of scope by decision: no read-through is authored from a dossier,
        so the shared record must not enter one program's synthesis."""
        _run(wired["root"], runner=FakeRunner())
        corpus = wired["research"].result.findings_by_aperture
        assert APERTURE not in corpus

    def test_dry_run_renders_the_dossier_prompt_and_writes_nothing(self, wired, caplog):
        before = {p for p in wired["root"].rglob("*")}
        assert _run(wired["root"], "--dry-run") == run.EXIT_OK
        assert f"[dry-run] {APERTURE}: rendered" in caplog.text
        assert {p for p in wired["root"].rglob("*")} == before
