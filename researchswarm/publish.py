"""Stage 6 — publish: the issue reaches disk, state edits itself, one commit lands.

The last stage, and the one where the pipeline becomes a product. A stage module
in the idiom of research.py / synthesis.py / validation.py: it owns the recipe,
run.py stays a recipe-follower. The recipe is FIVE ordered steps, and the order
is load-bearing:

  1. **Derive stats** — compute the bar from the arrays, never trust the manager
     ([07]: stats is derived, never authored). Runs first so the issue that
     reaches disk already carries a stats block the validator would re-accept.
  2. **Write issues/<date>.json** — immutable. A later run never edits an earlier
     issue ([08]); the guard lives in stub.check_overwritable, shared with the
     stub writer so "already published" means one thing.
  3. **Regenerate issues/index.json** — derived from the issues ON DISK, so it is
     rebuilt AFTER the issue is written and therefore includes it. If the
     manifest and the disk ever disagree, disk wins by construction.
  4. **Apply state edits** — the governance contract made real. This module owns
     the *publication* recipe; the self-edits themselves (promotions, thesis
     revisions, queue transitions) live in state_edits.py, which this imports as a
     seam — publication and the governance contract are different concerns.
  5. **One git commit** — the whole run as a single reviewable diff. If there is
     nothing to commit or git is unavailable, the run does NOT fail: the issue is
     already on disk, and the commit is the review trail, not the product.

A **stub is a run outcome too**, and publish_stub gives it the same last two
steps: a failed run's stub reaches the manifest and the commit at write time, not
whenever the next successful run happens to regenerate them — otherwise index.json
carries an unexplained date gap ([08]) and "one commit per run" quietly means
"per successful run".

Spec: docs/spec/08-publishing-and-dashboard.md, docs/spec/09-orchestrator.md,
docs/spec/03-state-and-governance.md, docs/spec/07-issue-schema.md
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from researchswarm.critic import (
    NOT_RUN,
    PUBLISHED_UNCRITIQUED,
    PUBLISHED_WITH_UNRESOLVED,
)
from researchswarm.critique import CritiqueStageResult
from researchswarm.programs import load_program
from researchswarm.runs import latest_covering_issue
from researchswarm.state import State
from researchswarm.state_edits import apply_state_edits, write_json
from researchswarm.stub import check_overwritable, issue_path, write_failed_stub
from researchswarm.validator import derive_stats

if TYPE_CHECKING:
    from researchswarm.calendar import SurgeState

log = logging.getLogger("researchswarm.publish")

# The pre-critic default outcome: when stage 5 produced no CritiqueStageResult, a
# run publishes uncritiqued by construction — a legitimate status, not a stopgap.
# The digest is good, unvetted, and honest about it; the uncritiqued banner the
# dashboard draws is PRECISELY run.status, so no separate banner artifact is
# written. The literals come from critic — one home for the vocabulary.
UNCRITIQUED_STATUS = PUBLISHED_UNCRITIQUED
UNCRITIQUED_VERDICT = NOT_RUN

# Advisory kinds a reader should see in the dropdown BEFORE opening the issue —
# the markers that change how much to trust it. They ride in the manifest's
# `flags`. "beats_failed" is added mechanically when the audit trail carries any
# dead beat. Kept small on purpose: flags are a triage signal, not the full
# degradation record (which lives in the issue itself).
FLAG_ADVISORY_KINDS = ("calendar_stale", "continuity_baseline_expired")


@dataclass(frozen=True)
class PublishResult:
    """What stage 6 hands back: where the issue landed, the status it published
    under, the stats it stamped, which state files it edited, and whether the run
    committed. `committed` is False on a benign no-op (nothing staged, or git
    unavailable) — the issue is still on disk, so it is not a failure."""

    issue_path: Path
    manifest_path: Path
    status: str
    stats: dict
    state_paths: tuple[Path, ...]
    committed: bool


# ---------------------------------------------------------------------------
# Step 1 — derive stats and stamp the orchestrator-owned run fields
# ---------------------------------------------------------------------------


def derive_full_stats(issue, issues_dir: Path) -> dict:
    """The full stats block: the six shared counts plus `previous_issue`.

    The counts come from `validator.derive_stats` — the same function the gate
    uses to check them, so the stamp cannot drift from what the validator would
    re-accept. `previous_issue` is the id of the most recent issue that actually
    covered days, found by the continuity walk that skips stubs; null on run #1,
    which is TRUE, not a bootstrap flag — there simply was no prior issue ([07]).
    """
    stats = derive_stats(issue)
    prior = latest_covering_issue(issues_dir)
    stats["previous_issue"] = (
        prior.payload["issue"]["id"] if prior.payload is not None else None
    )
    return stats


def stamp_run_fields(
    issue, stats: dict, critic: CritiqueStageResult | None = None,
    surge: "SurgeState | None" = None,
) -> None:
    """Stamp the fields the ORCHESTRATOR owns, overwriting the manager's guesses.

    `stats` is derived, never authored, so it replaces whatever the seam forced
    to {}. `run.status`, `run.critic_verdict`, `run.critic_retries`, `run.surge`,
    and the critic_report's own verdict/findings are ALL the orchestrator's to set
    — a critic outcome and a surge are properties of the RUN, not of the draft, so
    the manager's authored guesses (including its zero critic_retries) are
    overwritten rather than trusted, and #35's real retry counts cannot diverge
    from a stale zero.

    `surge` is the resolved SurgeState or None. Its `{window, day, of}` is stamped
    when a surge is live and REMOVED otherwise — absent, never null, on a baseline
    run (spec/02, spec/07); the manager is told to omit it, but the orchestrator
    owns the field, so a manager that emitted one anyway cannot leave a false surge
    on a baseline issue.

    `critic` is the stage-5 CritiqueStageResult or None. None keeps the pre-critic
    default — published_uncritiqued / not_run — still correct for a run where stage
    5 produced no outcome. Either way `critic_report.validator_report` is PRESERVED:
    stage 4 stamped it, it is the validator's record, not the critic's to clobber.
    """
    issue["stats"] = stats
    run = issue.setdefault("issue", {}).setdefault("run", {})
    if surge is not None:
        run["surge"] = surge.run_block
    else:
        run.pop("surge", None)  # absent, not null, on a baseline run
    report = issue.setdefault("critic_report", {})
    validator_report = report.get("validator_report")  # stage 4's — preserve it

    if critic is None:
        run["status"] = UNCRITIQUED_STATUS
        run["critic_verdict"] = UNCRITIQUED_VERDICT
        run["critic_retries"] = 0
        report["verdict"] = UNCRITIQUED_VERDICT
        report["retries_used"] = 0
        report["blocking_findings"] = []
        report["advisory_findings"] = []
    else:
        run["status"] = critic.status
        run["critic_verdict"] = critic.verdict
        run["critic_retries"] = critic.retries_used
        report["verdict"] = critic.verdict
        report["retries_used"] = critic.retries_used
        report["blocking_findings"] = list(critic.blocking_findings)
        report["advisory_findings"] = list(critic.advisory_findings)
        # critic_report.reason is a DELIBERATE extension beyond spec/07's shape:
        # on not_run it carries the banner's explanation of WHY the run went
        # uncritiqued (missing binary, timeout, unparseable output), not just that
        # it did. Omitted when there is no reason to record.
        if critic.reason:
            report["reason"] = critic.reason

    report["validator_report"] = validator_report


# ---------------------------------------------------------------------------
# Step 2 — write the immutable issue file
# ---------------------------------------------------------------------------


def write_issue(root: Path, issue) -> Path:
    """Write issues/<date>.json, refusing to overwrite a published issue.

    The immutability guard is shared with the stub writer (stub.check_overwritable):
    a same-day rerun may replace its own earlier FAILED stub — a retried failure
    succeeding is exactly the behaviour we want — but a published issue is
    immutable and this refuses to touch one, raising PublishedIssueExists.
    """
    path = issue_path(root, issue["issue"]["id"])
    check_overwritable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, issue)
    return path


# ---------------------------------------------------------------------------
# Step 3 — regenerate the manifest from the issues on disk
# ---------------------------------------------------------------------------


def regenerate_manifest(issues_dir: Path, *, generated_at: str | None = None) -> Path:
    """Rewrite issues/index.json from every issue on disk, newest first.

    Derived, never hand-edited ([08]): the manifest is a projection of the
    issues, so it is rebuilt whole every run rather than patched. Stubs appear
    with status "failed" and a null headline_title — a date gap is never
    unexplained. Unreadable issue files are skipped rather than fatal: one
    corrupt byte must not stop the dropdown from listing every other issue.
    """
    issues_dir = Path(issues_dir)
    entries = []
    for path in sorted(issues_dir.glob("*.json"), reverse=True):
        if path.name == "index.json":
            continue
        try:
            issue = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue  # an unreadable issue is not a dropdown entry
        entries.append(_manifest_entry(issue))

    manifest = {
        "generated_at": generated_at or datetime.now().isoformat(),
        "issues": entries,
    }
    path = issues_dir / "index.json"
    issues_dir.mkdir(parents=True, exist_ok=True)
    write_json(path, manifest)
    return path


def _manifest_entry(issue) -> dict:
    """One dropdown row — the subset a reader needs to triage without opening.

    headline_title is null for a stub (it has no headline). `surge` rides only
    when the issue carries it — absent on a baseline run, never null, so the
    dropdown can group an ASCO week under its window without opening five files.
    The stats subset is {tracked_updates, sources_cited}; a stub's stats is {}, so
    the subset is empty rather than a row of nulls.
    """
    block = issue.get("issue", {})
    run = block.get("run", {})
    headline = issue.get("headline")
    stats = issue.get("stats") or {}

    entry = {
        "id": block.get("id"),
        "published_at": block.get("published_at"),
        "coverage_window": block.get("coverage_window"),
        "status": run.get("status"),
        "headline_title": headline.get("title") if isinstance(headline, dict) else None,
        "stats": {k: stats[k] for k in ("tracked_updates", "sources_cited") if k in stats},
        "flags": _manifest_flags(issue),
    }
    surge = run.get("surge")
    if surge:
        entry["surge"] = surge
    return entry


def _manifest_flags(issue) -> list[str]:
    """The advisory markers worth seeing before opening — mechanical, no judgment.

    Two sources, both facts the issue already carries: the advisory kinds the
    validator or critic filed (calendar_stale, continuity_baseline_expired), and
    "beats_failed" whenever the audit trail names a dead beat. Order is stable
    (registered kinds first, then beats_failed) so a diff of the manifest is
    readable.
    """
    critic_report = issue.get("critic_report") or {}
    validator_report = critic_report.get("validator_report") or {}
    kinds = {
        f.get("kind")
        for f in (validator_report.get("findings") or [])
        + (critic_report.get("advisory_findings") or [])
        if isinstance(f, dict)
    }
    flags = [kind for kind in FLAG_ADVISORY_KINDS if kind in kinds]
    if (issue.get("sources_and_method") or {}).get("beats_failed"):
        flags.append("beats_failed")
    return flags


# ---------------------------------------------------------------------------
# Step 5 — the single git commit, and the manifest+commit both outcomes share
# ---------------------------------------------------------------------------


def git_commit_run(root: Path, run_id: str, paths, *, message: str, runner=subprocess.run) -> bool:
    """Stage the run's artifacts and commit them as one reviewable diff.

    The commit is the review trail that replaces a human approval step: the issue,
    the regenerated manifest, and the edited state files land as one diff citing
    the run_id. The run's findings + draft stay OUT of it — `runs/` is gitignored
    working papers with its own 24-run retention on disk (spec/09), and staging an
    ignored path makes `git add` fail wholesale, taking the real artifacts down
    with it.

    Failure here is NOT a run failure. If there is nothing to commit (a run that
    changed no state and re-derived an identical manifest can still stage the
    issue, but a dry rerun may stage nothing) or git is unavailable, this logs a
    warning and returns False — the issue is already on disk, and the product does
    not depend on the commit. The commit shells out through an injected runner so
    the failure path is testable without a real repo.
    """
    root = Path(root)
    rel = [str(Path(p).relative_to(root)) for p in paths if Path(p).exists()]
    if not rel:
        log.warning("publish: nothing to stage for %s — commit skipped", run_id)
        return False
    try:
        add = runner(
            ["git", "-C", str(root), "add", *rel], capture_output=True, text=True
        )
        if add.returncode != 0:
            log.warning(
                "publish: git add failed (%s) — issue is on disk, commit skipped",
                (add.stderr or "").strip(),
            )
            return False
        commit = runner(
            ["git", "-C", str(root), "commit", "-m", message], capture_output=True, text=True
        )
        if commit.returncode != 0:
            log.warning(
                "publish: git commit did not land (%s) — issue is on disk",
                (commit.stdout or commit.stderr or "").strip(),
            )
            return False
    except Exception as exc:  # git missing, or the injected runner raised
        log.warning("publish: git unavailable (%s) — issue is on disk, commit skipped", exc)
        return False
    log.info("publish: committed run %s", run_id)
    return True


def _manifest_and_commit(
    root: Path,
    *,
    run_id: str,
    now: datetime,
    issue_file: Path,
    state_paths,
    message: str,
    runner,
) -> tuple[Path, bool]:
    """Regenerate the manifest from disk and commit the run — the shared tail.

    Every run outcome ends the same way: the manifest is a projection of the
    issues on disk (so it always includes what was just written, published or
    stub), and the whole run lands as one commit. Sharing this between the
    published path and the stub path is what makes "in the dropdown" and "one
    commit per run" true for BOTH — a stub that skipped these would leave an
    unexplained date gap until the next success.
    """
    manifest_path = regenerate_manifest(root / "issues", generated_at=now.isoformat())
    committed = git_commit_run(
        root,
        run_id,
        [issue_file, manifest_path, *state_paths],
        message=message,
        runner=runner,
    )
    return manifest_path, committed


# ---------------------------------------------------------------------------
# The recipe, and the stub that shares its tail
# ---------------------------------------------------------------------------


def run_publish_stage(
    root: Path,
    *,
    draft: dict,
    state: State,
    run_id: str,
    now: datetime,
    critic: CritiqueStageResult | None = None,
    surge: "SurgeState | None" = None,
    runner=subprocess.run,
) -> PublishResult:
    """Run stage 6's five ordered steps and return where the issue landed.

    `draft` is the validated issue from stage 4 — validator_report already
    stamped, stats still {}. `critic` is the stage-5 outcome that decides the
    published run.status and critic_report (None → the pre-critic
    published_uncritiqued default). This derives stats, stamps the
    orchestrator-owned run fields, writes the immutable issue, regenerates the
    manifest, applies the state edits, and commits once. May raise
    PublishedIssueExists if the date already holds a published issue (immutability).

    Once the issue is ON DISK, a downstream failure (state edits, say) must not
    leave the artifact trail behind the artifact: the manifest is regenerated and
    the run committed on a best-effort basis before the exception propagates, so
    the caller's stub attempt finds the day already published (and correctly fails
    without laundering it), while the manifest and commit still reflect what
    reached disk.
    """
    issues_dir = root / "issues"

    stats = derive_full_stats(draft, issues_dir)
    stamp_run_fields(draft, stats, critic, surge)

    # write_issue may raise BEFORE anything lands (immutability) — a clean fail.
    issue_file = write_issue(root, draft)
    issue_id = draft["issue"]["id"]
    status = draft["issue"]["run"]["status"]
    message = f"run {run_id}: publish {issue_id} ({status})"

    try:
        state_paths = apply_state_edits(root, draft, state, run_id, now)
    except Exception:
        # The issue is already published on disk; keep the trail current even as
        # this run fails, then let the failure propagate to the caller's handler.
        log.warning("publish: state edits failed after the issue was written — committing what exists")
        _manifest_and_commit(
            root, run_id=run_id, now=now, issue_file=issue_file, state_paths=(),
            message=message, runner=runner,
        )
        raise

    manifest_path, committed = _manifest_and_commit(
        root, run_id=run_id, now=now, issue_file=issue_file, state_paths=state_paths,
        message=message, runner=runner,
    )

    return PublishResult(
        issue_path=issue_file,
        manifest_path=manifest_path,
        status=status,
        stats=stats,
        state_paths=tuple(state_paths),
        committed=committed,
    )


def publish_stub(
    root: Path,
    *,
    run_id: str,
    now: datetime,
    window: dict,
    stage: str,
    detail: str,
    thesis_version=None,
    beats_failed=None,
    runner=subprocess.run,
) -> Path:
    """Write a failed-run stub AND land it in the manifest and one commit.

    A stub is a run OUTCOME, not the absence of one: it must reach the dropdown
    and the commit trail exactly as a published issue does. Wrapping the stub
    writer with the shared manifest+commit tail is the one home for that — every
    failure path in run.py calls this, so none can forget, and "one commit per
    run" holds for failed runs too. Raises PublishedIssueExists (from
    write_failed_stub) when the date already published — the caller reports the
    failure without overwriting the real issue.

    A stub deliberately does NOT carry the calendar_stale marker: it never reaches
    stage 4, where the validator files that advisory. This is a conscious narrowing
    of spec/02's "marker in every issue" — a stub already announces its failure
    loudly in the reader's path, so the silent-failure risk the marker exists to
    prevent (a normal-looking digest shipping while ASCO reprices two companies)
    lives entirely in PUBLISHED digests, and those all pass through validation.
    """
    path = write_failed_stub(
        root, run_id=run_id, now=now, window=window, stage=stage, detail=detail,
        thesis_version=thesis_version, beats_failed=beats_failed,
    )
    message = f"run {run_id}: stub {now.date().isoformat()} (failed at {stage})"
    _manifest_and_commit(
        root, run_id=run_id, now=now, issue_file=path, state_paths=(),
        message=message, runner=runner,
    )
    return path


# ===========================================================================
# v2 — the per-program detective's three files
# ===========================================================================
#
# Everything above this line is v1's flat single-digest publisher and is
# UNTOUCHED: v1 is deleted whole, as its own ticket, not eroded in place. The v2
# publisher is additive beside it for the same reason every other v2 twin on this
# branch is (research, synthesis, critique, validation, state edits) — `--program`
# selects the whole v2 stage machine, so no function has to ask which schema it
# is serving.
#
# v2 emits THREE kinds of file, and naming the third is the point (spec/08 "The
# program registry"):
#
#   issues/<program_id>/<date>.json   the issue      — immutable, per-program
#   issues/<program_id>/index.json    the manifest   — derived,   per-program
#   issues/index.json                 the registry   — derived,   CROSS-program
#
# The registry is the new thing. It is what the program switcher reads, so it can
# label every detective without opening any program's manifest, and it is what
# lets the identity card paint before the issue is fetched (spec/08 "The data
# layer": first paint does not wait for the issue).
#
# The SOLE-WRITER invariant is unbroken. Everything here is a library function
# called by run.py; nothing in this section shells out to git, and nothing here
# runs off a background path. `publish.py` is `run.py`'s library, not a second
# actor (spec/08 "The program registry", last rule).


# The failure status a stub publishes under (stub.write_failed_stub). Named here
# so the v2 flag derivation keys on a CONSTANT rather than a bare literal — the
# whole point of v2's typed markers is that the chrome never greps prose.
FAILED_STATUS = "failed"

# The run statuses that are themselves reader-facing markers (spec/08 "Reader-
# facing markers": the uncritiqued banner, the unresolved-findings banner, and
# the failed-run stub are all raised BY `run.status`). They ride into `flags` as
# their own literals — no second vocabulary is invented for them, because the
# status string already IS the type the page keys on.
FLAG_RUN_STATUSES = (
    PUBLISHED_UNCRITIQUED,
    PUBLISHED_WITH_UNRESOLVED,
    FAILED_STATUS,
)

# The typed degradation/finding kinds the pipeline can raise, in the order
# spec/08's marker table lists them. This is an ORDERING, not a filter: an
# unrecognised kind is still emitted (sorted, after these), because spec/08
# "Vocabulary homes" is explicit that an unknown kind must render visibly rather
# than vanish — a marker the page does not recognise is exactly when the reader
# most needs to know something was raised.
FLAG_KIND_ORDER = (
    "calendar_stale",
    "arena_scan_failed",
    "arena_scan_dormant",
    "china_feed_partial",
    "interest_list_stale",
)

# The manifest's stats subset — the counts a reader triages on without opening
# the issue. Deliberately four of the v2 block's nine (07 `stats`): what moved,
# how well sourced, how wide the aperture reached, and what is new. A stub's
# stats is {}, so the subset comes out empty rather than a row of nulls.
MANIFEST_STATS_KEYS_V2 = (
    "competitors_moved",
    "sources_cited",
    "indications_covered",
    "newly_discovered",
)


@dataclass(frozen=True)
class PublishResultV2:
    """What the v2 publisher hands back: the three files it wrote.

    All three are returned because run.py stages all three in the run's single
    commit. Returning only the issue would leave the derived files behind the
    artifact in git — the manifest and registry are what the dashboard actually
    fetches, so a commit without them publishes an issue no reader can reach.
    """

    issue_path: Path
    manifest_path: Path
    registry_path: Path

    @property
    def paths(self) -> tuple[Path, ...]:
        """The three, in write order — what run.py stages."""
        return (self.issue_path, self.manifest_path, self.registry_path)


# ---------------------------------------------------------------------------
# Layout — one home for where a v2 file lives
# ---------------------------------------------------------------------------


def program_issues_dir(root: Path, program_id: str) -> Path:
    """`issues/<program_id>/` — one program's whole history (spec/07 `program_id`
    is the join key; issues are stored per program per [#59])."""
    return Path(root) / "issues" / program_id


def issue_path_v2(root: Path, program_id: str, issue_id: str) -> Path:
    """`issues/<program_id>/<date>.json`.

    The v2 twin of `stub.issue_path`, kept as its own function rather than a
    parameter on that one: the flat and the nested layouts coexist while v1 is
    alive, and a single function that switched on an argument would be the
    dispatch-inside-a-stage this branch has avoided everywhere else.
    """
    return program_issues_dir(root, program_id) / f"{issue_id}.json"


def registry_path(root: Path) -> Path:
    """`issues/index.json` — the cross-program registry (spec/08)."""
    return Path(root) / "issues" / "index.json"


# ---------------------------------------------------------------------------
# Step 2 — the immutable issue
# ---------------------------------------------------------------------------


def write_issue_v2(root: Path, program_id: str, issue: dict) -> Path:
    """Write `issues/<program_id>/<date>.json`, refusing to overwrite a publication.

    The immutability guard is `stub.check_overwritable`, REUSED rather than
    rewritten (spec/08 "Published issues are immutable"). Sharing it with v1 and
    with the stub writer is what makes "already published" mean exactly one thing
    across every writer of an issue path: a same-day rerun may replace its own
    earlier FAILED stub — a retried failure succeeding is the behaviour we want —
    but a published issue is untouchable and this raises PublishedIssueExists.
    """
    path = issue_path_v2(root, program_id, issue["issue"]["id"])
    check_overwritable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, issue)
    return path


