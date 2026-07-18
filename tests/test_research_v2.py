"""The v2 fan-out (spec/04): one researcher per ACTIVE aperture, failure isolated.

Exercised with a FAKE runner (no live model call): the stage plans the pilot
program's real `1 + N + 1` roster from the committed config, renders the real v2
researcher prompt, and hands each call to a runner returning a canned v2 envelope.

The interesting halves are the two audit shapes and the two ways an aperture can
be absent. A DORMANT arena scan never ran by design and a FAILED one died at the
seam — different facts, same consequence: both appear in `apertures_run` with a
non-ok status AND in `apertures_degraded` as an aperture-id string, because that
is the pair `validator._arena_mechanically_degraded` reads. The v1 stage and its
tests are untouched.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchswarm.apertures import HOUSE_SWEEP, plan_apertures
from researchswarm.findings import (
    HOUSE_SWEEP_KIND,
    FindingsInvalid,
    validate_findings_v2,
)
from researchswarm.programs import (
    load_edges,
    load_interests,
    load_program,
    program_roster,
)
from researchswarm.prompts import RunContext, load_template
from researchswarm.research import (
    ResearcherFailed,
    aperture_slug,
    load_findings_v2,
    run_research_stage_v2,
)
from researchswarm.researcher import (
    ALLOWED_TOOLS,
    DISALLOWED_TOOLS,
    build_command_v2,
    run_researcher_v2,
)

REPO = Path(__file__).resolve().parents[1]
RUN_ID = "run_20260718_0700"
WINDOW = {"from": "2026-07-11", "to": "2026-07-18"}
PROGRAM_ID = "hmbd-001"
MODEL = "claude-sonnet-5"

# The prompt names its own aperture in the output contract, so a fake runner can
# recover which scan a call is for from the argv alone — no side channel.
APERTURE_IN_PROMPT = re.compile(r'"aperture": "([\w:-]+)"')


# ---------------------------------------------------------------------------
# fixtures + fakes
# ---------------------------------------------------------------------------


@pytest.fixture
def program():
    return load_program(REPO / "config", PROGRAM_ID)


@pytest.fixture
def apertures(program):
    return plan_apertures(program)


@pytest.fixture
def ctx():
    return RunContext(
        run_id=RUN_ID,
        coverage_window_from=WINDOW["from"],
        coverage_window_to=WINDOW["to"],
    )


@pytest.fixture
def stage_kwargs(program):
    return {
        "program": program,
        "interests": load_interests(REPO / "config"),
        "edges": load_edges(REPO / "state", PROGRAM_ID),
        "thesis": json.loads((REPO / "state" / "thesis.json").read_text()),
        "known_entity_ids": program_roster(program, load_edges(REPO / "state", PROGRAM_ID)),
        "model": MODEL,
    }


@pytest.fixture
def template():
    return load_template(REPO / "prompts" / "researcher-v2.md")


def valid_findings(aperture_id: str, findings: list | None = None) -> dict:
    findings = findings or []
    return {
        "aperture": aperture_id,
        "program_id": PROGRAM_ID,
        "run_id": RUN_ID,
        "coverage_window": dict(WINDOW),
        "quiet": not findings,
        "findings": findings,
        "coverage_notes": {
            "scope_run": ["HER3 signalling blockade"],
            "entities_checked": ["asset_her3_dxd"],
            "notes": "test coverage",
        },
        "errors": [],
    }


def a_finding(**overrides) -> dict:
    finding = {
        "summary": "A HER3-DXd cohort abstract title posted in window.",
        "entity_ids": ["asset_her3_dxd"],
        "proposed_entity": None,
        "proposed_relation": None,
        "house_lens": None,
        "registry_delta": None,
        "sources": [
            {
                "url": "https://example.org/abstract",
                "publisher": "ESMO",
                "tier": "primary",
                "published_at": "2026-07-17",
                "paywalled": False,
            }
        ],
        "catalyst_refs": [],
        "priority_hint": "high",
        "unconfirmed": False,
    }
    finding.update(overrides)
    return finding


def envelope(payload: dict) -> str:
    return json.dumps(
        {"result": json.dumps(payload), "is_error": False, "total_cost_usd": 0.02, "num_turns": 4}
    )


def aperture_of(command: list[str]) -> str:
    match = APERTURE_IN_PROMPT.search(command[command.index("-p") + 1])
    assert match, "prompt does not name its aperture"
    return match.group(1)


def make_runner(respond, calls=None):
    """A fake subprocess.run. `respond(aperture_id)` returns the payload to emit."""

    def runner(command, capture_output=None, text=None, timeout=None):
        aperture_id = aperture_of(command)
        if calls is not None:
            calls.append(aperture_id)
        return SimpleNamespace(returncode=0, stdout=envelope(respond(aperture_id)), stderr="")

    return runner


# ---------------------------------------------------------------------------
# the v2 findings contract
# ---------------------------------------------------------------------------


class TestTheV2FindingsContract:
    """A correct v2 payload FAILS the v1 validator (no `beat`, no `beat_priority`,
    no `angles_run`). A live run hit exactly that and the model "fixed" it on
    retry by emitting BOTH field sets — a false pass. So the seam dispatches."""

    def _validate(self, payload, **overrides):
        kwargs = {
            "aperture_id": "biology_scan",
            "program_id": PROGRAM_ID,
            "run_id": RUN_ID,
            "window": dict(WINDOW),
            "known_entity_ids": {"asset_her3_dxd"},
        }
        kwargs.update(overrides)
        return validate_findings_v2(payload, **kwargs)

    def test_a_v2_shaped_payload_is_accepted(self):
        self._validate(valid_findings("biology_scan", [a_finding()]))

    def test_a_v1_shaped_payload_is_rejected(self):
        v1_payload = {
            "beat": "biology_scan",
            "run_id": RUN_ID,
            "coverage_window": dict(WINDOW),
            "quiet": True,
            "findings": [],
            "coverage_notes": {
                "angles_run": ["a"], "entities_checked": ["b"], "notes": "c",
            },
            "errors": [],
        }
        with pytest.raises(FindingsInvalid) as exc:
            self._validate(v1_payload)
        assert "aperture" in str(exc.value)
        assert "program_id" in str(exc.value)
        assert "scope_run" in str(exc.value)

    def test_a_leaked_read_through_is_rejected(self):
        """The read-through is the manager's whole job. A researcher emitting one
        has handed interpretation upstream dressed as a fact."""
        payload = valid_findings("biology_scan", [a_finding(read_through="bad for us")])
        with pytest.raises(FindingsInvalid, match="read_through"):
            self._validate(payload)

    @pytest.mark.parametrize(
        "field", ["thesis_bearing", "so_what", "priority", "section"]
    )
    def test_every_manager_only_field_is_rejected(self, field):
        payload = valid_findings("biology_scan", [a_finding(**{field: "x"})])
        with pytest.raises(FindingsInvalid, match=field):
            self._validate(payload)

    def test_priority_hint_replaces_beat_priority(self):
        payload = valid_findings("biology_scan", [a_finding(priority_hint="urgent")])
        with pytest.raises(FindingsInvalid, match="priority_hint"):
            self._validate(payload)

    def test_a_finding_with_no_source_does_not_exist(self):
        payload = valid_findings("biology_scan", [a_finding(sources=[])])
        with pytest.raises(FindingsInvalid, match="sources"):
            self._validate(payload)

    def test_an_off_roster_entity_id_is_rejected(self):
        payload = valid_findings("biology_scan", [a_finding(entity_ids=["not-on-roster"])])
        with pytest.raises(FindingsInvalid, match="not on the roster"):
            self._validate(payload)

    def test_house_lens_outside_the_house_sweep_is_rejected(self):
        payload = valid_findings("biology_scan", [a_finding(house_lens="partnership_bd")])
        with pytest.raises(FindingsInvalid, match="house_sweep-only"):
            self._validate(payload, aperture_kind="biology_scan")

    def test_house_lens_on_the_house_sweep_is_fine(self):
        payload = valid_findings("house_sweep", [a_finding(house_lens="partnership_bd")])
        self._validate(payload, aperture_id="house_sweep", aperture_kind=HOUSE_SWEEP_KIND)

    def test_the_house_sweep_kind_constant_matches_the_planner(self):
        """findings.py spells the kind literally to keep the dependency pointing
        the right way; this is the guard against the two drifting."""
        assert HOUSE_SWEEP_KIND == HOUSE_SWEEP

    def test_findings_answering_for_another_program_are_rejected(self):
        payload = valid_findings("biology_scan")
        payload["program_id"] = "some-other-drug"
        with pytest.raises(FindingsInvalid, match="does not match this program"):
            self._validate(payload)


# ---------------------------------------------------------------------------
# the v2 researcher invocation
# ---------------------------------------------------------------------------


class TestTheV2Wall:
    def test_only_web_tools_are_allowed(self):
        command = build_command_v2("prompt", MODEL)
        allowed = command[command.index("--allowedTools") + 1 : command.index("--disallowedTools")]
        assert allowed == list(ALLOWED_TOOLS)
        assert set(allowed) == {"WebSearch", "WebFetch"}

    @pytest.mark.parametrize("tool", DISALLOWED_TOOLS)
    def test_every_writing_tool_stays_denied(self, tool):
        assert tool in build_command_v2("prompt", MODEL)

    def test_never_skips_permissions(self):
        command = " ".join(build_command_v2("prompt", MODEL))
        assert "dangerously-skip-permissions" not in command
        assert "bypassPermissions" not in command

    def test_the_model_comes_from_the_caller_not_the_aperture(self, apertures):
        """An aperture has no per-scan model override — apertures differ in scope,
        never in rules — so the run's researcher id is passed in."""
        command = build_command_v2("prompt", "sonnet")
        assert command[command.index("--model") + 1] == "sonnet"


