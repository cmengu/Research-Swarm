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
from researchswarm.state import State
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


def state_view_v2(known_entity_ids, thesis: dict, catalyst_queue: dict) -> State:
    """Adapt the split v2 state to the `State` shape the shared checks still read.

    The v2 check-suite reuses three v1 checks unchanged — `_check_dangling_entity_v2`
    (needs the known-entity set), `_check_empty_section_v2` (needs it too, via the
    quiet-cycle trigger) and `_check_queue_tamper` — and all three reach for a
    `State`. Rather than re-open the validator's signature (v1 is deleted last, as
    its own ticket), the orchestrator hands it a VIEW: a `State` whose watchlist is
    synthesised from the v2 known-entity set, and whose thesis and queue are the v2
    files themselves.

    `known_entity_ids` is deliberately the WIDE set — every `state/entities/` record
    plus this program's roster — not the roster. The roster is the narrow
    accountability set and travels separately as `roster=`; the wide set is only
    "does this entity_id resolve to anything at all", which is exactly what the
    dangling-reference check asks (spec/03 the entity_id spine).

    This is an adapter, not a state layer: nothing writes through it, and it dies
    with v1.
    """
    return State(
        watchlist={"entities": [{"entity_id": e} for e in sorted(known_entity_ids)]},
        thesis=thesis,
        catalyst_queue=catalyst_queue,
    )


def run_validation_stage_v2(
    *,
    draft: dict,
    draft_path: Path,
    state: State,
    roster,
    issues_dir: Path,
    retry_template: str,
    model: str,
    run_id: str,
    thesis_version,
    calendar_stale: bool = False,
    derive_stats=None,
    runner=subprocess.run,
) -> ValidationStageResult:
    """The v2 stage 4 — the same loop, against the v2 check-suite.

    A twin of `run_validation_stage`, additive beside it in the same idiom the
    pivot used for research/synthesis/critique: run.py's v2 orchestration chooses
    this one, so the two never branch inside a single function and v1 can be
    deleted whole.

    **The loop is deliberately identical** — same two-retry budget separate from
    the critic's, same "hand the manager EXACTLY its own draft plus the blocking
    findings", same `ValidationExhausted` on exhaustion with the stub decision left
    to run.py, same `validator_report` stamp and re-persist. Spec/07 says the v2
    schema does not re-open the machinery; re-deriving the retry contract here
    would give it a second home to drift in.

    Only the INPUTS differ:

    - `state` is the `state_view_v2` adapter over the split v2 state, not a flat
      v1 `State` loaded from `state/`;
    - `roster` is the program roster (`programs.program_roster`) — the narrow
      accountability set the v2 coverage check (`_check_unaccounted_entity`) holds
      every typed competitor against. v1 has no such parameter, which is precisely
      why this twin exists rather than a keyword bolted onto v1;
    - `issues_dir` is THIS program's directory (`issues/<program_id>/`), since v2
      stores issues per program, so the queue-tamper baseline joins to the last
      covering issue OF THIS PROGRAM.
    - `beats_failed` is absent: v2's degradation audit is `apertures_degraded`, and
      the v2 check-suite reads it off the issue's own `sources_and_method` rather
      than taking it from the caller.
    - `derive_stats` is the orchestrator's stats derivation, applied to EVERY draft
      this loop judges. See the call site below for why it cannot live outside.
    """
    baseline = find_latest_issue_with(
        issues_dir, lambda payload: bool(payload.get("catalyst_queue"))
    )
    queue_baseline = (baseline.payload or {}).get("catalyst_queue")

    earlier_rounds: list[Finding] = []
    retries_used = 0

    while True:
        # DERIVE INSIDE THE LOOP, not before it. The manager seam requires
        # `stats == {}` and the validator requires stats populated; the
        # orchestrator closes that gap by deriving. But a RETRY calls the manager
        # again, and the manager obeys its seam again — so every retry draft
        # arrives with `stats` reset to {} and re-fails the shape check it was
        # never asked to fix. Deriving once before the loop fixes only round 1,
        # which is why runs kept exhausting the budget on `malformed_shape@stats`
        # while the manager dutifully fixed everything it was actually told about.
        #
        # The derivation is injected rather than imported: it needs `issues_dir`
        # for `previous_issue` and belongs to the publish altitude, and the stage
        # stays a loop over a gate rather than growing a second opinion about
        # what the counts are.
        if derive_stats is not None:
            draft["stats"] = derive_stats(draft)

        result = validate_issue(
            draft,
            state=state,
            queue_baseline=queue_baseline,
            baseline_expired=baseline.expired,
            calendar_stale=calendar_stale,
            roster=roster,
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