# ---------------------------------------------------------------------------
# Step 3 — the per-program manifest
# ---------------------------------------------------------------------------


def regenerate_manifest_v2(
    issues_dir: Path, program_id: str, *, generated_at: str | None = None
) -> Path:
    """Rewrite `issues/<program_id>/index.json` from the issues on disk, newest first.

    Same reconciliation rule as v1 (spec/08 "The issue manifest"): the manifest is
    a PROJECTION of the issues, so it is rebuilt whole every run rather than
    patched, and if it ever disagrees with disk, disk wins by construction. Stubs
    appear with `status: "failed"` — an unexplained date gap is exactly the silent
    failure the whole design refuses. An unreadable issue file is skipped rather
    than fatal: one corrupt byte must not stop the dropdown listing every other
    issue.

    Newest-first falls out of a reverse filename sort because issue ids are ISO
    dates — lexical order IS chronological order for `YYYY-MM-DD`, so no parse can
    fail and reorder the dropdown.
    """
    issues_dir = Path(issues_dir)
    entries = []
    for path in sorted(issues_dir.glob("*.json"), reverse=True):
        if path.name == "index.json":
            continue
        try:
            issue = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue  # an unreadable issue is not a dropdown entry
        entries.append(_manifest_entry_v2(issue))

    manifest = {
        "program_id": program_id,
        "generated_at": generated_at or datetime.now().isoformat(),
        "issues": entries,
    }
    issues_dir.mkdir(parents=True, exist_ok=True)
    path = issues_dir / "index.json"
    write_json(path, manifest)
    return path