class TestTheV2Retry:
    def _run(self, aperture, *stdouts):
        queue = list(stdouts)
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout=queue.pop(0), stderr="")

        result = run_researcher_v2(
            aperture, "prompt", model=MODEL, program_id=PROGRAM_ID, run_id=RUN_ID,
            window=dict(WINDOW), known_entity_ids={"asset_her3_dxd"}, runner=runner,
        )
        return result, calls

    def test_a_good_first_attempt_is_one_call(self, apertures):
        biology = apertures[0]
        result, calls = self._run(biology, envelope(valid_findings("biology_scan")))
        assert result.attempts == 1
        assert result.findings["aperture"] == "biology_scan"
        assert len(calls) == 1

    def test_one_retry_carries_the_validation_error(self, apertures):
        biology = apertures[0]
        result, calls = self._run(
            biology,
            envelope({"aperture": "biology_scan"}),
            envelope(valid_findings("biology_scan")),
        )
        assert result.attempts == 2
        assert "failed validation" in calls[1][calls[1].index("-p") + 1]

    def test_two_failures_fail_the_aperture(self, apertures):
        biology = apertures[0]
        with pytest.raises(ResearcherFailed, match="after 2 attempts"):
            self._run(biology, envelope({"nonsense": True}), envelope({"nonsense": True}))


