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
from collections.abc import Mapping
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
from researchswarm.dossiers import COMPANIES_DIRNAME, DOSSIER_SECTIONS
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


def stamp_generated_at(now: datetime | None = None) -> str:
    """`generated_at` for a derived file — timezone-AWARE, second precision.

    Spec/08 shows the shape twice, in the program registry and in the issue
    manifest, and both read `"2026-07-18T07:41:00+08:00"`. A bare
    `datetime.now().isoformat()` produces `"2026-07-18T22:58:38.196694"` — naive
    and microsecond-precise — which is what the first published `issues/index.json`
    actually carried.

    Both halves of the gap matter to a reader:

    - **The offset is the load-bearing half.** These files are read by a dashboard
      and diffed by a human; a naive stamp is unanchored, so "is this registry
      newer than that manifest" stops being answerable the moment anything crosses
      a machine or a DST boundary. `astimezone()` binds a naive local clock to the
      system's real offset without shifting the instant.
    - **Microseconds are noise.** They are precision the field does not have and
      no reader uses, and they make every regeneration a diff even when the second
      is unchanged.

    Taking `now` rather than reading the clock is what lets one run stamp its
    issue, its manifest and its registry with a single instant, instead of three
    near-simultaneous calls that disagree in the last digits.
    """
    return (now or datetime.now()).astimezone().replace(microsecond=0).isoformat()


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


def iter_issue_files(issues_dir: Path):
    """(stem, payload) for every readable issue in a directory, NEWEST FIRST.

    The one walk over an issues directory. Three v2↔v2 call sites had grown a
    byte-identical copy of it. The two V2 sites — `regenerate_manifest_v2` and
    `_registry_row`'s history walk — are routed through here, because v2↔v2 duplication
    earns no "v1 is deleted later" exemption: both encode the same three
    decisions, so a change to either is a silent divergence in the other.

    v1's `regenerate_manifest` is the THIRD copy and is deliberately left alone.
    Routing it through here would give frozen v1 code a new dependency, which is
    the invariant this branch is restoring elsewhere (`_apply_promotions`); v1 is
    deleted whole, and its copy dies with it.

    The three decisions, in one home:

    - **Newest-first falls out of a REVERSE FILENAME SORT.** Issue ids are ISO
      dates, so lexical order IS chronological order for `YYYY-MM-DD` — no parse
      can fail and silently reorder which issue the switcher calls "latest".
    - **`index.json` is skipped.** The manifest lives in the same directory it
      indexes; walking it would list the index as an issue.
    - **An unreadable file is SKIPPED, not fatal** (spec/08). One corrupt byte
      must not stop the dropdown listing every other issue — and a file that is
      skipped is therefore also not counted, which is the honest answer:
      `issue_count` counts issues the dashboard can actually open.

    A directory that does not exist yields nothing rather than raising: a program
    that has never published is an empty history, not an error.
    """
    issues_dir = Path(issues_dir)
    if not issues_dir.exists():
        return
    for path in sorted(issues_dir.glob("*.json"), reverse=True):
        if path.name == "index.json":
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue  # an unreadable issue is not a dropdown entry
        yield path.stem, payload


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
        "generated_at": generated_at or stamp_generated_at(),
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