def _manifest_entry_v2(issue: dict) -> dict:
    """One row of a program's issue dropdown (spec/08 "The issue manifest").

    The subset a reader triages on without opening the file: id, when, what it
    covered, how it ran, its headline, its counts, and its markers. Two shapes are
    deliberate rather than incidental:

    - `headline_title` is null for a stub — a stub has no headline, and null says
      so honestly instead of faking one from the failure detail.
    - `surge` rides ONLY when the issue carries it — absent, never null, on a
      baseline run, matching how the orchestrator stamps it (`stamp_run_fields`).
      It is load-bearing in the manifest: `surge.window` is what lets the dropdown
      group an ASCO week under its conference name without opening five files.
    """
    block = issue.get("issue", {})
    run = block.get("run", {})
    headline = issue.get("headline")
    stats = issue.get("stats") or {}

    entry = {
        "id": block.get("id"),
        "published_at": block.get("published_at"),
        "coverage_window": block.get("coverage_window"),
        "status": run.get("status"),
        "headline_title": headline.get("title") if isinstance(headline, dict) else None,
        "stats": {k: stats[k] for k in MANIFEST_STATS_KEYS_V2 if k in stats},
        "flags": _manifest_flags_v2(issue),
    }
    surge = run.get("surge")
    if surge:
        entry["surge"] = surge
    return entry


