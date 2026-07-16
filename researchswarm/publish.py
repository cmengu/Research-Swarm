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
  4. **Apply state edits** — the governance contract made real: promotions,
     thesis revisions, queue transitions, each citing the run_id and appending to
     that file's own log. This is what replaces a human approval step.
  5. **One git commit** — the whole run as a single reviewable diff. If there is
     nothing to commit or git is unavailable, the run does NOT fail: the issue is
     already on disk, and the commit is the review trail, not the product.

Steps 1–4 are pure of the model — every judgment was already made by the manager
and gated by the validator; publish only counts, writes, and records. Each step
is a small function testable on its own, and run_publish_stage is the recipe that
threads them.

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

from researchswarm.runs import latest_covering_issue
from researchswarm.state import State
from researchswarm.stub import check_overwritable
from researchswarm.validator import derive_stats, transition_brings_new_evidence

log = logging.getLogger("researchswarm.publish")

# While no critic exists (build 07 wires the real one), every published run is
# uncritiqued by construction — a legitimate status, not a stopgap. The digest is
# good, unvetted, and honest about it; the uncritiqued banner the dashboard draws
# is PRECISELY run.status, so no separate banner artifact is written.
UNCRITIQUED_STATUS = "published_uncritiqued"
UNCRITIQUED_VERDICT = "not_run"

# new_on_radar.type → watchlist tier, for accepted promotions. The radar surfaces
# emerging stories; a fresh promotion has NOT earned a market-structural claim, so
# the honest default is `frontier_asset` — "tracked as an asset, not a ticker,
# because the tickers keep disappearing" ([03] tiers). china_supply and platform
# are deliberately absent from the default: each asserts a price-setting or
# direction-setting role a radar item cannot have demonstrated in one cycle, and
# stamping one would launder an unproven claim into the roster's spine. A type
# that DOES carry its role (a big-pharma acquirer, a regulator) maps to it.
TYPE_TO_TIER = {
    "big_pharma": "acquirer",
    "acquirer": "acquirer",
    "regulator": "regulator",
    "china_pharma": "china_supply",
    "china_supply": "china_supply",
    "platform": "platform",
    "frontier_asset": "frontier_asset",
    "asset": "frontier_asset",
}
DEFAULT_TIER = "frontier_asset"

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
# The one json writer — every state edit goes through it
# ---------------------------------------------------------------------------


def _write_json(path: Path, data) -> None:
    """Write `data` as JSON with 2-space indent and a trailing newline.

    Matches the formatting of the seeded state files exactly, so a state edit
    shows in `git diff` as the lines that changed and nothing else — the diff is
    the review, and a reformatting churn would drown the one line that matters.
    """
    path.write_text(json.dumps(data, indent=2) + "\n")


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


def stamp_run_fields(issue, stats: dict) -> None:
    """Stamp the fields the ORCHESTRATOR owns, overwriting the manager's guesses.

    `stats` is derived, never authored, so it replaces whatever the seam forced
    to {}. `run.status` and `run.critic_verdict` are the orchestrator's to set:
    with no critic wired they are published_uncritiqued / not_run regardless of
    what the manager wrote, because a missing critic is a property of the run, not
    of the draft. `critic_report.validator_report` is left untouched — stage 4
    already stamped it, and it is the validator's record, not ours to rewrite.
    """
    issue["stats"] = stats
    run = issue.setdefault("issue", {}).setdefault("run", {})
    run["status"] = UNCRITIQUED_STATUS
    run["critic_verdict"] = UNCRITIQUED_VERDICT


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
    issue_id = issue["issue"]["id"]
    path = root / "issues" / f"{issue_id}.json"
    check_overwritable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, issue)
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
    _write_json(path, manifest)
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
# Step 4 — apply the state edits
# ---------------------------------------------------------------------------


