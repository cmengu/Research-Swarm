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
from typing import TYPE_CHECKING

from researchswarm.critic import (
    BLOCKED,
    NOT_RUN,
    PASS_WITH_ADVISORIES,
    PUBLISHED,
    PUBLISHED_UNCRITIQUED,
    PUBLISHED_WITH_UNRESOLVED,
    REAFFIRMED,
    WITHDRAWN,
    attach_adjudication,
    enforce_receipt_rule,
    extract_rebuttals,
    finding_key,
    match_survivor_key,
    rebuttal_record,
    run_critic,
    run_critic_v2,
)
from researchswarm.manager import ManagerFailed, run_manager
from researchswarm.prompts import (
    render_critic_prompt,
    render_critic_prompt_v2,
    render_critic_retry_prompt,
)
from researchswarm.research import load_findings
from researchswarm.runs import latest_covering_issue
from researchswarm.state import State

if TYPE_CHECKING:
    from researchswarm.calendar import SurgeState

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
    """What stage 5 hands publish: the (possibly edited) draft the retries
    produced, the status to stamp, the critic's verdict, the findings that
    survived the loop, and the retries it spent.

    `draft` is REQUIRED, and load-bearing after this ticket: the manager EDITS the
    draft across retries, so the artifact publish writes is the one that came OUT
    of the loop, not the one that went in — the stage always produces one, so
    there is no None case to guard. `blocking_findings` on an exhausted run carry
    each finding's `rebuttal` (with the critic's `reaffirmed`); `advisory_findings`
    carry any `withdrawn` rebuttal records — both sides always printed. `reason` is
    set only on not_run."""

    draft: dict
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
    retry_template: str,
    model: str,
    manager_model: str,
    draft_path: Path,
    thesis_version,
    schema_file: Path | None = None,
    surge: "SurgeState | None" = None,
    timeout: int = 900,
    runner=subprocess.run,
    manager_runner=subprocess.run,
) -> CritiqueStageResult:
    """Run the critic, drive the retry loop, map the outcome to a run.status.

    `draft` is the validated issue from stage 4 (validator_report already stamped).
    `surge` is the resolved SurgeState or None: it supplies the critic the
    conference window that provenance_stale compares against during a surge (the
    one reference-window change, spec/02) — the bar is otherwise unchanged.
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

    # Rebuttal bookkeeping across the loop, all keyed by (kind, where):
    #   pending    — filed on the retry just completed, awaiting THIS pass's verdict.
    #   reaffirmed — the critic re-filed the fault; keyed by the SURVIVOR it rides on.
    #   withdrawn  — the critic dropped the fault; terminal, non-gating records.
    # A rebuttal is adjudicated exactly ONCE, on the pass right after it is filed —
    # before the manager has had a chance to comply — so "the critic withdrew it"
    # is never confused with "the manager later fixed it".
    pending: dict[tuple, dict] = {}
    reaffirmed: dict[tuple, dict] = {}
    withdrawn_records: list[dict] = []
    retries_used = 0

    while True:
        prompt = render_critic_prompt(
            critic_template,
            issue=draft,
            findings_by_beat=findings_by_beat,
            previous_issue=previous_issue,
            watchlist=state.watchlist,
            thesis=state.thesis,
            surge=surge,
        )
        result = run_critic(
            prompt, model=model, timeout=timeout, schema_file=schema_file, runner=runner
        )

        if result.verdict == NOT_RUN:
            # The critic is unavailable — banner-visible, not a failure. Publish the
            # draft as it stands (whatever earlier retries edited into it).
            return CritiqueStageResult(
                draft=draft,
                status=PUBLISHED_UNCRITIQUED,
                verdict=NOT_RUN,
                reason=result.reason,
                retries_used=retries_used,
            )

        if result.verdict == BLOCKED:
            kept, downgraded = enforce_receipt_rule(
                result.blocking_findings, findings_corpus=findings_corpus, issue=draft
            )
            advisory = [*result.advisory_findings, *downgraded]
        else:
            kept = ()
            advisory = list(result.advisory_findings)

        # Adjudicate the rebuttals filed on the previous retry against THIS pass:
        # re-filed (exact, or the sole same-kind survivor) → reaffirmed; else the
        # critic dropped it → a withdrawn record. Then pair every upheld rebuttal
        # with the finding it now rides on.
        for key, reb in pending.items():
            survivor = match_survivor_key(key, kept)
            if survivor is not None:
                reaffirmed[survivor] = reb
            else:
                withdrawn_records.append(rebuttal_record(key, reb, WITHDRAWN))
        pending = {}
        kept = tuple(
            attach_adjudication(f, reaffirmed[finding_key(f)], REAFFIRMED)
            if finding_key(f) in reaffirmed
            else f
            for f in kept
        )

        # An upheld rebuttal whose finding no longer survives (the manager complied,
        # or the critic merged it away) still publishes as its own record — a filed
        # rebuttal is never silently deleted.
        survivor_keys = {finding_key(f) for f in kept}
        orphan_reaffirmed = [
            rebuttal_record(key, reb, REAFFIRMED)
            for key, reb in reaffirmed.items()
            if key not in survivor_keys
        ]
        terminal_advisory = (*advisory, *withdrawn_records, *orphan_reaffirmed)

        if not kept:
            # Nothing blocks — a clean verdict, every blocking finding downgraded by
            # the receipt rule, or every dispute withdrawn. Either way the run
            # publishes, the withdrawn records riding among the advisories.
            if result.verdict == BLOCKED:
                log.info("critic blocked, but the receipt rule downgraded every finding")
            verdict = PASS_WITH_ADVISORIES if result.verdict == BLOCKED else result.verdict
            return CritiqueStageResult(
                draft=draft,
                status=PUBLISHED,
                verdict=verdict,
                advisory_findings=terminal_advisory,
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
                draft=draft,
                status=PUBLISHED_WITH_UNRESOLVED,
                verdict=BLOCKED,
                blocking_findings=kept,
                advisory_findings=terminal_advisory,
                retries_used=retries_used,
            )

        # A real block with budget remaining — hand the manager its OWN prior draft
        # plus exactly the surviving findings (advisories withheld). It edits: fix,
        # or (except on the final round) file a sourced rebuttal; researchers are
        # not re-run. `final_round` closes the rebuttal channel: retry 2 is
        # comply-only, so a reaffirmed finding cannot be rebutted a second time.
        final_round = retries_used + 1 >= MAX_CRITIC_RETRIES
        log.warning(
            "critic: %d blocking finding(s), retry %d/%d%s",
            len(kept),
            retries_used + 1,
            MAX_CRITIC_RETRIES,
            " (comply-only)" if final_round else "",
        )
        retry_prompt = render_critic_retry_prompt(
            retry_template, prior_draft=draft, blocking_findings=kept, final_round=final_round
        )
        # Stage 4's record is the validator's, not the manager's to clobber on an
        # edit — carry it across the round so publish still finds it.
        validator_report = (draft.get("critic_report") or {}).get("validator_report")
        prior_draft = draft
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
            # printed. Stamp the cause on each survivor so a reader can tell "manager
            # crashed at retry N" from "survived a full retry 2".
            log.warning(
                "critic retry manager failed (%s) — publishing %d unresolved finding(s)",
                exc,
                len(kept),
            )
            trace = f"manager unavailable at retry {retries_used + 1}: {exc}"
            degraded = tuple(_note(f, trace) for f in kept)
            return CritiqueStageResult(
                draft=draft,
                status=PUBLISHED_WITH_UNRESOLVED,
                verdict=BLOCKED,
                blocking_findings=degraded,
                advisory_findings=terminal_advisory,
                retries_used=retries_used,
            )

        draft = manager_result.draft
        if validator_report is not None:
            draft.setdefault("critic_report", {})["validator_report"] = validator_report
        # Only a non-final round may file rebuttals; on the final round the channel
        # is closed and any rebuttal the manager typed is ignored (never extracted,
        # and publish overwrites critic_report.blocking_findings regardless).
        if not final_round:
            pending = extract_rebuttals(draft)
        _log_edited_sections(prior_draft, draft, retries_used + 1)
        # Re-persist so the draft on disk always matches the one under judgment
        # (run.py is the sole writer; the edited draft replaces the one it wrote).
        draft_path.write_text(json.dumps(draft, indent=2) + "\n")
        retries_used += 1


def run_critique_stage_v2(
    root: Path,
    *,
    draft: dict,
    program,
    edges,
    entities: dict,
    thesis: dict,
    findings_by_aperture: dict[str, dict],
    run_id: str,
    issues_dir: Path,
    critic_template: str,
    retry_template: str,
    model: str,
    manager_model: str,
    draft_path: Path,
    thesis_version,
    schema_file: Path | None = None,
    surge: "SurgeState | None" = None,
    timeout: int = 900,
    runner=subprocess.run,
    manager_runner=subprocess.run,
) -> CritiqueStageResult:
    """The v2 critique stage — the per-program detective's gate (spec/06, spec/07).

    The v2 twin of `run_critique_stage`, additive beside it and dispatch-free in the
    same idiom as `run_synthesis_stage_v2`: run.py's v2 orchestration chooses this,
    so the two never branch inside one function.

    **The machinery is deliberately identical**, because spec/07 says the v2 schema
    "does not re-open the machinery" — same two-retry budget, same receipt rule,
    same rebuttal join, same verdict→run.status map, same degrade-don't-crash
    behaviour when the retry manager dies. Re-deriving any of that here would be a
    second place for the retry contract to drift. What differs is only the INPUTS
    it assembles for the rubric:

      - the v2 rubric (`prompts/critic-v2.md`) via `render_critic_prompt_v2`, whose
        centrepiece is the `weak_read_through` ADVISORY — the quality half of the
        admission rule, whose presence half the free validator already blocked.
      - `run_critic_v2`, which sorts against the v2 blocking set: `relation_miscast`
        joined it, and a v2 pass sorted against v1's set would silently demote a
        miscast competitor into an advisory and publish it clean.
      - the split v2 state (`program`, `edges`, `entities`, `thesis`) in place of a
        single flat `State`.
      - findings arriving IN MEMORY as `findings_by_aperture`, not loaded from disk
        here — aperture ids carry a colon (`arena_scan:<indication>`), an unsafe
        filename character, so on-disk naming is the research stage's call and this
        stage stays agnostic to it (exactly as `run_synthesis_stage_v2` does).
        The corpus is still evidence, not context: it is what the `dropped_story`
        receipt rule is enforced against (spec/04 "this corpus is evidence"), and it
        is serialised here into the same `findings_corpus` string the receipt rule
        greps, so what the critic READ and what the orchestrator CHECKS are one
        value.

    `issues_dir` is this PROGRAM's issue directory (`issues/<program_id>/`), since
    v2 stores issues per program; the previous issue joins to the most recent
    COVERING one, walking past stubs, and is fetched once because retries do not
    change it.

    The retry prompt reuses `prompts/critic-retry.md` unchanged: it hands the
    manager exactly its own prior draft plus the surviving blocking findings, and
    neither of those is schema-shaped. The manager's SEAM validation dispatches on
    the draft's own schema_version, so a v2 redraft is already held to the v2
    contract with no change here.
    """
    previous_issue = latest_covering_issue(issues_dir).payload
    findings_corpus = json.dumps(findings_by_aperture, ensure_ascii=False)

    # Rebuttal bookkeeping across the loop, keyed by (kind, where) — identical to
    # v1's, and identical on purpose: the rebuttal channel is machinery the pivot
    # did not re-open. A rebuttal is adjudicated exactly ONCE, on the pass right
    # after it is filed, so "the critic withdrew it" is never confused with "the
    # manager later complied".
    pending: dict[tuple, dict] = {}
    reaffirmed: dict[tuple, dict] = {}
    withdrawn_records: list[dict] = []
    retries_used = 0

    while True:
        prompt = render_critic_prompt_v2(
            critic_template,
            issue=draft,
            findings_by_aperture=findings_by_aperture,
            previous_issue=previous_issue,
            program=program,
            edges=edges,
            entities=entities,
            thesis=thesis,
            surge=surge,
        )
        result = run_critic_v2(
            prompt, model=model, timeout=timeout, schema_file=schema_file, runner=runner
        )

        if result.verdict == NOT_RUN:
            # The critic is unavailable — banner-visible, not a failure.
            return CritiqueStageResult(
                draft=draft,
                status=PUBLISHED_UNCRITIQUED,
                verdict=NOT_RUN,
                reason=result.reason,
                retries_used=retries_used,
            )

        if result.verdict == BLOCKED:
            kept, downgraded = enforce_receipt_rule(
                result.blocking_findings, findings_corpus=findings_corpus, issue=draft
            )
            advisory = [*result.advisory_findings, *downgraded]
        else:
            kept = ()
            advisory = list(result.advisory_findings)

        for key, reb in pending.items():
            survivor = match_survivor_key(key, kept)
            if survivor is not None:
                reaffirmed[survivor] = reb
            else:
                withdrawn_records.append(rebuttal_record(key, reb, WITHDRAWN))
        pending = {}
        kept = tuple(
            attach_adjudication(f, reaffirmed[finding_key(f)], REAFFIRMED)
            if finding_key(f) in reaffirmed
            else f
            for f in kept
        )

        survivor_keys = {finding_key(f) for f in kept}
        orphan_reaffirmed = [
            rebuttal_record(key, reb, REAFFIRMED)
            for key, reb in reaffirmed.items()
            if key not in survivor_keys
        ]
        terminal_advisory = (*advisory, *withdrawn_records, *orphan_reaffirmed)

        if not kept:
            # Nothing blocks: a clean verdict, every finding receipt-downgraded, or
            # every dispute withdrawn. Advisories — weak_read_through above all —
            # ride out with the issue and never gate it.
            if result.verdict == BLOCKED:
                log.info("critic blocked, but the receipt rule downgraded every finding")
            verdict = PASS_WITH_ADVISORIES if result.verdict == BLOCKED else result.verdict
            return CritiqueStageResult(
                draft=draft,
                status=PUBLISHED,
                verdict=verdict,
                advisory_findings=terminal_advisory,
                retries_used=retries_used,
            )

        if retries_used >= MAX_CRITIC_RETRIES:
            log.warning(
                "critic still blocking after %d retr%s: %d finding(s) publish unresolved",
                retries_used,
                "y" if retries_used == 1 else "ies",
                len(kept),
            )
            return CritiqueStageResult(
                draft=draft,
                status=PUBLISHED_WITH_UNRESOLVED,
                verdict=BLOCKED,
                blocking_findings=kept,
                advisory_findings=terminal_advisory,
                retries_used=retries_used,
            )

        final_round = retries_used + 1 >= MAX_CRITIC_RETRIES
        log.warning(
            "critic: %d blocking finding(s), retry %d/%d%s",
            len(kept),
            retries_used + 1,
            MAX_CRITIC_RETRIES,
            " (comply-only)" if final_round else "",
        )
        retry_prompt = render_critic_retry_prompt(
            retry_template, prior_draft=draft, blocking_findings=kept, final_round=final_round
        )
        validator_report = (draft.get("critic_report") or {}).get("validator_report")
        prior_draft = draft
        try:
            manager_result = run_manager(
                retry_prompt,
                model=manager_model,
                thesis_version=thesis_version,
                run_id=run_id,
                runner=manager_runner,
            )
        except ManagerFailed as exc:
            # A broken retry manager DEGRADES the run, never fails it: the draft
            # under judgment already passed the validator, so it is publishable.
            log.warning(
                "critic retry manager failed (%s) — publishing %d unresolved finding(s)",
                exc,
                len(kept),
            )
            trace = f"manager unavailable at retry {retries_used + 1}: {exc}"
            degraded = tuple(_note(f, trace) for f in kept)
            return CritiqueStageResult(
                draft=draft,
                status=PUBLISHED_WITH_UNRESOLVED,
                verdict=BLOCKED,
                blocking_findings=degraded,
                advisory_findings=terminal_advisory,
                retries_used=retries_used,
            )

        draft = manager_result.draft
        if validator_report is not None:
            draft.setdefault("critic_report", {})["validator_report"] = validator_report
        if not final_round:
            pending = extract_rebuttals(draft)
        _log_edited_sections(prior_draft, draft, retries_used + 1)
        draft_path.write_text(json.dumps(draft, indent=2) + "\n")
        retries_used += 1


def _note(finding: dict, note: str) -> dict:
    """A copy of `finding` with `note` appended in brackets — the audit crumb that
    survives into the published report without mutating the original."""
    existing = finding.get("note", "")
    joined = f"{existing} [{note}]" if existing else f"[{note}]"
    return {**finding, "note": joined}


def _log_edited_sections(before: dict, after: dict, retry: int) -> None:
    """Log which top-level sections the manager changed this round (spec/05:
    sections that already passed must not silently mutate).

    Not a gate — a legitimate fix cascades into headline/tldr, and blocking that
    would break the retry. It is mechanical VISIBILITY: an operator reading the log
    can see a retry that was meant to soften one claim quietly re-authored five
    sections. `critic_report` is expected to change (the loop re-stamps it), so it
    is excluded from the surprise set."""
    keys = (set(before) | set(after)) - {"critic_report"}
    changed = sorted(k for k in keys if before.get(k) != after.get(k))
    log.warning("retry %d edited section(s): %s", retry, ", ".join(changed) or "(none)")