def _manifest_flags_v2(issue: dict) -> list[str]:
    """The markers a reader should see BEFORE opening — typed, never grepped.

    This is the concrete cash-out of spec/08 "Vocabulary homes": *"`MARKER`'s regex
    is retired. v2 carries `degradation.kind` and `run.status` as typed fields, so
    the chrome keys on the type and never greps the prose."* v3 conceded its regex
    was "HEURISTIC, not a contract" and that a reworded marker silently degraded to
    prose. Nothing here reads a `marker` string; every flag comes from a typed
    field the pipeline already writes.

    Four typed sources, all facts the issue carries:

    1. Every `degradation.kind` anywhere in the issue — competitors, indications,
       treatment landscapes. The walk is structural rather than a hand-listed set
       of paths, because spec/07 attaches a degradation wherever an absence occurs
       and a maintained path list would rot the first time a new section grew one.
    2. The typed `kind` of every validator finding and critic advisory finding —
       this is where `calendar_stale` is filed.
    3. `sources_and_method.interest_list.rot_status == "stale"` → the whole-list
       `interest_list_stale` rot line (spec/07 `interest_list`, [#55]).
    4. `run.status`, when the status is itself a banner (spec/08's marker table).

    Order is stable — the registered kinds in spec/08's table order, then any
    unrecognised kind sorted — so a manifest diff is readable. Unrecognised kinds
    are EMITTED, not dropped: spec/08 requires an unknown kind to render visibly,
    and a flag list that silently filtered would defeat that in Python before the
    page ever saw it.
    """
    kinds: set[str] = set(_walk_degradation_kinds(issue))

    critic_report = issue.get("critic_report") or {}
    validator_report = critic_report.get("validator_report") or {}
    for finding in list(validator_report.get("findings") or []) + list(
        critic_report.get("advisory_findings") or []
    ):
        if isinstance(finding, dict) and finding.get("kind"):
            kinds.add(finding["kind"])

    interest_list = (issue.get("sources_and_method") or {}).get("interest_list") or {}
    if interest_list.get("rot_status") == "stale":
        kinds.add("interest_list_stale")

    status = (issue.get("issue") or {}).get("run", {}).get("status")
    if status in FLAG_RUN_STATUSES:
        kinds.add(status)

    known = [k for k in FLAG_KIND_ORDER if k in kinds]
    known += [s for s in FLAG_RUN_STATUSES if s in kinds]
    return known + sorted(kinds - set(known))