# The READER-FACING markers, derived from spec/08's marker table — every row of
# it that is raised by a typed `degradation.kind` or a validator/critic advisory,
# in the order the table lists them. This is the ALLOWED SET as well as the
# ordering, and it is one named constant because it is one rule.
#
# It is a filter, and the review found out why. The first published
# `issues/index.json` carried
# `["calendar_stale", "arena_scan_dormant", "dangling_entity", "uncited_claim"]`.
# The last two are BLOCKING validator kinds (spec/07 §6). A blocking kind cannot
# reach a published issue as an unresolved defect — the gate would have stopped
# it — so what it means in a manifest is "something was caught and fixed", which
# is provenance, not triage. spec/08 scopes `flags` to "markers the reader should
# see before opening", and "a claim was uncited before the manager fixed it" is
# not a reason to open, or not open, an issue. The blocking record lives in the
# issue's own `critic_report.validator_report`, where a reader who wants the
# forensics can find it, unabridged.
#
# What is NOT sacrificed is spec/08 "Vocabulary homes": an unknown kind must
# render visibly rather than vanish. That rule is about the PAGE's chrome — a
# marker the dashboard does not recognise still draws as a neutral chip. It was
# never a licence for the manifest to promote every kind in the pipeline to a
# triage signal, and reading it that way is what put two blocking kinds in a
# reader's dropdown.
READER_FACING_FLAG_KINDS = (
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
    companies_index_path: Path | None = None
    company_paths: tuple[Path, ...] = ()

    @property
    def paths(self) -> tuple[Path, ...]:
        """Everything written, in write order — what run.py stages.

        The company layer is last and is OPTIONAL: a run with no dossiers on disk
        writes no index and returns nothing extra, so a deployment that has never
        scanned a company stages exactly the three files it always did.
        """
        written = [self.issue_path, self.manifest_path, self.registry_path]
        if self.companies_index_path is not None:
            written.append(self.companies_index_path)
        written.extend(self.company_paths)
        return tuple(written)


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


def companies_dir(root: Path) -> Path:
    """`issues/companies/` — the published dossier layer (#97).

    Beside the registry, NOT inside a program, and that placement is the whole
    argument. A dossier is shared across programs (`dossiers` rule 1); nesting it
    under `issues/<program>/` would duplicate one company into every program that
    names it and freeze each copy at that issue's date. A dossier accumulates and
    an issue is immutable — they cannot be the same file.
    """
    return Path(root) / "issues" / "companies"


def companies_index_path(root: Path) -> Path:
    """`issues/companies/index.json` — what companies we hold, and how thin each is."""
    return companies_dir(root) / "index.json"


def company_publish_path(root: Path, entity_id: str) -> Path:
    """`issues/companies/<entity_id>.json` — one company's published dossier."""
    return companies_dir(root) / f"{entity_id}.json"


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
    entries = [_manifest_entry_v2(issue) for _, issue in iter_issue_files(issues_dir)]

    manifest = {
        "program_id": program_id,
        "generated_at": generated_at or stamp_generated_at(),
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

    The result is SCOPED to `READER_FACING_FLAG_KINDS` plus the banner statuses —
    spec/08's reader-facing-marker table, and nothing else. Order follows that
    table, so a manifest diff is readable. See that constant for why the scope is
    a filter rather than only an ordering: unscoped, the first published registry
    leaked `dangling_entity` and `uncited_claim`, two BLOCKING validator kinds
    (spec/07 §6), into a list whose whole job is "should I open this".
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

    flags = [k for k in READER_FACING_FLAG_KINDS if k in kinds]
    flags += [s for s in FLAG_RUN_STATUSES if s in kinds]
    return flags


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
        "generated_at": generated_at or stamp_generated_at(),
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
    # `iter_issue_files` skips what it cannot parse, so an unreadable file is
    # also not COUNTED — the honest answer: `issue_count` counts issues the
    # dashboard can actually open.
    issues = list(iter_issue_files(issues_dir))

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


# ---------------------------------------------------------------------------
# Step 5 — the company dossier layer (#97)
# ---------------------------------------------------------------------------

# Governance fields that stay behind. `last_edited_by` records WHO wrote (loop vs
# owner) and is how [03] adjudicates a human/machine write conflict — an internal
# concern with no reader meaning. Everything else on the record ships, because
# everything else is the answer to "who are these people and what have they
# already abandoned".
_UNPUBLISHED_DOSSIER_FIELDS = frozenset({"last_edited_by"})


def company_display_name(record: Mapping) -> str | None:
    """The name to show for a dossier — its legal name, else its entity_id tail.

    A dossier whose identity section has not been scanned yet still needs a label,
    or the index renders a row a reader cannot recognise. Falling back to the id
    keeps a never-scanned company nameable without inventing a name for it.
    """
    facts = record.get("facts")
    identity = facts.get("identity") if isinstance(facts, Mapping) else None
    value = identity.get("value") if isinstance(identity, Mapping) else None
    legal_name = value.get("legal_name") if isinstance(value, Mapping) else None
    if isinstance(legal_name, str) and legal_name.strip():
        return legal_name.strip()
    entity_id = record.get("entity_id")
    if isinstance(entity_id, str) and entity_id.strip():
        return entity_id.strip()
    return None


def project_company_dossier(record: Mapping) -> dict:
    """The published projection of one dossier record.

    Near-total: the state record and the published one are almost the same file,
    and that is deliberate. The temptation is to flatten `{value, established_by,
    issue}` down to bare values so the dashboard has less to walk — and that would
    throw away the citation. Provenance is per FIELD here (`dossiers` rule 2);
    a published dossier that cannot say which run established which section is a
    company profile, not intelligence.

    `drift_log` ships whole. It is the append-only record of what we believed
    before and what corrected it, and it is the single thing that makes this
    different from a company's own about-page — the actual answer to "show me the
    entire history".
    """
    published = {
        key: value
        for key, value in record.items()
        if key not in _UNPUBLISHED_DOSSIER_FIELDS
    }
    published["name"] = company_display_name(record)
    return published


def _company_index_row(entity_id: str, record: Mapping) -> dict:
    """One index row — enough to list and triage a company without fetching it.

    Carries `thin_sections` in full rather than a count, for the same reason the
    registry carries `flags`: this is the pre-fetch triage signal, and *which*
    sections are unmeasured is the signal. A count says "incomplete"; the list
    says "we have never looked at their funding", which is actionable.
    """
    coverage = record.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    facts = record.get("facts")
    facts = facts if isinstance(facts, Mapping) else {}
    drift_log = record.get("drift_log")
    row = {
        "entity_id": entity_id,
        "name": company_display_name(record),
        "as_of": record.get("as_of"),
        "version": record.get("version"),
        "first_seen": record.get("first_seen"),
        "sections_held": [name for name in DOSSIER_SECTIONS if name in facts],
        "thin_sections": list(coverage.get("thin_sections") or []),
        "drift_entries": len(drift_log) if isinstance(drift_log, list) else 0,
    }
    degradation = coverage.get("degradation")
    if degradation:
        row["degradation"] = degradation
    return row


def write_company_dossiers(
    root: Path, *, generated_at: str | None = None
) -> tuple[Path | None, tuple[Path, ...]]:
    """Publish every dossier on disk. Returns `(index_path, dossier_paths)`.

    Wholesale on every run, for the registry's reason and not a new one: nothing
    here reads the published layer, so nothing can carry forward from it and a
    stale published dossier is impossible by construction rather than by locking.

    **A company with no dossier is absent, not present-and-empty.** The index
    lists what we hold; a company we have merely *named* (a holder string on an
    asset) has no row until a scan has actually built its record. That keeps
    "we have not looked yet" distinguishable from "we looked and found nothing",
    which is the distinction the whole honesty layer rests on.

    Returns `(None, ())` when no dossiers exist — no empty index is written. An
    index asserting zero companies and the absence of the file both mean "nothing
    published yet", and writing the file would put an artifact in git for every
    deployment that has never run a dossier scan.

    TOTAL. This runs after the run has committed to succeeding, so an unreadable
    record is skipped with a warning and every other company still publishes. A
    dossier that took the emission seam down would kill the run *after* the
    intelligence was written — the failure this repo has shipped five times.
    """
    root = Path(root)
    source_dir = root / "state" / "entities" / COMPANIES_DIRNAME
    rows: list[dict] = []
    written: list[Path] = []

    for path in sorted(source_dir.glob("*.json")) if source_dir.exists() else []:
        if path.name == "index.json":
            continue
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            log.warning(
                "publish: skipping unreadable dossier %s (%s) — every other company still publishes",
                path.name, exc,
            )
            continue
        if not isinstance(record, dict):
            log.warning("publish: skipping %s — not an object", path.name)
            continue
        entity_id = record.get("entity_id") or path.stem
        out = company_publish_path(root, entity_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out, project_company_dossier(record))
        written.append(out)
        rows.append(_company_index_row(entity_id, record))

    if not rows:
        return None, ()

    index = companies_index_path(root)
    index.parent.mkdir(parents=True, exist_ok=True)
    write_json(index, {
        "generated_at": generated_at or stamp_generated_at(),
        "companies": rows,
    })
    return index, tuple(written)


# ---------------------------------------------------------------------------
# The v2 recipe — the writes, in order
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
    generated_at = stamp_generated_at(now)
    issue_file = write_issue_v2(root, program_id, issue)
    manifest = regenerate_manifest_v2(
        program_issues_dir(root, program_id), program_id, generated_at=generated_at
    )
    registry = write_registry(root, generated_at=generated_at)
    # LAST, and outside the program's directory: the dossier layer is global, so
    # it is written after the per-program files and rebuilt wholesale like the
    # registry above it. A run that has never scanned a company writes nothing
    # here and stages exactly the three files it always did.
    companies_index, company_files = write_company_dossiers(root, generated_at=generated_at)
    log.info(
        "publish: %s → issue, manifest, a wholesale registry rewrite%s",
        issue_file.name,
        f", and {len(company_files)} company dossier(s)" if company_files else "",
    )
    return PublishResultV2(
        issue_path=issue_file,
        manifest_path=manifest,
        registry_path=registry,
        companies_index_path=companies_index,
        company_paths=company_files,
    )