def apply_state_edits(root: Path, issue, state: State, run_id: str, now: datetime) -> list[Path]:
    """Apply promotions, thesis revisions and queue transitions to state/.

    Each of the three writers is independent and self-contained; each cites the
    run_id and appends to its file's own log; each writes its file back only if it
    actually changed something. Returns the paths that were rewritten — what the
    commit stages. The order is arbitrary (they touch different files); listing
    them here keeps the recipe legible.
    """
    date = now.date().isoformat()
    touched: list[Path] = []
    for path, changed in (
        _apply_promotions(root, issue, state, run_id, date),
        _apply_thesis_updates(root, issue, state, run_id, date),
        _apply_queue_transitions(root, issue, state, run_id, date),
    ):
        if changed:
            touched.append(path)
    return touched


def _apply_promotions(root: Path, issue, state: State, run_id: str, date: str) -> tuple[Path, bool]:
    """Accepted promotion_proposals become watchlist entities, with a drift_log.

    Self-maintaining watchlist, no human approval — but the reason is written down
    so drift is auditable ([03] auto_promote). For each new_on_radar entry whose
    proposal says promote_to_watchlist, a new entity is appended: tier mapped from
    its type (TYPE_TO_TIER, defaulting to frontier_asset), priority and categories
    carried across, why_tracked taken from the proposal's reason. A proposal whose
    entity_id already exists is skipped with a log line — a promotion is an add,
    never an edit of a standing entity.
    """
    path = root / "state" / "watchlist.json"
    watchlist = state.watchlist
    existing = state.entity_ids
    entities = watchlist.setdefault("entities", [])
    drift_log = watchlist.setdefault("drift_log", [])
    changed = False

    for entry in issue.get("new_on_radar") or []:
        proposal = (entry or {}).get("promotion_proposal") or {}
        if not proposal.get("promote_to_watchlist"):
            continue
        entity_id = entry.get("entity_id")
        if not entity_id:
            log.warning("publish: promotion with no entity_id — skipped")
            continue
        if entity_id in existing:
            log.info("publish: %s already on the watchlist — promotion skipped", entity_id)
            continue

        tier = TYPE_TO_TIER.get(entry.get("type"), DEFAULT_TIER)
        entities.append({
            "entity_id": entity_id,
            "name": entry.get("name"),
            "tier": tier,
            "priority": entry.get("priority"),
            "why_tracked": proposal.get("reason"),
            "watch_for": entry.get("categories") or [],
        })
        drift_log.append({
            "date": date,
            "action": "promoted",
            "entity_id": entity_id,
            "reason": proposal.get("reason"),
            "run_id": run_id,
        })
        existing = existing | {entity_id}
        changed = True
        log.info("publish: promoted %s to the watchlist (tier %s)", entity_id, tier)

    if changed:
        _bump(watchlist, date)
        _write_json(path, watchlist)
    return path, changed


def _apply_thesis_updates(root: Path, issue, state: State, run_id: str, date: str) -> tuple[Path, bool]:
    """Thesis revisions become stance changes, with a per-slot drift_log entry.

    The loop may revise an ACTIVE belief (non-null stance) and must log every
    revision ([03]). It may NEVER author a stance into a DORMANT slot — a
    thesis_updates entry targeting a null-stance slot is a contract violation
    upstream (the manager must not improvise an opinion), so it is skipped LOUDLY
    rather than applied. On apply: stance becomes `after`, the slot's drift_log
    gains {date, from_stance, to_stance, trigger, cycle_id}, the top-level version
    bumps and last_evolved_at / last_edited_by record that the loop, not the
    owner, moved it — the distinction the whole governance contract turns on.
    """
    path = root / "state" / "thesis.json"
    thesis = state.thesis
    beliefs = {b.get("id"): b for b in thesis.get("beliefs", []) if isinstance(b, dict)}
    changed = False

    for update in issue.get("thesis_updates") or []:
        slot_id = (update or {}).get("field")
        belief = beliefs.get(slot_id)
        if belief is None:
            log.warning("publish: thesis_update targets unknown slot %r — skipped", slot_id)
            continue
        if belief.get("stance") is None:
            # Authoring a stance into a dormant slot is a contract violation, not
            # a judgment call: the loop must never fill an unowned opinion.
            log.warning(
                "publish: thesis_update targets DORMANT slot %r (null stance) — "
                "refusing to author a stance the owner never seeded",
                slot_id,
            )
            continue

        from_stance = belief.get("stance")
        to_stance = update.get("after")
        belief["stance"] = to_stance
        belief.setdefault("drift_log", []).append({
            "date": date,
            "from_stance": from_stance,
            "to_stance": to_stance,
            "trigger": update.get("triggered_by") or [],
            "cycle_id": run_id,
        })
        changed = True
        log.info("publish: thesis slot %r revised by the loop", slot_id)

    if changed:
        thesis["version"] = (thesis.get("version") or 0) + 1
        thesis["last_evolved_at"] = date
        thesis["last_edited_by"] = "loop"
        _write_json(path, thesis)
    return path, changed


