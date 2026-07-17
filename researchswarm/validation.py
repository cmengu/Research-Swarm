"""Stage 4 — validation: the deterministic gate and its retry loop.

A stage module in the idiom of research.py (stage 2) and synthesis.py (stage 3):
it owns the loop, run.py stays a recipe-follower. The gate itself lives in
researchswarm.validator — free, deterministic, no model — and this module wires
it to the manager's retry seam.

The shape of the loop, from spec/06:

  - validate the draft;
  - if it passes, stamp critic_report.validator_report and re-persist;
  - if it blocks and a retry remains, hand the manager EXACTLY its own prior
    draft plus the blocking findings — it EDITS that draft (spec/05: sections
    that passed must not mutate, no new facts, no re-running researchers);
  - two retries maximum, a budget SEPARATE from the critic's (they fail for
    unrelated reasons, and a trivial structural slip must never starve the
    critic of the budget it needed for substance);
  - on exhaustion, signal the caller, which writes a stub with
    failure.stage "validation".

The continuity walk that finds the queue-tamper baseline lives here, not in the
validator: the validator stays a pure function of the draft plus the facts
handed to it, and IO belongs to the stage.

Spec: docs/spec/06-validator-and-critic.md, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from researchswarm.manager import run_manager
from researchswarm.prompts import render_manager_retry_prompt
from researchswarm.runs import find_latest_issue_with
from researchswarm.validator import Finding, validate_issue

log = logging.getLogger("researchswarm.validation")

# Separate from the critic's budget on purpose: two structural retries here,
# two judgment retries there, worst case bounded at four manager calls. A JSON
# slip must never eat the budget the critic needed for substance.
MAX_VALIDATION_RETRIES = 2


class ValidationExhausted(RuntimeError):
    """Still structurally invalid after the retry budget. There IS a draft, but
    it cannot be published — run.py turns this into a stub with failure.stage
    "validation", not a degradation: a degradation explains an absence inside a
    VALID issue, and this issue is invalid."""

    def __init__(self, message: str, blocking: tuple[Finding, ...] = ()):
        super().__init__(message)
        self.blocking = blocking


@dataclass(frozen=True)
class ValidationStageResult:
    """What stage 4 hands back on a pass: the draft with its validator_report
    stamped, how many retries the gate spent (0 on a clean first pass), and the
    advisory findings that still stand (rendered in the report, never blocking).
    """

    draft: dict
    retries_used: int
    advisory: tuple[Finding, ...]


def _validator_report(passed: bool, retries_used: int, findings) -> dict:
    """critic_report.validator_report — stage 1's record, orchestrator-owned.

    findings carries the ADVISORIES that still stand plus a record of what
    earlier retry rounds caught: the published report should describe both what
    the gate still flags (advisories publish visibly) and what the retries
    fixed, so a reader auditing the run can see the gate did its job rather than
    a suspiciously clean pass. Each is the {kind, where, note} shape spec/07 puts
    in validator_report.findings.
    """
    return {
        "passed": passed,
        "retries_used": retries_used,
        "findings": [
            {"kind": f.kind, "where": f.where, "note": f.note} for f in findings
        ],
    }


def run_validation_stage(
    *,
    draft: dict,
    draft_path: Path,
    state,
    issues_dir: Path,
    beats_failed: list[str],
    retry_template: str,
    model: str,
    run_id: str,
    thesis_version,
    calendar_stale: bool = False,
    runner=subprocess.run,
) -> ValidationStageResult:
    """Validate the draft, retry the manager on a block, stamp and re-persist.

    Raises ValidationExhausted when the budget runs out still blocking; run.py
    owns the stub. A ManagerFailed during a retry propagates unchanged — run.py
    maps both to the validation stub, because either way there is a draft that
    could not be made publishable.

    `calendar_stale` is the Stage-1 fact; passed straight to validate_issue so a
    stale calendar files its advisory on this run's issue (the marker rides on
    every issue, critic present or not).
    """
    # The queue-tamper baseline: the most recent issue CARRYING a catalyst_queue
    # snapshot, walked back past stubs. None on run #1 (tolerated — nothing to
    # compare); `expired` when the 12-issue floor was hit (an advisory, never a
    # block).
    baseline = find_latest_issue_with(
        issues_dir, lambda payload: bool(payload.get("catalyst_queue"))
    )
    queue_baseline = (baseline.payload or {}).get("catalyst_queue")

    earlier_rounds: list[Finding] = []
    retries_used = 0

    while True:
        result = validate_issue(
            draft,
            state=state,
            queue_baseline=queue_baseline,
            baseline_expired=baseline.expired,
            beats_failed=beats_failed,
            calendar_stale=calendar_stale,
        )

        if result.passed:
            return _finish(
                draft, draft_path, retries_used, result.advisory, earlier_rounds
            )

        if retries_used >= MAX_VALIDATION_RETRIES:
            names = ", ".join(f"{f.kind}@{f.where}" for f in result.blocking)
            raise ValidationExhausted(
                f"validation still blocking after {retries_used} retr"
                f"{'y' if retries_used == 1 else 'ies'}: {names}",
                blocking=result.blocking,
            )

        # Record what this round caught, then hand the manager its own draft and
        # exactly the blocking findings — advisories are withheld from the retry.
        earlier_rounds.extend(result.blocking)
        log.warning(
            "validation: %d blocking finding(s), retry %d/%d",
            len(result.blocking),
            retries_used + 1,
            MAX_VALIDATION_RETRIES,
        )
        prompt = render_manager_retry_prompt(
            retry_template, prior_draft=draft, blocking_findings=result.blocking
        )
        manager_result = run_manager(
            prompt,
            model=model,
            thesis_version=thesis_version,
            run_id=run_id,
            runner=runner,
        )
        draft = manager_result.draft
        # run.py is the sole writer: the edited draft replaces the one on disk so
        # the artifact on disk always matches the draft under judgment.
        draft_path.write_text(json.dumps(draft, indent=2) + "\n")
        retries_used += 1


def _finish(draft, draft_path, retries_used, advisory, earlier_rounds):
    """Stamp validator_report onto the passing draft and re-persist it."""
    recorded = list(advisory) + earlier_rounds
    draft.setdefault("critic_report", {})["validator_report"] = _validator_report(
        passed=True, retries_used=retries_used, findings=recorded
    )
    draft_path.write_text(json.dumps(draft, indent=2) + "\n")
    return ValidationStageResult(
        draft=draft, retries_used=retries_used, advisory=advisory
    )