# ---------------------------------------------------------------------------
# the stage
# ---------------------------------------------------------------------------


class TestV2FanOut:
    def test_it_fans_out_over_active_apertures_only(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        """The pilot roster is biology + 2 arenas + house, one arena dormant — so
        three model calls, not four. The dormancy is the cost saving."""
        calls = []
        stage = run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(valid_findings, calls), **stage_kwargs,
        )
        assert sorted(calls) == [
            "arena_scan:squamous-nsclc", "biology_scan", "house_sweep",
        ]
        assert set(stage.findings_by_aperture) == set(calls)

    def test_it_persists_each_findings_file_under_a_slugified_name(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        """A colon is not a safe filename character, so `arena_scan:squamous-nsclc`
        lands as `arena_scan-squamous-nsclc.json` — matching the committed fixtures."""
        run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(valid_findings), **stage_kwargs,
        )
        findings_dir = tmp_path / "runs" / RUN_ID / "findings"
        assert sorted(p.name for p in findings_dir.glob("*.json")) == [
            "arena_scan-squamous-nsclc.json", "biology_scan.json", "house_sweep.json",
        ]
        arena = json.loads((findings_dir / "arena_scan-squamous-nsclc.json").read_text())
        assert arena["aperture"] == "arena_scan:squamous-nsclc"

    def test_the_slug_is_only_a_filename_never_a_key(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        stage = run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(valid_findings), **stage_kwargs,
        )
        assert "arena_scan:squamous-nsclc" in stage.findings_by_aperture
        assert aperture_slug("arena_scan:squamous-nsclc") == "arena_scan-squamous-nsclc"

    def test_the_corpus_comes_back_in_memory_for_the_synthesis_stage(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        """v2 synthesis takes `findings_by_aperture` rather than reading disk, so
        the stage must return what it wrote — and the two must agree."""
        stage = run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(lambda aid: valid_findings(aid, [a_finding()])),
            **stage_kwargs,
        )
        on_disk = load_findings_v2(tmp_path, RUN_ID, stage.findings_by_aperture)
        assert on_disk == stage.findings_by_aperture

    def test_apertures_run_in_parallel_not_sequentially(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        """Every active researcher must be in flight at once: each fake blocks on
        a barrier sized to the active roster, so a sequential stage deadlocks."""
        barrier = threading.Barrier(3, timeout=15)

        def respond(aperture_id):
            barrier.wait()
            return valid_findings(aperture_id)

        stage = run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(respond), **stage_kwargs,
        )
        assert len(stage.findings_by_aperture) == 3