def _walk_degradation_kinds(node) -> list[str]:
    """Every typed `degradation.kind` anywhere in the issue tree.

    Structural, not path-listed, for the reason in `_manifest_flags_v2`: spec/07
    hangs a `degradation` off whatever object the absence belongs to, so walking
    is the only derivation that cannot rot when a new section grows one. A
    `degradation` of null (the common case) contributes nothing.
    """
    found: list[str] = []
    if isinstance(node, dict):
        degradation = node.get("degradation")
        if isinstance(degradation, dict) and degradation.get("kind"):
            found.append(degradation["kind"])
        for value in node.values():
            found.extend(_walk_degradation_kinds(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_degradation_kinds(value))
    return found


# ---------------------------------------------------------------------------
# Step 3b — the cross-program registry
# ---------------------------------------------------------------------------


def write_registry(root: Path, *, generated_at: str | None = None) -> Path:
    """Rewrite `issues/index.json` WHOLESALE — config ⋈ state, config on the left.

    The key behaviour of the whole file, and it is a join, not a patch (spec/08
    "The program registry"):

    - **Regenerated wholesale on every run.** A run touches ONE program but
      rewrites EVERY row. That is what makes a stale row impossible by
      construction rather than by locking — the same reconciliation the manifest
      already uses, inherited rather than reinvented. Nothing here reads the
      existing registry, precisely so nothing can carry forward from it.
    - **Config is the LEFT side.** A program exists because it has a
      `config/programs/<id>.toml`, not because it has published. So a program
      that has never run still appears, with `latest_issue: null` and
      `issue_count: 0`. The switcher shows it and the page renders its identity
      card over a "no issues yet" empty state. Fail-visible over clean: a
      detective that exists but is invisible is exactly the silent absence this
      design refuses, and it is the program-altitude twin of *stubs appear*.
    - **`sponsor` and `mechanism` ride here on purpose** — they are the
      five-second test, and carrying them in the registry lets the identity card
      paint before the issue is fetched (spec/08 "The data layer").

    A stub reaching the registry needs no special case, and that is a property of
    the join rather than a happy accident: the row exists because the `.toml`
    exists, and `latest_issue` is whatever the newest file on disk is — a stub is
    a file on disk. The program appears whether it has published, stubbed, or
    never run at all.

    A program whose `.toml` will not parse is SKIPPED with a warning rather than
    taking the registry down: one broken config must not make every other
    detective unreachable, which is the whole-page error spec/08 grades a registry
    failure as.
    """
    root = Path(root)
    programs_dir = root / "config" / "programs"
    rows = []
    for path in sorted(programs_dir.glob("*.toml")) if programs_dir.exists() else []:
        try:
            program = load_program(root / "config", path.stem)
        except (KeyError, ValueError) as exc:
            log.warning(
                "registry: skipping %s — %s; every other program still lists",
                path.name, exc,
            )
            continue
        rows.append(_registry_row(root, program))

    registry = {
        "generated_at": generated_at or datetime.now().isoformat(),
        "programs": rows,
    }
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, registry)
    return path


