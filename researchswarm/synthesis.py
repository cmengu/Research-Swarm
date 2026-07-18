"""Stage 3 — synthesis: the six findings piles become one issue.json draft.

The mirror of stage 2 (researchswarm/research.py): where the research stage
fans out and persists, this stage gathers what was persisted and hands it to
the one component that interprets. It is deliberately thin — it loads the
findings run.py wrote, renders the manager prompt, calls the manager, and writes
the draft. It does NOT decide what a synthesis failure means: it raises
ManagerFailed and lets run.py own the stub, so the published-issue immutability
guard lives in exactly one place.

run.py remains the sole writer throughout: it reads the findings the researchers
could not persist themselves, and writes the issue-draft the manager cannot.

Spec: docs/spec/05-manager.md, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from researchswarm.beats import Beat
from researchswarm.manager import ManagerResult, run_manager
from researchswarm.prompts import RunContext, render_manager_prompt, render_manager_prompt_v2
from researchswarm.research import ResearchStage, load_findings
from researchswarm.state import State

# ⚑ The researcher model for the v2 aperture fan-out, in ONE home. models.toml
# grew a `researchers` key with the pivot (v1 read a per-BEAT model from
# beats.toml, and beats.toml is gone), so this is the fallback when it is unset.
# It lives here, beside the models block that RECORDS it, and run.py imports it
# for the invocation — so the id invoked and the id published are one value by
# construction rather than by two literals agreeing.
RESEARCHER_MODEL_DEFAULT = "claude-sonnet-5"


@dataclass(frozen=True)
class IssueIdentity:
    """Who this issue is: the run it belongs to, its dated id, its stamp time.

    The trio travels together because it is authored together — issue_id is the
    filename and the issue.id, published_at is the stamp, and ctx carries the
    run_id and coverage window the manager echoes into the run block. Passing
    them as one value keeps run_synthesis_stage's signature honest rather than
    growing a fresh positional every time the identity gains a field.
    """

    ctx: RunContext
    issue_id: str
    published_at: str


def run_synthesis_stage(
    root: Path,
    *,
    identity: IssueIdentity,
    state: State,
    beats: list[Beat],
    stage: ResearchStage,
    models_config: dict,
    manager_template: str,
    prior_quiet: dict[str, int],
    runner=subprocess.run,
) -> tuple[ManagerResult, Path]:
    """Load the persisted findings, render, call the manager, write the draft.

    Raises ManagerFailed on synthesis failure; the stub decision belongs to
    run.py. The models block records what argued this issue: researchers by
    their beats.toml default (a per-beat override), the manager by its
    models.toml id (a per-role id), and the critic by its models.toml id (build
    07 wired Codex in — no longer null).
    """
    run_id = identity.ctx.run_id
    findings_by_beat = load_findings(root, run_id, stage.beats_run)
    models = {
        "researchers": beats[0].model if beats else None,
        "manager": models_config["manager"],
        "critic": models_config.get("critic"),
    }
    prompt = render_manager_prompt(
        manager_template,
        identity.ctx,
        state,
        findings_by_beat=findings_by_beat,
        beats_failed=stage.beats_failed,
        prior_quiet=prior_quiet,
        models=models,
        issue_id=identity.issue_id,
        published_at=identity.published_at,
    )
    result = run_manager(
        prompt,
        model=models_config["manager"],
        thesis_version=state.thesis.get("version"),
        run_id=run_id,
        runner=runner,
    )

    draft_path = root / "runs" / run_id / "issue-draft.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(json.dumps(result.draft, indent=2) + "\n")
    return result, draft_path


def run_synthesis_stage_v2(
    root: Path,
    *,
    identity: IssueIdentity,
    program,
    interests,
    apertures,
    findings_by_aperture: dict[str, dict],
    apertures_degraded: list[str],
    thesis: dict,
    catalyst_queue: dict,
    edges,
    entities: dict,
    prior_quiet: dict[str, int],
    models_config: dict,
    manager_template: str,
    runner=subprocess.run,
) -> tuple[ManagerResult, Path]:
    """The v2 synthesis stage — render the per-program manager prompt, call the
    manager, write the draft. The v2 twin of `run_synthesis_stage`, wiring the
    v2 renderer ([05]/[07]) into the same stage machinery.

    Deliberately thin and dispatch-free: run.py's v2 orchestration chooses this
    over the v1 stage, so the two never branch inside one function. Two shape
    differences from v1 flow from the state split ([03]):

    - The inputs are the program CONFIG (`program`, `interests`, `apertures`) plus
      the split v2 STATE (`thesis`, per-program `catalyst_queue`, `edges`,
      `entities`) — not a single flat `State`.
    - Findings arrive **in memory** as `findings_by_aperture`, not loaded from
      disk here. Aperture ids carry a colon (`arena_scan:<indication>`), an unsafe
      filename character, so the on-disk naming is the research stage's call to
      make; this stage stays agnostic to it and takes the corpus the caller holds.

    `run_manager` is shared unchanged — it validates the draft at the seam via
    `validate_issue_draft`, which dispatches on the draft's own schema_version, so
    a v2 draft is held to the v2 seam contract with no change here. The models
    block records what argued the issue; the researchers' id defaults to the v2
    researcher model when models.toml does not pin one (it is a per-role id only
    for the two single-agent roles).
    """
    run_id = identity.ctx.run_id
    models = {
        "researchers": models_config.get("researchers", RESEARCHER_MODEL_DEFAULT),
        "manager": models_config["manager"],
        "critic": models_config.get("critic"),
    }
    prompt = render_manager_prompt_v2(
        manager_template,
        program=program,
        interests=interests,
        apertures=apertures,
        findings_by_aperture=findings_by_aperture,
        apertures_degraded=apertures_degraded,
        thesis=thesis,
        catalyst_queue=catalyst_queue,
        edges=edges,
        entities=entities,
        prior_quiet=prior_quiet,
        run_id=run_id,
        issue_id=identity.issue_id,
        published_at=identity.published_at,
        coverage_window_from=identity.ctx.coverage_window_from,
        coverage_window_to=identity.ctx.coverage_window_to,
        thesis_version=thesis.get("version"),
        interest_list_version=interests.version,
        models=models,
    )
    result = run_manager(
        prompt,
        model=models_config["manager"],
        thesis_version=thesis.get("version"),
        run_id=run_id,
        runner=runner,
    )

    draft_path = root / "runs" / run_id / "issue-draft.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(json.dumps(result.draft, indent=2) + "\n")
    return result, draft_path