def _apply_queue_transitions(root: Path, issue, state: State, run_id: str, date: str) -> tuple[Path, bool]:
    """Queue status / window transitions apply to state, under the source rule.

    Diffs the issue's catalyst_queue snapshot against state per item id. A
    transition applies ONLY when the item carries a new source relative to state —
    the SAME "no source, no transition" rule the validator enforces
    (transition_brings_new_evidence, shared), so the publisher can never write a
    transition the validator would have blocked. Skipped transitions are logged.

    first_expected_window is NEVER changed in state: it is immutable after
    creation, and an issue whose snapshot differs is upstream tamper the validator
    should have caught — this skips the whole item and logs, refusing to be the
    process that launders it. expected_window revisions carry their slip_log
    entries across. Items are matched by id only: this stage never adds or retires
    (the monthly re-cut, build 10, owns that). Every applied transition appends to
    the queue's drift_log with the run_id.
    """
    path = root / "state" / "catalyst-queue.json"
    queue = state.catalyst_queue
    state_items = {it.get("id"): it for it in queue.get("queue", []) if isinstance(it, dict)}
    drift_log = queue.setdefault("drift_log", [])
    changed = False

    for snap in issue.get("catalyst_queue", {}).get("items") or []:
        if not isinstance(snap, dict):
            continue
        item_id = snap.get("id")
        current = state_items.get(item_id)
        if current is None:
            log.info("publish: queue item %r not in state (add/retire is monthly) — skipped", item_id)
            continue

        if snap.get("first_expected_window") != current.get("first_expected_window"):
            log.warning(
                "publish: queue item %r first_expected_window differs from state "
                "(%r → %r) — immutable, refusing to propagate; skipping the item",
                item_id, current.get("first_expected_window"), snap.get("first_expected_window"),
            )
            continue

        status_changed = snap.get("status") != current.get("status")
        window_changed = snap.get("expected_window") != current.get("expected_window")
        if not status_changed and not window_changed:
            continue

        if not transition_brings_new_evidence(snap, current):
            log.warning(
                "publish: queue item %r transition carries no source new to state — "
                "no source, no transition; skipped", item_id,
            )
            continue

        detail = []
        if status_changed:
            current["status"] = snap.get("status")
            detail.append(f"status {snap.get('status')!r}")
        if window_changed:
            _append_new_slips(current, snap, date)
            current["expected_window"] = snap.get("expected_window")
            detail.append(f"window {snap.get('expected_window')!r}")
        # Refresh the machine-authored evidence so state's citation set stays
        # current — otherwise the same source reads as "new" again next cycle.
        current["window_source"] = snap.get("window_source")
        current["sources"] = snap.get("sources") or []

        drift_log.append({
            "date": date,
            "action": "transition",
            "item_id": item_id,
            "detail": ", ".join(detail),
            "run_id": run_id,
        })
        changed = True
        log.info("publish: queue item %r transitioned (%s)", item_id, ", ".join(detail))

    if changed:
        _bump(queue, date)
        _write_json(path, queue)
    return path, changed