class TestTheAuditTrail:
    def test_apertures_run_is_kind_plus_scope_plus_status_in_roster_order(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        """The shape the validator's mechanical-degradation exemption matches on:
        `aperture` is the KIND, the indication lives in `scope`."""
        stage = run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(valid_findings), **stage_kwargs,
        )
        assert stage.apertures_run == [
            {"aperture": "biology_scan",
             "scope": "target=HER3 (ERBB3), moa=signalling_blockade", "status": "ok"},
            {"aperture": "arena_scan", "scope": "squamous-nsclc", "status": "ok"},
            {"aperture": "arena_scan", "scope": "nrg1-fusion-solid-tumors", "status": "dormant"},
            {"aperture": "house_sweep",
             "scope": "partnership_bd + threat_financing + blind_spots", "status": "ok"},
        ]

    def test_a_dormant_arena_is_reported_in_both_lists(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        """It spent no budget, but it must still reach the manager — otherwise the
        dormancy cannot render and the reader sees a thin section as truth."""
        stage = run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(valid_findings), **stage_kwargs,
        )
        assert stage.apertures_degraded == ["arena_scan:nrg1-fusion-solid-tumors"]
        assert all(isinstance(a, str) for a in stage.apertures_degraded)
        dormant = [e for e in stage.apertures_run if e["status"] == "dormant"]
        assert dormant == [
            {"aperture": "arena_scan", "scope": "nrg1-fusion-solid-tumors", "status": "dormant"}
        ]

    def test_one_dead_aperture_degrades_the_run_but_does_not_kill_it(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        def respond(aperture_id):
            if aperture_id == "arena_scan:squamous-nsclc":
                return {"aperture": aperture_id, "garbage": True}  # fails twice
            return valid_findings(aperture_id)

        calls = []
        stage = run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(respond, calls), **stage_kwargs,
        )

        assert set(stage.findings_by_aperture) == {"biology_scan", "house_sweep"}
        assert not stage.all_failed
        # dormant and failed both degrade, in roster order
        assert stage.apertures_degraded == [
            "arena_scan:squamous-nsclc", "arena_scan:nrg1-fusion-solid-tumors",
        ]
        statuses = {(e["aperture"], e["scope"]): e["status"] for e in stage.apertures_run}
        assert statuses[("arena_scan", "squamous-nsclc")] == "failed"
        assert statuses[("arena_scan", "nrg1-fusion-solid-tumors")] == "dormant"
        # one retry, no more — and nothing persisted for the dead scan
        assert calls.count("arena_scan:squamous-nsclc") == 2
        assert not (
            tmp_path / "runs" / RUN_ID / "findings" / "arena_scan-squamous-nsclc.json"
        ).exists()

    def test_all_apertures_failing_is_reported_not_raised(
        self, apertures, template, ctx, stage_kwargs, tmp_path
    ):
        """The module reports; the stub decision is run.py's, exactly as in v1."""
        stage = run_research_stage_v2(
            apertures, template, ctx, tmp_path,
            runner=make_runner(lambda aid: {"nonsense": True}), **stage_kwargs,
        )
        assert stage.findings_by_aperture == {}
        assert stage.all_failed
        assert {e["status"] for e in stage.apertures_run} == {"failed", "dormant"}
