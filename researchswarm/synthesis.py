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
from researchswarm.prompts import RunContext, render_manager_prompt
from researchswarm.research import ResearchStage
from researchswarm.state import State


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
    models.toml id (a per-role id), and the critic null until build 07.
    """
    run_id = identity.ctx.run_id
    findings_by_beat = {
        beat_id: json.loads(
            (root / "runs" / run_id / "findings" / f"{beat_id}.json").read_text()
        )
        for beat_id in stage.beats_run
    }
    models = {
        "researchers": beats[0].model if beats else None,
        "manager": models_config["manager"],
        "critic": None,
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
