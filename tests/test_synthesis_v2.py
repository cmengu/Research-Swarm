"""The v2 synthesis stage (spec/05) — render the per-program manager prompt,
call the manager, write the draft.

Exercised with a FAKE runner (no live model call): the stage renders the real v2
prompt from the real pilot config + fixtures, hands it to a runner that returns a
canned valid-v2 envelope, and writes the draft. The v1 stage and its tests are
untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from researchswarm.apertures import plan_apertures
from researchswarm.programs import load_edges, load_entities, load_interests, load_program
from researchswarm.prompts import RunContext
from researchswarm.synthesis import IssueIdentity, run_synthesis_stage_v2

REPO = Path(__file__).resolve().parents[1]
RUN_ID = "run_20260718_0700"
THESIS_VERSION = 3


def _fixtures() -> dict[str, dict]:
    fdir = REPO / "tests" / "fixtures" / "findings-v2"
    return {p.stem: json.loads(p.read_text()) for p in sorted(fdir.glob("*.json"))}


def _valid_v2_draft() -> dict:
    """The minimal v2 draft the manager would emit — passes the v2 seam
    (`validate_issue_draft` → `_validate_issue_draft_v2`): 15 keys, program block,
    stats == {}, headline.so_what, and the run identifiers echoed."""
    return {
        "schema_version": "2.0.0",
        "issue": {
            "id": "2026-07-18",
            "program_id": "hmbd-001",
            "run": {"run_id": RUN_ID, "thesis_version": THESIS_VERSION, "interest_list_version": 1},
        },
        "program": {"id": "hmbd-001", "moa": "signalling_blockade"},
        "headline": {"title": "t", "summary": "s", "so_what": "why the owner cares today"},
        "stats": {},
        "tldr_bullets": [],
        "catalyst_queue": {"items": []},
        "competitors": [],
        "indications": [],
        "quiet_this_cycle": {"no_news": [], "critic_catches": [], "open_threads": []},
        "newly_discovered": [],
        "house_view": {"partnership_bd": [], "threat_financing": [], "blind_spots": {"cap": 5, "ranked": []}},
        "thesis_updates": [],
        "critic_report": {},
        "sources_and_method": {"apertures_run": [], "apertures_degraded": []},
    }


def _envelope(draft: dict) -> str:
    return json.dumps(
        {"is_error": False, "result": json.dumps(draft), "total_cost_usd": 0.61, "num_turns": 1}
    )


def _runner_returning(stdout: str):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    runner.calls = calls
    return runner


def _identity() -> IssueIdentity:
    ctx = RunContext(run_id=RUN_ID, coverage_window_from="2026-06-18", coverage_window_to="2026-07-18")
    return IssueIdentity(ctx=ctx, issue_id="2026-07-18", published_at="2026-07-18T07:41:00+08:00")


def _thesis() -> dict:
    thesis = json.loads((REPO / "state" / "thesis.json").read_text())
    thesis["version"] = THESIS_VERSION
    return thesis


def _run(tmp_path, runner):
    from researchswarm.prompts import load_template

    program = load_program(REPO / "config", "hmbd-001")
    interests = load_interests(REPO / "config")
    return run_synthesis_stage_v2(
        tmp_path,
        identity=_identity(),
        program=program,
        interests=interests,
        apertures=plan_apertures(program),
        findings_by_aperture=_fixtures(),
        apertures_degraded=["arena_scan:nrg1-fusion-solid-tumors"],
        thesis=_thesis(),
        catalyst_queue=json.loads((REPO / "state" / "programs" / "hmbd-001" / "catalyst-queue.json").read_text()),
        edges=load_edges(REPO / "state", "hmbd-001"),
        entities=load_entities(REPO / "state"),
        prior_quiet={},
        models_config={"manager": "claude-opus-4-8", "critic": "gpt-5-codex"},
        manager_template=load_template(REPO / "prompts" / "manager-v2.md"),
        runner=runner,
    )


class TestV2SynthesisStage:
    def test_it_renders_the_v2_prompt_and_returns_the_managers_draft(self, tmp_path):
        runner = _runner_returning(_envelope(_valid_v2_draft()))
        result, draft_path = _run(tmp_path, runner)
        assert result.draft["schema_version"] == "2.0.0"
        assert result.draft["issue"]["program_id"] == "hmbd-001"
        assert result.cost_usd == 0.61

    def test_the_prompt_handed_to_the_manager_is_the_v2_prompt(self, tmp_path):
        runner = _runner_returning(_envelope(_valid_v2_draft()))
        _run(tmp_path, runner)
        # the prompt is the -p argument of the one manager call
        prompt = runner.calls[0][runner.calls[0].index("-p") + 1]
        assert "HER3" in prompt
        assert "signalling_blockade" in prompt
        assert "read_through" in prompt
        assert "2.0.0" in prompt

    def test_it_writes_the_draft_to_the_run_dir(self, tmp_path):
        runner = _runner_returning(_envelope(_valid_v2_draft()))
        _, draft_path = _run(tmp_path, runner)
        assert draft_path == tmp_path / "runs" / RUN_ID / "issue-draft.json"
        assert draft_path.exists()
        assert json.loads(draft_path.read_text())["schema_version"] == "2.0.0"

    def test_the_models_block_records_all_three_roles(self, tmp_path):
        runner = _runner_returning(_envelope(_valid_v2_draft()))
        _run(tmp_path, runner)
        prompt = runner.calls[0][runner.calls[0].index("-p") + 1]
        # the models block the manager stamps into the issue is rendered into the prompt
        assert "claude-opus-4-8" in prompt
        assert "gpt-5-codex" in prompt