def _append_new_slips(current: dict, snap: dict, date: str) -> None:
    """Carry the snapshot's new slip_log entries into the state item, in state shape.

    The snapshot's slip_log ([07]: {from, to, date, source}) is translated to the
    state item_contract shape ({date, from_window, to_window, reason, source}) so
    the state file stays internally consistent. An entry already recorded (matched
    on the from/to window pair) is not duplicated — the log is append-only.
    """
    slip_log = current.setdefault("slip_log", [])
    seen = {(_slip_from(e), _slip_to(e)) for e in slip_log if isinstance(e, dict)}
    for entry in snap.get("slip_log") or []:
        if not isinstance(entry, dict):
            continue
        key = (_slip_from(entry), _slip_to(entry))
        if key in seen:
            continue
        slip_log.append({
            "date": entry.get("date") or date,
            "from_window": _slip_from(entry),
            "to_window": _slip_to(entry),
            "reason": entry.get("reason"),
            "source": entry.get("source"),
        })
        seen.add(key)


def _slip_from(entry: dict):
    return entry.get("from_window", entry.get("from"))


def _slip_to(entry: dict):
    return entry.get("to_window", entry.get("to"))


def _bump(state_file: dict, date: str) -> None:
    """Bump a state file's version and stamp it as a loop edit.

    Every machine write bumps the file's version and records last_edited_by:
    "loop", keeping loop edits distinguishable from owner edits ([03] clause 3) —
    the field an owner edit resets to distinguish theirs.
    """
    state_file["version"] = (state_file.get("version") or 0) + 1
    state_file["last_edited_by"] = "loop"


# ---------------------------------------------------------------------------
# Step 5 — the single git commit
# ---------------------------------------------------------------------------


def git_commit_run(root: Path, run_id: str, paths, *, message: str, runner=subprocess.run) -> bool:
    """Stage the run's artifacts and commit them as one reviewable diff.

    The commit is the review trail that replaces a human approval step: the issue,
    the regenerated manifest, the edited state files, and the run's findings +
    draft (evidence the spec retains) land as one diff citing the run_id.

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


# ---------------------------------------------------------------------------
# The recipe
# ---------------------------------------------------------------------------


def run_publish_stage(
    root: Path,
    *,
    draft: dict,
    state: State,
    run_id: str,
    now: datetime,
    runner=subprocess.run,
) -> PublishResult:
    """Run stage 6's five ordered steps and return where the issue landed.

    `draft` is the validated issue from stage 4 — validator_report already
    stamped, stats still {}. This derives stats, stamps the orchestrator-owned run
    fields, writes the immutable issue, regenerates the manifest, applies the
    state edits, and commits once. May raise PublishedIssueExists if the date
    already holds a published issue (immutability); every other exception is the
    caller's to turn into a publish stub.
    """
    issues_dir = root / "issues"

    stats = derive_full_stats(draft, issues_dir)
    stamp_run_fields(draft, stats)

    issue_path = write_issue(root, draft)
    manifest_path = regenerate_manifest(issues_dir, generated_at=now.isoformat())
    state_paths = apply_state_edits(root, draft, state, run_id, now)

    issue_id = draft["issue"]["id"]
    status = draft["issue"]["run"]["status"]
    message = f"run {run_id}: publish {issue_id} ({status})"
    committed = git_commit_run(
        root,
        run_id,
        [issue_path, manifest_path, *state_paths, root / "runs" / run_id],
        message=message,
        runner=runner,
    )

    return PublishResult(
        issue_path=issue_path,
        manifest_path=manifest_path,
        status=status,
        stats=stats,
        state_paths=tuple(state_paths),
        committed=committed,
    )
