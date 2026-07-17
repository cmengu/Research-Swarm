"""Stage 5 — critique: the Codex gate, the receipt rule, and the retry loop.

A stage module in the idiom of research.py / synthesis.py / validation.py: it owns
the recipe, run.py stays a recipe-follower. The judgment itself lives in
researchswarm.critic (the Codex call, the mechanical receipt rule, the rebuttal
join); this module wires it to the run — loads the five inputs, renders the rubric,
runs the critic, and drives the retry loop that ends in the run.status the issue
publishes under.

The loop mirrors the validator's (validation.py), because the shape is the same:
a gate, a manager who EDITS its own draft on a block, a budget SEPARATE from the
other gate's, and an exhaustion outcome. The differences are what this ticket is
about (spec/06 the retry loop, the rebuttal channel):

  - pass / pass_with_advisories → published.
  - blocked, budget remaining → hand the manager its own prior draft plus exactly
    the surviving blocking_findings (advisories withheld). It EDITS: it fixes the
    finding, or files a sourced `rebuttal`. Researchers are not re-run.
  - the critic re-judges the edited draft. A rebutted finding it re-files is
    `reaffirmed` (both sides now travel with the finding); one it drops is
    `withdrawn` (the dispute is resolved, nothing survives to print).
  - blocked, budget exhausted → published_with_unresolved_findings, the banner is
    run.status itself, and every surviving finding prints WITH its rebuttal.
  - a receipt-rule downgrade that empties the blocking list means nothing blocks,
    so the run publishes clean as pass_with_advisories, consuming no retry.
  - not_run → published_uncritiqued + banner. A missing critic is NOT a failed
    run: the digest is good, unvetted, and says so.

`quiet_this_cycle.critic_catches` is NOT populated here. A catch is a claim the
manager REMOVED after a critic rejection — the manager authors it when it fixes a
finding on retry, and this module's duty is only to PRESERVE it through to publish
(it never touches quiet_this_cycle, and derive_stats already counts them).

Spec: docs/spec/06-validator-and-critic.md (stage 2), docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from researchswarm.critic import (
    BLOCKED,
    NOT_RUN,
    PASS_WITH_ADVISORIES,
    PUBLISHED,
    PUBLISHED_UNCRITIQUED,
    PUBLISHED_WITH_UNRESOLVED,
    annotate_reaffirmed,
    enforce_receipt_rule,
    extract_rebuttals,
    run_critic,
)
from researchswarm.manager import ManagerFailed, run_manager
from researchswarm.prompts import render_critic_prompt, render_critic_retry_prompt
from researchswarm.research import load_findings
from researchswarm.runs import latest_covering_issue
from researchswarm.state import State

log = logging.getLogger("researchswarm.critique")

# Two retries against the critic, SEPARATE from the validator's budget (spec/06):
# they fail for unrelated reasons, and a trivial JSON slip must never starve the
# critic of the budget it needed for substance. Worst case is bounded at four
# manager calls — two here, two in validation.py.
MAX_CRITIC_RETRIES = 2

# Verdict and run-status vocabulary is imported from critic — one home, so this
# stage and the publisher can never disagree on a literal.


@dataclass(frozen=True)
class CritiqueStageResult:
    """What stage 5 hands publish: the status to stamp, the critic's verdict, the
    findings that survived the loop, the retries it spent, and the (possibly
    edited) draft the retries produced.

    `draft` is load-bearing after this ticket: the manager EDITS the draft across
    retries, so the artifact publish writes is the one that came OUT of the loop,
    not the one that went in. `blocking_findings` on an exhausted run carry each
    finding's `rebuttal` (with the critic's `reaffirmed`), so publish prints both
    sides. `reason` is set only on not_run."""

    status: str
    verdict: str
    draft: dict | None = None
    blocking_findings: tuple[dict, ...] = ()
    advisory_findings: tuple[dict, ...] = ()
    retries_used: int = 0
    reason: str | None = None


def run_critique_stage(
    root: Path,
    *,
    draft: dict,
    state: State,
    run_id: str,
    beats_run,
    issues_dir: Path,
    critic_template: str,
    retry_template: str,
    model: str,
    manager_model: str,
    draft_path: Path,
    thesis_version,
    schema_file: Path | None = None,
    timeout: int = 900,
    runner=subprocess.run,
    manager_runner=subprocess.run,
) -> CritiqueStageResult:
    """Run the critic, drive the retry loop, map the outcome to a run.status.

    `draft` is the validated issue from stage 4 (validator_report already stamped).
    The previous issue joins to the most recent COVERING issue, walking past stubs
    — the same continuity primitive the coverage window uses — so a failed run does
    not blind the critic to real history; it is fetched once, since retries do not
    change it. Never raises on a critic failure: a not_run resolves to
    published_uncritiqued, and a retry-manager failure DEGRADES to
    published_with_unresolved rather than crashing the run. A genuine bug in THIS
    wiring is left to propagate (spec: an orchestrator bug should escape loudly
    rather than be laundered).
    """
    findings_by_beat = load_findings(root, run_id, beats_run)
    previous_issue = latest_covering_issue(issues_dir).payload
    findings_corpus = json.dumps(findings_by_beat, ensure_ascii=False)

    # The manager's rebuttals, accumulated across retries, keyed by (kind, where).
    # The critic re-judges the draft that carries them; annotate_reaffirmed pairs a
    # surviving finding back with the rebuttal the critic overruled.
    rebuttals: dict[tuple, dict] = {}
    retries_used = 0

    while True:
        prompt = render_critic_prompt(
            critic_template,
            issue=draft,
            findings_by_beat=findings_by_beat,
            previous_issue=previous_issue,
            watchlist=state.watchlist,
            thesis=state.thesis,
        )
        result = run_critic(
            prompt, model=model, timeout=timeout, schema_file=schema_file, runner=runner
        )

        if result.verdict == NOT_RUN:
            # The critic is unavailable — banner-visible, not a failure. Publish the
            # draft as it stands (whatever earlier retries edited into it).
            return CritiqueStageResult(
                status=PUBLISHED_UNCRITIQUED,
                verdict=NOT_RUN,
                draft=draft,
                reason=result.reason,
                retries_used=retries_used,
            )

        if result.verdict == BLOCKED:
            kept, downgraded = enforce_receipt_rule(
                result.blocking_findings, findings_corpus=findings_corpus, issue=draft
            )
            kept = annotate_reaffirmed(kept, rebuttals)
            advisory = (*result.advisory_findings, *downgraded)
        else:
            kept = ()
            advisory = result.advisory_findings

        if not kept:
            # Nothing blocks — either a clean verdict, or the receipt rule
            # downgraded every blocking finding. Either way the run publishes.
            if result.verdict == BLOCKED:
                log.info("critic blocked, but the receipt rule downgraded every finding")
            verdict = PASS_WITH_ADVISORIES if result.verdict == BLOCKED else result.verdict
            return CritiqueStageResult(
                status=PUBLISHED,
                verdict=verdict,
                draft=draft,
                advisory_findings=advisory,
                retries_used=retries_used,
            )

        if retries_used >= MAX_CRITIC_RETRIES:
            # Budget exhausted, findings still stand: a genuine cross-family dispute
            # the reader should see, not something either side settles silently.
            # Both the finding and (where filed) the rebuttal print under the banner.
            log.warning(
                "critic still blocking after %d retr%s: %d finding(s) publish unresolved",
                retries_used,
                "y" if retries_used == 1 else "ies",
                len(kept),
            )
            return CritiqueStageResult(
                status=PUBLISHED_WITH_UNRESOLVED,
                verdict=BLOCKED,
                draft=draft,
                blocking_findings=kept,
                advisory_findings=advisory,
                retries_used=retries_used,
            )

        # A real block with budget remaining — hand the manager its OWN prior draft
        # plus exactly the surviving findings (advisories withheld). It edits: fix,
        # or file a sourced rebuttal; researchers are not re-run.
        log.warning(
            "critic: %d blocking finding(s), retry %d/%d",
            len(kept),
            retries_used + 1,
            MAX_CRITIC_RETRIES,
        )
        retry_prompt = render_critic_retry_prompt(
            retry_template, prior_draft=draft, blocking_findings=kept
        )
        # Stage 4's record is the validator's, not the manager's to clobber on an
        # edit — carry it across the round so publish still finds it.
        validator_report = (draft.get("critic_report") or {}).get("validator_report")
        try:
            manager_result = run_manager(
                retry_prompt,
                model=manager_model,
                thesis_version=thesis_version,
                run_id=run_id,
                runner=manager_runner,
            )
        except ManagerFailed as exc:
            # The retry manager broke, but the draft under judgment already passed
            # the validator — it is publishable. A broken model DEGRADES the run,
            # never fails it: publish the last good draft with the disputed findings
            # printed, exactly as exhaustion would.
            log.warning(
                "critic retry manager failed (%s) — publishing %d unresolved finding(s)",
                exc,
                len(kept),
            )
            return CritiqueStageResult(
                status=PUBLISHED_WITH_UNRESOLVED,
                verdict=BLOCKED,
                draft=draft,
                blocking_findings=kept,
                advisory_findings=advisory,
                retries_used=retries_used,
            )

        draft = manager_result.draft
        if validator_report is not None:
            draft.setdefault("critic_report", {})["validator_report"] = validator_report
        # Record whatever rebuttals the manager filed this round, then re-persist so
        # the draft on disk always matches the one under judgment (run.py is the
        # sole writer, and the edited draft replaces the one it wrote).
        rebuttals.update(extract_rebuttals(draft))
        draft_path.write_text(json.dumps(draft, indent=2) + "\n")
        retries_used += 1