def _registry_row(root: Path, program) -> dict:
    """One program switcher row — its config identity joined to its issues on disk.

    The config half (`display_name`, `sponsor`, `mechanism`) is read fresh from the
    `.toml` every run, so an owner's edit to the aperture reaches the switcher on
    the next run without any migration. The state half is read from the program's
    directory, NOT from its manifest: the manifest is itself derived, and deriving
    the registry from another derived file would give a stale manifest two chances
    to poison the switcher instead of none.

    `flags` carries the LATEST issue's flags. The registry's job is the
    pre-fetch triage signal — "should I look at this detective before I open it" —
    and the newest issue is what that question is about; older issues' markers are
    already visible in that program's own dropdown, one hop away.
    """
    issues_dir = program_issues_dir(root, program.id)
    issues = _published_issue_files(issues_dir)

    latest_id = None
    latest_published_at = None
    flags: list[str] = []
    if issues:
        latest_id, latest = issues[0]
        latest_published_at = (latest.get("issue") or {}).get("published_at")
        flags = _manifest_flags_v2(latest)

    return {
        "program_id": program.id,
        "display_name": program.name,
        "sponsor": program.sponsor,
        "mechanism": program.mechanism,
        "latest_issue": latest_id,
        "latest_published_at": latest_published_at,
        "issue_count": len(issues),
        "flags": flags,
    }


