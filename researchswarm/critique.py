"""Stage 5 — critique: one Codex pass, the receipt rule, and the run status.

A stage module in the idiom of research.py / synthesis.py / validation.py: it owns
the recipe, run.py stays a recipe-follower. The judgment itself lives in
researchswarm.critic (the Codex call, the mechanical receipt rule); this module
wires it to the run — loads the five inputs, renders the rubric, runs one critic
pass, enforces the receipt rule, and maps the verdict to the run.status the issue
publishes under.

The retry loop is ticket #35, NOT here. This ticket wires ONE critic pass:

  - pass / pass_with_advisories  → published.
  - blocked (after the receipt rule) → published_with_unresolved_findings, the
    honest INTERIM until #35 lands the loop. With no retry yet, the blocking
    findings publish in the banner rather than being resolved — the dispute is
    shown, and the manager's rebuttals arrive with the loop. If the receipt rule
    downgrades every blocking finding, nothing survives to block, so the run
    publishes clean as pass_with_advisories.
  - not_run → published_uncritiqued + banner. A missing critic is NOT a failed
    run: the digest is good, unvetted, and says so.

`quiet_this_cycle.critic_catches` is NOT populated here. A catch is a claim the
manager REMOVED after a critic rejection, and with no retry loop nothing is
removed yet — that population belongs to #35. This module's duty to it is only to
PRESERVE any manager-authored catches through to publish (which it does: it never
touches quiet_this_cycle, and derive_stats already counts them).

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
    CriticResult,
    enforce_receipt_rule,
    run_critic,
)
from researchswarm.prompts import render_critic_prompt
from researchswarm.research import load_findings
from researchswarm.runs import latest_covering_issue
from researchswarm.state import State

log = logging.getLogger("researchswarm.critique")

# Verdict and run-status vocabulary is imported from critic — one home, so this
# stage and the publisher can never disagree on a literal.


@dataclass(frozen=True)
class CritiqueStageResult:
    """What stage 5 hands publish: the status to stamp, the critic's verdict, the
    findings that survived the receipt rule, and (on not_run) the reason.

    `retries_used` is always 0 this ticket — the retry loop is #35. It rides here
    now so the field publish stamps into critic_report has a home that will not
    move when the loop lands."""

    status: str
    verdict: str
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
    model: str,
    schema_file: Path | None = None,
    timeout: int = 900,
    runner=subprocess.run,
) -> CritiqueStageResult:
    """Render the rubric, run one critic pass, enforce receipts, map to a status.

    `draft` is the validated issue from stage 4 (validator_report already stamped).
    The previous issue joins to the most recent COVERING issue, walking past stubs
    — the same continuity primitive the coverage window uses — so a failed run does
    not blind the critic to real history. Never raises on a critic failure: a
    not_run resolves to published_uncritiqued. A genuine bug in THIS wiring is left
    to propagate (spec: a critic being unreachable is not a failure, but an
    orchestrator bug should escape loudly rather than be laundered into a stub).
    """
    findings_by_beat = load_findings(root, run_id, beats_run)
    previous_issue = latest_covering_issue(issues_dir).payload

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

    findings_corpus = json.dumps(findings_by_beat, ensure_ascii=False)
    return _resolve(result, findings_corpus=findings_corpus, issue=draft)


def _resolve(
    result: CriticResult, *, findings_corpus: str, issue: dict
) -> CritiqueStageResult:
    """Map a CriticResult to the run status, applying the receipt rule on a block.

    The one place the verdict becomes an outcome. On `blocked`, the receipt rule
    runs first: a dropped_story without a well-formed receipt is downgraded to
    advisory (consuming no retry), and if that empties the blocking list the block
    evaporates and the run publishes clean. What survives publishes under
    published_with_unresolved_findings — the honest interim until #35's loop.
    """
    if result.verdict == NOT_RUN:
        return CritiqueStageResult(
            status=PUBLISHED_UNCRITIQUED,
            verdict=NOT_RUN,
            reason=result.reason,
        )

    if result.verdict == BLOCKED:
        kept, downgraded = enforce_receipt_rule(
            result.blocking_findings, findings_corpus=findings_corpus, issue=issue
        )
        advisory = (*result.advisory_findings, *downgraded)
        if kept:
            log.warning(
                "critic blocked: %d finding(s) survived the receipt rule", len(kept)
            )
            return CritiqueStageResult(
                status=PUBLISHED_WITH_UNRESOLVED,
                verdict=BLOCKED,
                blocking_findings=kept,
                advisory_findings=advisory,
            )
        # Every blocking finding was downgraded — nothing blocks, so the run is
        # not blocked. Publish clean, recording the honest reduced verdict.
        log.info("critic blocked, but the receipt rule downgraded every finding")
        return CritiqueStageResult(
            status=PUBLISHED,
            verdict=PASS_WITH_ADVISORIES,
            advisory_findings=advisory,
        )

    # pass or pass_with_advisories — both publish.
    return CritiqueStageResult(
        status=PUBLISHED,
        verdict=result.verdict,
        advisory_findings=result.advisory_findings,
    )