def _published_issue_files(issues_dir: Path) -> list[tuple[str, dict]]:
    """Every readable issue in a program's directory, newest first.

    Newest-first by reverse filename sort, for the reason in
    `regenerate_manifest_v2`: ISO date ids sort lexically into chronological
    order, so no parse can fail and silently reorder which issue the switcher
    calls "latest". Unreadable files are skipped — and are therefore not counted
    either, which is the honest answer: `issue_count` counts issues the dashboard
    can actually open.
    """
    issues_dir = Path(issues_dir)
    if not issues_dir.exists():
        return []
    out = []
    for path in sorted(issues_dir.glob("*.json"), reverse=True):
        if path.name == "index.json":
            continue
        try:
            out.append((path.stem, json.loads(path.read_text())))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# The v2 recipe — the three writes, in order
# ---------------------------------------------------------------------------


def run_publish_stage_v2(
    root: Path,
    *,
    issue: dict,
    program_id: str,
    run_id: str,
    now: datetime | None = None,
) -> PublishResultV2:
    """Write the issue, its manifest, and the registry — the v2 emission seam.

    Called BY run.py as its `publisher`, never by anything else. The order is
    load-bearing and is the same argument v1's recipe makes: the issue is written
    FIRST, and both derived files are rebuilt AFTER, so each is a projection of a
    disk state that already includes what this run just published. Deriving them
    first would publish a manifest that does not list its own issue.

    Deliberately narrower than v1's `run_publish_stage`: stats derivation, the
    state edits and the git commit all stay in `_main_v2`, which already owns
    them. This is the EMISSION seam only — the three files — because that is what
    run.py's seam contract asks for and widening it here would give the run two
    owners of its commit.

    May raise `PublishedIssueExists` when the date already holds a published issue
    (spec/08 immutability). It raises BEFORE anything is written, so a refused
    overwrite leaves the manifest and registry exactly as the real issue left
    them — a clean fail, not a half-published one.

    `now` stamps `generated_at` on both derived files; defaulting to None lets the
    caller pass the run's own clock so all three artifacts agree on when the run
    happened.
    """
    generated_at = (now or datetime.now()).isoformat()
    issue_file = write_issue_v2(root, program_id, issue)
    manifest = regenerate_manifest_v2(
        program_issues_dir(root, program_id), program_id, generated_at=generated_at
    )
    registry = write_registry(root, generated_at=generated_at)
    log.info(
        "publish: %s → issue, manifest, and a wholesale registry rewrite",
        issue_file.name,
    )
    return PublishResultV2(
        issue_path=issue_file, manifest_path=manifest, registry_path=registry
    )
