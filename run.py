#!/usr/bin/env python3
"""ResearchSwarm orchestrator — the thin recipe-follower.

The OS scheduler fires this at 07:00 local, every day, forever. It reads
config/cadence.toml, asks "is today a run day?", and exits in milliseconds if
not. A skipped day is a no-op, not a run: no issue, no stub, no dashboard
entry, no trace.

This file decides only things a script can decide with certainty. Every
judgment call belongs to a model (the manager interprets, the critic judges) or
to a human (the owner seeds stances). If you find yourself writing an `if` that
weighs significance, it belongs in a prompt.

Two stage machines run side by side while the pivot lands. `run.py --program <id>`
selects the **v2** per-program detective (`_main_v2`); without it, the **v1**
market digest below runs unchanged. The dispatch is one `if` in main(), and v1 is
deleted whole as its own ticket — no stage branches internally on schema version.

Stages, and where each lands:
  0. gate       — this ticket
  1. prepare    — this ticket
  2. research   — build 02/03
  3. synthesize — build 04
  4. validate   — build 05
  5. critique   — build 07/08
  6. publish    — build 06

Spec: SPEC.md, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

from researchswarm.apertures import (
    cap_receipt,
    company_ids_from_entities,
    company_ids_from_holders,
    plan_apertures,
    plan_dossier_scans,
)
from researchswarm.beats import load_beats
from researchswarm.cadence import (
    is_run_day,
    is_run_day_v2,
    load_cadence,
    next_due_date_v2,
    program_surge_v2,
)
from researchswarm.calendar import (
    load_calendar,
    resolve_surge,
    runs_since_verified,
    stale_reason,
    verify_calendar,
    write_verified_dates,
)
from researchswarm.critique import run_critique_stage, run_critique_stage_v2
from researchswarm.dossiers import load_company_dossiers
from researchswarm.manager import ManagerFailed, load_models
from researchswarm.programs import (
    load_edges,
    load_entities,
    load_interests,
    load_program,
    program_roster,
)
from researchswarm.prompts import RunContext, load_template, render_dossier_prompt
from researchswarm.publish import (
    derive_full_stats,
    git_commit_run,
    PublishResultV2,
    publish_stub,
    run_publish_stage,
    run_publish_stage_v2,
    stamp_run_fields,
)
from researchswarm.research import (
    ResearchStageV2,
    persist_findings_v2,
    render_all_prompts,
    render_all_prompts_v2,
    run_research_stage,
    run_research_stage_v2,
)
from researchswarm.researcher import ResearcherFailed, run_researcher_v2
from researchswarm.runs import (
    LOOKBACK_FLOOR,
    latest_covering_issue,
    resolve_coverage_window,
    resolve_prior_quiet,
    resolve_run_id,
)
from researchswarm.state import _load_json as load_state_json, check_entity_refs, load_state
from researchswarm.state_edits import apply_dossier_edits_v2, apply_state_edits_v2
from researchswarm.stub import failed_stub_v2, PublishedIssueExists
from researchswarm.synthesis import (
    RESEARCHER_MODEL_DEFAULT,
    IssueIdentity,
    run_synthesis_stage,
    run_synthesis_stage_v2,
)
from researchswarm.validation import (
    ValidationExhausted,
    run_validation_stage,
    run_validation_stage_v2,
    state_view_v2,
)
from researchswarm.validator import CALENDAR_STALE_MARKER

REPO_ROOT = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CONFIG_ERROR = 2

log = logging.getLogger("researchswarm")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one ResearchSwarm cycle.")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="Fake the date (YYYY-MM-DD). Cadence is testable by faking the date "
        "rather than waiting a week.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if today is not a run day. Does not fake the date.",
    )
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="Repo root (defaults to this file's dir)."
    )
    parser.add_argument(
        "--beats",
        default=None,
        help="Comma-separated beat ids to run (default: all). An operator's "
        "scalpel — rerun one dead beat without paying for the other five.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render prompts and stop. Calls no models, writes nothing.",
    )
    parser.add_argument(
        "--program",
        default=None,
        help="Run the v2 per-program detective for this program id (a file in "
        "config/programs/). Selects the v2 stage machine; without it run.py runs "
        "the v1 market digest. Adding a program is one config file.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Spec/02's third trigger: a single, manual, out-of-cadence run for a "
        "named program. Bypasses the cadence gate and changes nothing else — same "
        "apertures, same gates, same rubric, a normal dated issue. Requires "
        "--program; it is a per-program trigger, not a global one.",
    )
    parser.add_argument(
        "--reuse-findings",
        metavar="RUN_ID",
        default=None,
        help="Reuse a previous run's researcher findings instead of paying for the "
        "fan-out again (e.g. run_20260720_1102). The research is the expensive "
        "part and it is already on disk under runs/<RUN_ID>/findings/; a run that "
        "died in the manager or the validator should not re-buy it. Requires "
        "--program.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def _config_error(exc: Exception) -> int:
    """Config and state problems all die the same way: say what broke, exit 2."""
    log.error("%s", exc)
    return EXIT_CONFIG_ERROR


def _fail_run(root, *, run_id, now, window, stage, detail, thesis_version, beats_failed) -> int:
    """Publish a failed-run stub and return EXIT_RUN_FAILED — the one failure path.

    Every stage that can die (research, synthesis, validation, publish) ends here,
    so a stub always reaches the manifest and the commit at write time, not
    whenever the next successful run regenerates them (publish_stub owns that
    tail). A stub that overlaps a day already published raises PublishedIssueExists
    — the real issue is immutable, so the rerun reports its failure without
    touching it.
    """
    try:
        path = publish_stub(
            root,
            run_id=run_id,
            now=now,
            window=window,
            stage=stage,
            detail=detail,
            thesis_version=thesis_version,
            beats_failed=beats_failed,
        )
    except PublishedIssueExists as exc:
        log.error("%s", exc)
        return EXIT_RUN_FAILED
    log.error("run failed at %s — published failed-run stub %s", stage, path.relative_to(root))
    return EXIT_RUN_FAILED


def _resolve_surge_and_staleness(cadence, calendar, today, issues_dir):
    """Resolve surge and calendar staleness together — one place, one rule.

    A stale calendar DISABLES surge (spec/02 staleness table): otherwise a rotted
    calendar whose previously-verified window still contains today would surge
    daily while every issue printed "surge disabled", the marker lying about the
    behaviour. So staleness is computed first and gates surge — the two can never
    disagree. When [surge] is absent from cadence.toml the whole feature is off:
    no surge, no staleness, no shadow defaults. Returns (surge, calendar_stale,
    reason).
    """
    if cadence.surge is None:
        return None, False, None
    reason = stale_reason(
        calendar,
        today=today,
        cycles_since_verified=runs_since_verified(issues_dir, calendar),
        stale_after_cycles=cadence.surge.stale_after_cycles,
    )
    if reason is not None:
        return None, True, reason
    return resolve_surge(calendar, cadence.surge, today), False, None


# ---------------------------------------------------------------------------
# The v2 stage machine — one program detective per run (spec/09)
# ---------------------------------------------------------------------------

# ⚑ KNOWN GAP, wired honestly rather than papered over. Spec/02 fires a surge for
# "any program with a competitor in that window", but NOTHING in the system
# currently defines the attendee set: config/calendar.toml carries a window's
# name/dates/source and no competitor field, and no state file maps entities to
# conference windows. cadence.program_surge_v2 therefore takes the attendee set as
# a CALLER-SUPPLIED parameter, and this is that caller — so until the source of
# that set is decided (a calendar field? an entity record field? a per-run scan
# finding?), the honest value is the empty set and a program surge can never fire.
# The seam is real and the intersection is live code; only the data is missing.
# Inventing an attendee source here would make surge fire on a guess, which is
# precisely what spec/02's require_verified_dates guard exists to forbid.
COMPETITORS_IN_WINDOW_V2: frozenset[str] = frozenset()

# ⚑ The researcher model for the v2 aperture fan-out. models.toml grew a
# `researchers` key with the pivot (v1 read a per-BEAT model from beats.toml, and
# beats.toml is gone), and the fallback matches synthesis.run_synthesis_stage_v2's
# so the id INVOKED and the id RECORDED in the issue's models block are one value.
RESEARCHER_MODEL_DEFAULT_V2 = RESEARCHER_MODEL_DEFAULT

# Every model role `_main_v2` later reads by SUBSCRIPT rather than `.get()`. The
# Stage-0 gate proves all of them present before a single token is spent, because
# the alternative is what the review found: `models_config["manager"]` is not read
# until stages 4 and 5, so a models.toml missing that role sailed through the
# config gate and surfaced as a raw KeyError traceback mid-run — after the
# research fan-out had already been paid for, and in the voice of a crash rather
# than the clean `_config_error` every other config problem gets.
#
# The roles read with a DEFAULT (`researchers`) or handled inline (`verifier`,
# whose absence degrades calendar verification rather than failing the run) are
# deliberately not here: a role with a fallback has nothing to gate on.
REQUIRED_MODEL_ROLES_V2 = ("manager", "critic")

# ⚑ KNOWN GAP, wired the same honest way as COMPETITORS_IN_WINDOW_V2 above. Spec
# #92 lists a MATERIAL EVENT as the third dossier trigger — "an acquisition or a
# discontinuation must not wait for the dial" — and `apertures.plan_dossier_scans`
# takes that set as a CALLER-SUPPLIED parameter, so the seam is live code. What is
# missing is the SOURCE: nothing in config or state marks an entity as having had
# a material event this cycle, and deriving one from the previous issue's prose
# would be a judgement call, which by this file's own rule belongs in a prompt and
# not in an `if` here. So the honest value is the empty set: the other two triggers
# (first sighting, the quarterly dial) still fire, and no dossier is refreshed on a
# guess. The day an event source is decided, this constant is what changes.
MATERIAL_EVENT_IDS_V2: frozenset[str] = frozenset()

# The dossier scan's own prompt document. A FOURTH template beside researcher-v2:
# the dossier aperture is window-exempt, and the shared researcher template
# hard-codes the coverage window into its output contract, so a scope block could
# not repeal it (prompts/dossier-scan.md states this in full).
DOSSIER_TEMPLATE_V2 = "dossier-scan.md"


def _require_model_roles_v2(models_config: dict) -> None:
    """Every subscripted model role is present at Stage 0, or the run never starts.

    Fails in the config voice (`ValueError` → `_config_error` → exit 2), naming
    the role and the file, so the operator is told what to add rather than shown
    a KeyError from four stages away (spec/09 stage 0: the gate reads everything
    it needs from config, so a bad config costs milliseconds, not a fan-out).
    """
    missing = [role for role in REQUIRED_MODEL_ROLES_V2 if not models_config.get(role)]
    if missing:
        raise ValueError(
            "config/models.toml: [models]."
            + ", [models].".join(missing)
            + f" {'is' if len(missing) == 1 else 'are'} required"
        )


def _fail_run_v2(
    stage: str,
    detail: str,
    *,
    root: Path | None = None,
    program=None,
    run_id: str | None = None,
    now: datetime | None = None,
    window: dict | None = None,
    thesis_version: int | None = None,
    interest_list_version: int | None = None,
    apertures_degraded=(),
) -> int:
    """A v2 run that died: publish its stub, say where it died, exit non-zero.

    A stub is a run OUTCOME, not the absence of one (spec/09 failure handling),
    and in v2 it is an EMITTED per-program issue: `stub.failed_stub_v2` builds the
    v2.0.0 shape with the `program` block, and it rides the SAME publisher
    (`run_publish_stage_v2`) a successful run rides. That is what puts it at
    `issues/<program_id>/<date>.json`, in the program manifest, and in the
    registry — "stubs appear" in the dropdown (spec/08), with no special case
    anywhere downstream, because the registry is already a wholesale config ⋈ disk
    join (#83).

    Then it commits, for the same reason the successful path does: one commit per
    run, citing the run_id, so a failed cycle leaves a review trail instead of an
    untracked file. A failed commit is not a failed run — the stub is on disk.

    **Every write here is best-effort and the exit code never depends on it.** A
    failure path that raises is strictly worse than one that misses: it would
    replace a precise "died at synthesis" with a traceback from the stub writer,
    which is a lie about what broke. So a date that already holds a PUBLISHED
    issue (immutability, spec/08 — a forced rerun of a day that already shipped)
    and any other write error are both logged and swallowed. The context arguments
    are optional for the same reason: a failure before the run has a program or a
    window still gets a loud log rather than a TypeError.
    """
    log.error("run failed at %s — %s", stage, detail)
    if program is None or root is None or run_id is None or now is None or window is None:
        log.error("no stub written: the run died before it had a program and a window to stub")
        return EXIT_RUN_FAILED

    try:
        stub = failed_stub_v2(
            program=program,
            issue_id=now.date().isoformat(),
            run_id=run_id,
            now=now,
            window=window,
            stage=stage,
            detail=detail,
            thesis_version=thesis_version,
            interest_list_version=interest_list_version,
            apertures_degraded=list(apertures_degraded),
        )
        published = run_publish_stage_v2(
            root, issue=stub, program_id=program.id, run_id=run_id, now=now
        )
    except PublishedIssueExists as exc:
        log.error("no stub written: %s", exc)
        return EXIT_RUN_FAILED
    except Exception as exc:  # noqa: BLE001 — the failure path never raises its own
        log.error("stub could not be written (%s) — the failure stands on the exit code", exc)
        return EXIT_RUN_FAILED

    log.info(
        "stub published: %s",
        ", ".join(str(Path(p).relative_to(root)) for p in published.paths),
    )
    git_commit_run(
        root,
        run_id,
        published.paths,
        message=f"run {run_id}: {program.id} stub {now.date().isoformat()} (failed at {stage})",
    )
    return EXIT_RUN_FAILED


def _published_paths_v2(published) -> tuple[Path, ...]:
    """Normalise whatever came back through the publisher seam into paths to stage.

    The wired publisher returns a `PublishResultV2` (three files). The seam
    contract, though, is documented as "returns the path it wrote (or None)", and
    an injected double is entitled to honour exactly that — a test publisher that
    writes one file and returns its path must not have to know about a result
    object. So both are accepted: the seam keeps its narrow published contract
    while the real implementation can hand back everything it wrote.
    """
    if published is None:
        return ()
    if isinstance(published, PublishResultV2):
        return published.paths
    return (Path(published),)


def _default_publisher_v2(now: datetime):
    """The wired publisher: `publish.run_publish_stage_v2`, bound to this run's clock.

    The seam stays a seam — `publisher=` still overrides — but its DEFAULT is now
    the real emitter rather than a warning. #83 decided the three shapes the seam
    was blocked on (spec/08 "The program registry", "The issue manifest", "The
    data layer"), so there is nothing left to invent and the run publishes.

    The closure exists only to carry `now` across the seam's fixed signature
    (`issue`, `program_id`, `root`, `run_id`). Threading the run's own clock is
    what makes the issue, its manifest and the registry agree on when the run
    happened, instead of three near-simultaneous `datetime.now()` calls.

    `publish.py` is still called BY run.py and never calls git itself: the single
    commit below stays run.py's, so the sole-writer invariant holds (spec/08).
    """

    def publisher(*, issue, program_id, root, run_id):
        return run_publish_stage_v2(
            root, issue=issue, program_id=program_id, run_id=run_id, now=now
        )

    return publisher


# ---------------------------------------------------------------------------
# The fourth aperture kind — dossier scans (spec #92)
# ---------------------------------------------------------------------------
#
# Everything below is SUBORDINATE to the cycle's intelligence. A dossier is
# background gathering: it answers "who is this company", not "what moved this
# window", and spec #92 is explicit that a failed, capped or dormant dossier scan
# DEGRADES the run rather than failing it. So every function here is total — the
# worst outcome any of them may produce is "no dossier this cycle", never an
# exception. A crash in this path would kill a run whose real product (the issue)
# was already gathered, and in the tail of the machine it would kill it AFTER
# publishing. That bug has shipped five times in this repo.


def _verify_calendar_v2(
    root: Path, *, cadence, calendar, models_config, run_id: str, now: datetime, dry_run: bool
):
    """Re-verify the conference windows against their sources. Returns the calendar.

    THE STEP THE v1->v2 RE-ROOT LEFT BEHIND. `_main_v2` resolved surge and
    staleness from the calendar but never verified it, and a window is only
    `verified` once a run has resolved its dates against the society's own page.
    So on the v2 path the calendar could never become fresh: every run warned
    "calendar stale — surge disabled", every run was right, and no run could ever
    fix it. A permanently-disabled surge is exactly the silent failure the
    staleness marker exists to prevent, wearing the marker as a disguise.

    Identical rules to v1's copy, deliberately: the verifier PROPOSES and the
    orchestrator decides (`calendar._accept_dates`), so a date that was not read
    from `source` is never written. A verifier failure never crashes the run —
    background maintenance is subordinate to the cycle's intelligence.

    The write lands as its own commit, before the run knows its own outcome,
    because a verified date is worth keeping even if this cycle later stubs.
    """
    if dry_run or cadence.surge is None:
        return calendar
    verification = verify_calendar(
        calendar, model=models_config["verifier"], max_surge_days=cadence.surge.max_surge_days
    )
    if not verification.updated:
        return calendar
    calendar_path = root / "config" / "calendar.toml"
    dates = {
        v.window_id: {"starts": v.starts, "ends": v.ends}
        for v in verification.windows
        if v.verified
    }
    if write_verified_dates(calendar_path, now.isoformat(timespec="seconds"), dates):
        cited = ", ".join(f"{wid} ({verification.sources[wid]})" for wid in verification.updated)
        log.info("calendar: verified %s", cited)
        git_commit_run(
            root, run_id, [calendar_path], message=f"run {run_id}: verify calendar — {cited}"
        )
    return load_calendar(calendar_path)


def _reuse_findings_v2(root: Path, source_run_id: str, apertures) -> ResearchStageV2:
    """Rebuild stage 2 from a previous run's findings on disk. No model calls.

    WHY THIS EXISTS. The fan-out is ~90% of a cycle's cost and it is the part
    LEAST likely to be wrong: a researcher that returns an honest "nothing in
    this window" has done its job perfectly. Yet a run that then dies in the
    manager or the validator threw all of it away, so every iteration on a
    downstream contract re-bought research that had not changed. Two live cycles
    paid roughly $9 to re-answer the same question about the same 48 hours.

    The findings were always persisted (`runs/<run_id>/findings/<aperture>.json`,
    written as each lands so a crash keeps what completed). Nothing ever read
    them back. This does.

    The aperture ROSTER still comes from the current plan, not from the reused
    run: dormancy and the degradation register are properties of THIS cycle's
    config, and inheriting them would let a stale roster masquerade as today's.
    Only the findings themselves are reused. An aperture with no file on disk is
    reported degraded rather than silently dropped — the same status a failed
    scan gets, because from the manager's side they are the same fact.
    """
    findings_dir = Path(root) / "runs" / source_run_id / "findings"
    if not findings_dir.is_dir():
        raise FileNotFoundError(f"no findings to reuse at {findings_dir}")

    findings_by_aperture: dict[str, dict] = {}
    apertures_run: list[dict] = []
    degraded: list[str] = []
    for aperture in apertures:
        path = findings_dir / f"{aperture.id.replace(':', '-')}.json"
        if aperture.dormant:
            apertures_run.append({"aperture": aperture.kind, "scope": aperture.scope, "status": "dormant"})
            degraded.append(aperture.id)
            continue
        try:
            findings_by_aperture[aperture.id] = json.loads(path.read_text())
            apertures_run.append({"aperture": aperture.kind, "scope": aperture.scope, "status": "ok"})
        except (OSError, ValueError) as exc:
            log.warning("reuse: %s has no usable findings (%s) — degraded", aperture.id, exc)
            apertures_run.append({"aperture": aperture.kind, "scope": aperture.scope, "status": "failed"})
            degraded.append(aperture.id)
    log.info(
        "reusing findings from %s: %d aperture(s) restored, %d degraded — no researcher was called",
        source_run_id, len(findings_by_aperture), len(degraded),
    )
    return ResearchStageV2(
        apertures_run=apertures_run, apertures_degraded=degraded,
        findings_by_aperture=findings_by_aperture,
    )


def _plan_dossier_scans_v2(root: Path, entities, today: date) -> tuple[list, int]:
    """Which companies need a dossier scan this run — `(apertures, candidates)`.

    The candidate COUNT comes back alongside the apertures because an empty
    result has two very different meanings and the caller must be able to tell
    them apart. "Every company we know is inside the refresh dial" is the dial
    working; "we know no companies at all" is the aperture being unreachable.
    Reporting the second as the first is a gate announcing itself clean because
    it had nothing to read — the same shape as a treatment landscape publishing
    zero rivals because the scan never ran.

    Two decisions live here and nowhere else:

    * **The candidate set comes from the shared entity layer**
      (`company_ids_from_entities`), which is how DISCOVERY FEEDS THE ROSTER
      (spec #92 story 40). A competitor first sighted in cycle N is written into
      `state/entities/` by that cycle's state edits; cycle N+1's planner therefore
      sees a company with no dossier and queues its first scan, so the roster
      deepens automatically as it widens rather than being bounded by what a human
      remembered to seed. No hand-maintained queue file is needed for that, and
      inventing one would put a second source of truth beside the entity layer.
    * **The triggers are the planner's**, not this file's: first sighting, a
      material event, then the ⚑ quarterly dial, in that precedence order. run.py
      supplies the inputs (what we hold, what day it is) and asks.

    The prior records are read from disk here rather than passed in because they
    are the planner's INPUT, and reading them is cheap: a run with no dossiers on
    disk reads an absent directory and gets `{}`.
    """
    try:
        held = load_company_dossiers(root)
        # `extra` is the DISCOVERY path, and without it the aperture is
        # unreachable: the roster is asset-typed, so a layer holding only assets
        # yields no companies and no cycle can ever plan a first scan. The
        # holders those assets name are the companies we have actually sighted.
        company_ids = company_ids_from_entities(
            entities, extra=company_ids_from_holders(entities)
        )
        apertures = plan_dossier_scans(
            company_ids,
            dossiers=held,
            today=today,
            material_events=MATERIAL_EVENT_IDS_V2,
        )
        return apertures, len(company_ids)
    except Exception as exc:  # noqa: BLE001 — background gathering never fails the run
        log.warning("dossier planning failed (%s) — no dossier scans this cycle", exc)
        return [], 0


def _run_one_dossier_scan_v2(
    aperture, template, ctx, root, *, program_id, entities, dossiers, known_entity_ids,
    model, as_of: str, runner,
) -> dict:
    """Scan one company's history. Returns its findings envelope, or raises ResearcherFailed.

    The aperture is asked for its own properties rather than tested for its kind
    (spec #92: the window exemption "must be explicit in the aperture's own
    definition rather than a special case"). Hence `aperture.window_bounded`
    deciding what window the researcher echoes: a dossier scan hands `None`,
    because its subject is history and a window would truncate a founding story
    to nothing — the same rule a seven-day window recently used to discard a
    $1.1B acquisition.

    Identity is seeded from the prior record first and the entity record second,
    which is the propagation contract applied to identity: an established,
    provenanced name outranks the one a discovery finding happened to spell.
    Deliberately NOT seeded from the program: a dossier is shared across programs,
    so program-relative steering must not reach its prompt.
    """
    entity_id = aperture.scope
    prompt = render_dossier_prompt(
        template,
        aperture,
        program_id=program_id,
        dossier=dossiers.get(entity_id),
        candidate=entities.get(entity_id) if isinstance(entities, dict) else None,
        as_of=as_of,
        ctx=ctx,
    )
    result = run_researcher_v2(
        aperture,
        prompt,
        model=model,
        program_id=program_id,
        run_id=ctx.run_id,
        window=ctx.window if aperture.window_bounded else None,
        known_entity_ids=known_entity_ids,
        runner=runner,
    )
    findings = result.findings

    # The cost cap, turned into a receipt rather than a silent truncation. The
    # receipt rides on the envelope's `errors[]`, which is exactly where
    # `state_edits._dossier_degradation_v2` looks for the record's
    # `coverage.degradation` — so a capped scan lands on the page as UNMEASURED
    # rather than reading as a small company.
    #
    # The spend comes from the TRANSPORT ENVELOPE (`ResearcherResult`), never from
    # the model's payload. Spec/06 admission test 2: a degradation is only
    # exempt-able when its trigger is mechanically detectable from facts the
    # orchestrator holds, and a scan self-reporting the overrun it just committed
    # is not such a fact. `num_turns`/`total_cost_usd` are ours; we parsed them.
    receipt = cap_receipt(aperture, {"turns": result.num_turns, "usd": result.cost_usd})
    if receipt and isinstance(findings, dict):
        errors = findings.get("errors")
        findings["errors"] = (errors if isinstance(errors, list) else []) + [
            f"{receipt['degradation']}: exceeded {', '.join(receipt['exceeded'])} "
            f"(cap {receipt['cap']}, spend {receipt['spend']})"
        ]
        log.warning("%s: cost cap exceeded — %s", aperture.id, receipt["exceeded"])

    persist_findings_v2(root, ctx.run_id, aperture.id, findings)
    return findings


def _run_dossier_scans_v2(
    dossier_apertures, template, ctx, root, *, program_id, entities, known_entity_ids,
    model, as_of: str, runner,
):
    """Fan out the dossier scans beside the cycle's apertures. `(corpus, degraded)`.

    Threads, and one per scan, for the same reason the cycle fan-out uses them: a
    researcher is a subprocess blocked on the network for minutes. The corpus is
    rebuilt in ROSTER order afterwards, never completion order, so the run's
    artifacts are deterministic.

    The corpus is kept SEPARATE from `stage.findings_by_aperture` on purpose. That
    corpus is rendered into the manager's prompt and handed to the critic, and
    spec #92 puts authorship of dossier facts out of scope for this work ("no
    read-through is authored from the dossier"). Feeding it in would invite the
    manager to interpret a shared record inside one program's issue, which is the
    exact leak the facts-only rule exists to prevent. The dossier corpus goes one
    place: the state-edit path, which writes the shared store.

    A scan that RETURNED NOTHING is in the corpus with `quiet: true`; a scan that
    DID NOT RUN is in `degraded` and absent from the corpus (story 38). The two
    are never confused, and both are logged in their own voice.
    """
    corpus: dict[str, dict] = {}
    degraded: list[str] = []
    if not dossier_apertures:
        return corpus, degraded

    dossiers = load_company_dossiers(root)
    with ThreadPoolExecutor(max_workers=len(dossier_apertures)) as pool:
        futures = {
            pool.submit(
                _run_one_dossier_scan_v2,
                aperture, template, ctx, root,
                program_id=program_id,
                entities=entities,
                dossiers=dossiers,
                known_entity_ids=known_entity_ids,
                model=model,
                as_of=as_of,
                runner=runner,
            ): aperture
            for aperture in dossier_apertures
        }
        for future in as_completed(futures):
            aperture = futures[future]
            try:
                corpus[aperture.id] = future.result()
            except ResearcherFailed as exc:
                log.warning("%s: DOSSIER SCAN FAILED — %s", aperture.id, exc)
                degraded.append(aperture.id)
            except Exception as exc:  # noqa: BLE001 — a dossier never fails the run
                log.warning("%s: DOSSIER SCAN ERRORED — %s", aperture.id, exc)
                degraded.append(aperture.id)

    ordered = {a.id: corpus[a.id] for a in dossier_apertures if a.id in corpus}
    for aperture_id, findings in ordered.items():
        log.info(
            "%s: %s",
            aperture_id,
            "scanned, nothing to record (quiet)"
            if findings.get("quiet")
            else f"dossier returned, {len(findings.get('findings') or [])} datable finding(s)",
        )
    if degraded:
        log.warning(
            "dossier scans degraded: %s — the cycle continues without them",
            ", ".join(degraded),
        )
    return ordered, degraded


def _main_v2(args, *, root: Path, now: datetime, publisher=None, runner=None) -> int:
    """Stages 0-6 for one program detective. The v2 twin of `main`'s body.

    Additive beside v1 and dispatch-free inside each stage, exactly as every other
    v2 twin on this branch (research, synthesis, critique, validation, state
    edits): `--program` chooses this whole function, so no stage has to ask which
    schema it is serving. v1 is deleted whole, as its own ticket.

    `publisher` is stage 6's EMISSION seam, called as
    `publisher(issue=..., program_id=..., root=..., run_id=...)`. It now DEFAULTS
    to the real emitter (`publish.run_publish_stage_v2`) rather than to a warning:
    #83 decided the issue path, the per-program manifest and the cross-program
    registry (spec/08), so there is nothing left to invent. The seam survives its
    unblocking — an injected publisher still overrides — because that is what lets
    the whole spine be tested without writing files.
    """
    today = now.date()
    config_dir = root / "config"
    state_dir = root / "state"

    # --- Stage 0: the gate -------------------------------------------------
    # Same discipline as v1: everything the gate needs is read from config and
    # from the issues already on disk, so a no-op day exits in milliseconds
    # without a network call, a model call, or a trace.
    try:
        program = load_program(config_dir, args.program)
        interests = load_interests(config_dir)
        models_config = load_models(config_dir / "models.toml")
        _require_model_roles_v2(models_config)
        cadence = load_cadence(config_dir / "cadence.toml")
        calendar = load_calendar(config_dir / "calendar.toml")
        edges = load_edges(state_dir, program.id)
        entities = load_entities(state_dir)
        thesis = load_state_json(state_dir / "thesis.json")
        catalyst_queue = load_state_json(
            state_dir / "programs" / program.id / "catalyst-queue.json"
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return _config_error(exc)

    issues_dir = root / "issues" / program.id
    roster = program_roster(program, edges)
    log.info(
        "program %s: %d typed edge(s), %d roster entit%s, %d shared entity record(s), "
        "interest list v%d%s",
        program.id,
        len(edges),
        len(roster),
        "y" if len(roster) == 1 else "ies",
        len(entities),
        interests.version,
        " (STALE)" if interests.is_stale(today) else "",
    )

    # Surge, in two steps that must not be collapsed. Step one is the WINDOW: is a
    # verified, fresh conference window live today (and is the calendar itself
    # trustworthy)? That is global and reuses v1's resolver, guards and staleness
    # rule unchanged. Step two is the PROGRAM: does that window hold one of THIS
    # program's competitors? An ASH window is a real surge for a heme program and a
    # dead week for HMBD-001 (spec/02).
    # Verify BEFORE resolving, and before the cadence gate, so a window verified
    # today can surge today rather than one cycle late. `resolve_run_id` is a pure
    # function of `now`, so calling it here and again below yields the same id.
    calendar = _verify_calendar_v2(
        root,
        cadence=cadence,
        calendar=calendar,
        models_config=models_config,
        run_id=resolve_run_id(now),
        now=now,
        dry_run=getattr(args, "dry_run", False),
    )
    window_surge, calendar_stale, stale_detail = _resolve_surge_and_staleness(
        cadence, calendar, today, issues_dir
    )
    surge = program_surge_v2(window_surge, roster, COMPETITORS_IN_WINDOW_V2)
    if window_surge is not None and surge is None:
        log.info(
            "conference window %s is live, but the attendee set is undefined "
            "(calendar.toml has no competitor field and no state file maps entities "
            "to windows) — a program surge CANNOT fire until that source is decided",
            window_surge.window,
        )
    if calendar_stale:
        log.warning("%s (%s)", CALENDAR_STALE_MARKER, stale_detail)

    # The interval is measured from the last issue that actually COVERED days,
    # walking past stubs — the same join the coverage window uses. A stubbed cycle
    # therefore leaves the program still due, and the next run widens its window
    # over the missed days rather than dropping them (spec/02, spec/09).
    previous = latest_covering_issue(issues_dir).payload
    last_issue_date = None
    if previous is not None:
        try:
            last_issue_date = date.fromisoformat(previous["issue"]["id"])
        except (KeyError, TypeError, ValueError):
            log.warning("previous issue carries no parseable id — treating as a cold start")

    try:
        decision = is_run_day_v2(
            program,
            today,
            last_issue_date=last_issue_date,
            surge_state=surge,
            push=args.push,
        )
    except ValueError as exc:  # an unknown baseline is a config typo, not a no-op
        return _config_error(exc)

    if not decision.run and not args.force:
        due = "run #1" if last_issue_date is None else next_due_date_v2(
            last_issue_date, decision.cadence
        )
        log.info(
            "%s is not a run day for %s (%s cadence, %s) — next due %s — no-op",
            today, program.id, decision.cadence, decision.reason, due,
        )
        return EXIT_OK
    log.info(
        "%s is a run day for %s: %s (%s cadence)",
        today, program.id, decision.reason, decision.cadence,
    )

    # --- Stage 1: prepare --------------------------------------------------
    run_id = resolve_run_id(now)
    log.info("run_id=%s today=%s program=%s", run_id, today, program.id)

    window = resolve_coverage_window(
        issues_dir, today=today, cold_start_days=program.cold_start_lookback_days
    )
    if window.previous_issue:
        log.info("coverage %s → %s (joins %s)", window.from_, window.to, window.previous_issue)
    elif window.baseline_expired:
        log.warning(
            "no issue carrying a coverage window within the last %d — cold-start window %s → %s",
            LOOKBACK_FLOOR, window.from_, window.to,
        )
    else:
        log.info("no previous issue — run #1, cold-start window %s → %s", window.from_, window.to)

    # NOT WIRED, and deliberately not faked: the registry watch (spec/09 stage 1 —
    # poll ClinicalTrials.gov v2 by lastUpdatePostDate for the program's tracked NCT
    # set and hand the diff to the biology/arena apertures). No NCT set exists in
    # config or state to poll, and no module implements the poll. Its absence
    # narrows what the apertures see; it does not make the run dishonest.
    log.info("registry watch not wired — apertures scan without a registry diff")

    apertures = plan_apertures(program)
    active = [a for a in apertures if a.active]
    log.info(
        "apertures: %d planned, %d active (%s)",
        len(apertures), len(active), ", ".join(a.id for a in active),
    )

    # The fourth kind joins the fan-out (spec #92). Planned SEPARATELY because
    # `plan_apertures` is a total function of CONFIG and stays `1 + N + 1`, while a
    # dossier scan is a function of STATE: which companies we know, how old their
    # records are, what just happened to them. Most cycles plan none, which is the
    # slow dial working rather than a failure.
    dossier_apertures, dossier_candidates = _plan_dossier_scans_v2(root, entities, today)
    if dossier_apertures:
        log.info(
            "dossier scans: %d planned (%s)",
            len(dossier_apertures),
            ", ".join(f"{a.scope} ({a.trigger})" for a in dossier_apertures),
        )
    elif dossier_candidates:
        log.info(
            "dossier scans: none due — all %d company record(s) are inside the refresh dial",
            dossier_candidates,
        )
    else:
        # NOT the same as "none due", and the difference is the whole aperture.
        # No company records exist, so the planner ranged over an empty set and
        # every trigger was vacuously satisfied. Warn: the fourth aperture cannot
        # fire at all until something writes a company into `state/entities/`.
        log.warning(
            "dossier scans: no company records exist — the dossier aperture has "
            "nothing to scan (the roster is asset-typed; no asset names a holder)"
        )

    ctx = RunContext(
        run_id=run_id,
        coverage_window_from=window.from_.isoformat(),
        coverage_window_to=window.to.isoformat(),
        surge=surge,
    )

    # Everything a stub needs to be an emitted v2 issue, assembled once, here,
    # where the run has all of it — so the three failure exits below cannot
    # disagree about the stub's identity, its window, or which state versions
    # were in force when it died.
    stub_ctx = {
        "root": root,
        "program": program,
        "run_id": run_id,
        "now": now,
        "window": {"from": ctx.coverage_window_from, "to": ctx.coverage_window_to},
        "thesis_version": thesis.get("version"),
        "interest_list_version": interests.version,
    }

    try:
        researcher_template = load_template(root / "prompts" / "researcher-v2.md")
        manager_template = load_template(root / "prompts" / "manager-v2.md")
        retry_template = load_template(root / "prompts" / "manager-retry.md")
        critic_template = load_template(root / "prompts" / "critic-v2.md")
        critic_retry_template = load_template(root / "prompts" / "critic-retry.md")
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    # The dossier template is loaded SOFTLY, unlike the five above. A missing
    # manager template is a broken run; a missing dossier template costs the run
    # its background gathering and nothing else, and #92 is explicit that
    # background gathering is subordinate to the cycle's intelligence.
    dossier_template = None
    if dossier_apertures:
        try:
            dossier_template = load_template(root / "prompts" / DOSSIER_TEMPLATE_V2)
        except (FileNotFoundError, ValueError) as exc:
            log.warning("%s — dossier scans skipped, the cycle continues", exc)
            dossier_apertures = []

    if args.dry_run:
        rendered = render_all_prompts_v2(
            active, researcher_template, program=program, interests=interests,
            edges=edges, thesis=thesis, ctx=ctx,
        )
        for aperture_id, prompt in rendered.items():
            log.info("[dry-run] %s: rendered %d chars, no placeholders left", aperture_id, len(prompt))
        held = load_company_dossiers(root)
        for aperture in dossier_apertures:
            prompt = render_dossier_prompt(
                dossier_template,
                aperture,
                program_id=program.id,
                dossier=held.get(aperture.scope),
                candidate=entities.get(aperture.scope),
                as_of=today.isoformat(),
                ctx=ctx,
            )
            log.info("[dry-run] %s: rendered %d chars, no placeholders left", aperture.id, len(prompt))
        log.info("[dry-run] complete — nothing called, nothing written")
        return EXIT_OK

    # --- Stage 2: research -------------------------------------------------
    # The known-entity set is WIDE (every shared record plus this program's
    # roster): it answers "does this entity_id resolve to anything", which is the
    # findings-contract's dangling check. The narrow roster travels separately to
    # the validator, where it is the coverage accountability set.
    known_entity_ids = set(entities) | roster
    researcher_model = models_config.get("researchers", RESEARCHER_MODEL_DEFAULT_V2)

    if args.reuse_findings:
        stage = _reuse_findings_v2(root, args.reuse_findings, apertures)
    else:
        log.info("fanning out %d researcher(s) on %s", len(active), researcher_model)
        stage = run_research_stage_v2(
            apertures, researcher_template, ctx, root,
            program=program, interests=interests, edges=edges, thesis=thesis,
            known_entity_ids=known_entity_ids, model=researcher_model,
            runner=runner or subprocess.run,
        )

    # The dossier fan-out rides beside the cycle's, on the same model and the same
    # transport, and is NEVER allowed to decide the run's fate: its corpus is
    # collected here and spent in stage 6, and every failure mode inside it is a
    # degradation. The `all_failed` check below therefore reads the CYCLE stage
    # alone — a run whose apertures all died is a stub even if a dossier landed,
    # and a run whose dossiers all died still publishes its issue.
    dossier_corpus, dossiers_degraded = _run_dossier_scans_v2(
        dossier_apertures, dossier_template, ctx, root,
        program_id=program.id, entities=entities, known_entity_ids=known_entity_ids,
        model=researcher_model, as_of=today.isoformat(), runner=runner or subprocess.run,
    )

    if stage.all_failed:
        return _fail_run_v2(
            "research",
            f"no aperture produced findings ({len(stage.apertures_degraded)} degraded) — "
            "see the run log",
            apertures_degraded=stage.apertures_degraded,
            **stub_ctx,
        )
    log.info(
        "research complete: %d aperture(s) with findings, %d degraded",
        len(stage.findings_by_aperture), len(stage.apertures_degraded),
    )
    if stage.apertures_degraded:
        log.warning("apertures_degraded: %s", ", ".join(stage.apertures_degraded))

    # --- Stage 3: synthesize ----------------------------------------------
    identity = IssueIdentity(ctx=ctx, issue_id=today.isoformat(), published_at=now.isoformat())
    try:
        result, draft_path = run_synthesis_stage_v2(
            root,
            identity=identity,
            program=program,
            interests=interests,
            apertures=apertures,
            findings_by_aperture=stage.findings_by_aperture,
            apertures_degraded=stage.apertures_degraded,
            thesis=thesis,
            catalyst_queue=catalyst_queue,
            edges=edges,
            entities=entities,
            prior_quiet=resolve_prior_quiet(issues_dir),
            models_config=models_config,
            manager_template=manager_template,
        )
    except ManagerFailed as exc:
        return _fail_run_v2(
            "synthesis", str(exc), apertures_degraded=stage.apertures_degraded, **stub_ctx
        )

    log.info(
        "manager: draft %s (%d turn(s), $%.4f, attempt %d)",
        draft_path.relative_to(root), result.num_turns, result.cost_usd, result.attempts,
    )

    # --- Stage 4: validate -------------------------------------------------
    try:
        validation = run_validation_stage_v2(
            draft=result.draft,
            draft_path=draft_path,
            state=state_view_v2(known_entity_ids, thesis, catalyst_queue),
            roster=roster,
            issues_dir=issues_dir,
            retry_template=retry_template,
            model=models_config["manager"],
            run_id=run_id,
            thesis_version=thesis.get("version"),
            calendar_stale=calendar_stale,
        )
    except (ManagerFailed, ValidationExhausted) as exc:
        return _fail_run_v2(
            "validation", str(exc), apertures_degraded=stage.apertures_degraded, **stub_ctx
        )

    log.info(
        "validation passed: %d retr%s, %d advisory finding(s)",
        validation.retries_used,
        "y" if validation.retries_used == 1 else "ies",
        len(validation.advisory),
    )

    # --- Stage 5: critique -------------------------------------------------
    # As in v1 there is deliberately NO try/except laundering a critic failure into
    # a stub: an unreachable or unparseable critic already resolves to not_run and
    # publishes with a banner, and a genuine bug in this wiring should escape loudly.
    critic = run_critique_stage_v2(
        root,
        draft=validation.draft,
        program=program,
        edges=edges,
        entities=entities,
        thesis=thesis,
        findings_by_aperture=stage.findings_by_aperture,
        run_id=run_id,
        issues_dir=issues_dir,
        critic_template=critic_template,
        retry_template=critic_retry_template,
        model=models_config["critic"],
        manager_model=models_config["manager"],
        draft_path=draft_path,
        thesis_version=thesis.get("version"),
        schema_file=root / "prompts" / "critic-output-schema-v2.json",
        surge=surge,
    )
    log.info(
        "critic: %s → %s (%d retr%s)%s",
        critic.verdict, critic.status, critic.retries_used,
        "y" if critic.retries_used == 1 else "ies",
        f" ({critic.reason})" if critic.reason else "",
    )

    # --- Stage 6: derived stats, state edits, commit -----------------------
    # Spec/09 stage 6, in its stated order — with steps 2 and 3 (write the issue,
    # regenerate the manifest) behind the publisher seam.
    issue = critic.draft
    stats = derive_full_stats(issue, issues_dir)
    stamp_run_fields(issue, stats, critic=critic, surge=surge)
    draft_path.write_text(json.dumps(issue, indent=2) + "\n")
    log.info("stats derived from the arrays: %s", stats)

    # The three files the emission writes (spec/08): the immutable issue, its
    # per-program manifest, and the wholesale-regenerated cross-program registry.
    # ALL THREE are staged below — the manifest and registry are what the
    # dashboard actually fetches, so a commit carrying only the issue would
    # publish something no reader can reach.
    published = (publisher or _default_publisher_v2(now))(
        issue=issue, program_id=program.id, root=root, run_id=run_id
    )
    artifacts = _published_paths_v2(published)
    log.info(
        "published %s",
        ", ".join(str(Path(p).relative_to(root)) for p in artifacts) or "nothing",
    )

    touched = apply_state_edits_v2(
        root, issue,
        program_id=program.id,
        entities=entities,
        edges=edges,
        thesis=thesis,
        catalyst_queue=catalyst_queue,
        run_id=run_id,
        now=now,
    )
    log.info(
        "state edits: %s",
        ", ".join(str(p.relative_to(root)) for p in touched) or "none (a quiet cycle)",
    )

    # The dossier writes go through the same state-edit path as everything else, so
    # `run.py` stays the sole writer (spec #92 story 36) and the files land in the
    # run's ONE commit below. Guarded, and the guard is the point: this is past the
    # publish line, where a crash would cost a published run its commit, and the
    # dossier is background gathering — it degrades, it never fails the cycle.
    try:
        dossier_paths = apply_dossier_edits_v2(
            root, dossier_corpus, run_id=run_id, issue_id=today.isoformat(), now=now
        )
    except Exception as exc:  # noqa: BLE001 — a dossier write never fails the run
        log.warning("dossier state edits failed (%s) — the issue is published regardless", exc)
        dossier_paths = []
    log.info(
        "dossier edits: %s%s",
        ", ".join(str(p.relative_to(root)) for p in dossier_paths)
        or ("none (nothing new about the companies we hold)" if dossier_corpus else "none scanned"),
        f" — {len(dossiers_degraded)} scan(s) degraded" if dossiers_degraded else "",
    )

    # One commit for the whole run, citing the run_id (spec/09 stage 6 step 5). A
    # failed commit is NOT a failed run: the artifacts are on disk either way.
    committed = git_commit_run(
        root,
        run_id,
        [p for p in [*touched, *dossier_paths, *artifacts] if p is not None],
        message=f"run {run_id}: {program.id} {today.isoformat()} ({critic.status})",
    )
    log.info(
        "run %s complete (%s)%s", run_id, critic.status,
        "" if committed else " — commit skipped (artifacts are on disk)",
    )
    return EXIT_OK


def main(argv: list[str] | None = None, *, publisher=None, runner=None) -> int:
    """The CLI entry point. `publisher` and `runner` are the two injection seams.

    `runner` is the SUBPROCESS runner every researcher shells out through
    (`subprocess.run` in production). Threading it from here is what lets a test
    drive the whole v2 spine — the cycle fan-out and the dossier fan-out both —
    against canned envelopes without reaching a model, which the offline guard in
    `researcher.run_researcher_v2` otherwise refuses outright.
    """
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    root: Path = args.root
    now = datetime.now()
    if args.today:
        # Keep run_id and the coverage window on the SAME date. Otherwise a
        # faked run stamps its findings dir with the real date while the window
        # uses the fake one, and the artifact disagrees with itself.
        now = now.replace(year=args.today.year, month=args.today.month, day=args.today.day)
    today = now.date()

    # The version dispatch, and the only one: `--program` names a v2 program
    # detective and selects the v2 stage machine WHOLE. v1 below is untouched and
    # is deleted as its own ticket.
    if args.program:
        return _main_v2(args, root=root, now=now, publisher=publisher, runner=runner)
    if args.push:
        return _config_error(
            ValueError("--push is a per-program trigger — it requires --program <id>")
        )

    # --- Stage 0: the gate -------------------------------------------------
    # Exits before touching anything else. The scheduler is dumb; this is where
    # cadence actually lives, which is why it is a config fact and not a cron
    # fact — versioned, git-visible, reviewable in a diff.
    try:
        cadence = load_cadence(root / "config" / "cadence.toml")
        calendar = load_calendar(root / "config" / "calendar.toml")
        models_config = load_models(root / "config" / "models.toml")
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    # A verified, FRESH conference window makes every day a run day (surge →
    # daily), resolved from the dates ALREADY in calendar.toml — no network at the
    # gate, so a skipped day still exits in milliseconds. A stale calendar disables
    # surge here, matching the marker. Stage 1 re-verifies and may newly resolve
    # today's window; this is the cheap pre-check that keeps a surge day from being
    # gated out before verification runs.
    surge, _, _ = _resolve_surge_and_staleness(cadence, calendar, today, root / "issues")
    if not is_run_day(cadence, today) and surge is None and not args.force:
        log.info("%s is not a run day (cadence: %s) — no-op", today, ", ".join(cadence.days))
        return EXIT_OK
    if surge is not None:
        log.info("surge: %s day %d of %d — daily cadence", surge.window, surge.day, surge.of)

    # --- Stage 1: prepare --------------------------------------------------
    run_id = resolve_run_id(now)
    log.info("run_id=%s today=%s", run_id, today)

    try:
        state = load_state(root / "state")
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    dangling = check_entity_refs(state)
    if dangling:
        # The spine is what links watchlist to issue to queue to findings.
        # If it has forked, everything downstream is unsound — refuse the run
        # rather than publish an issue built on references that go nowhere.
        for ref in dangling:
            log.error("dangling entity_id %r referenced at %s", ref.entity_id, ref.where)
        log.error("%d dangling entity reference(s) — refusing to run", len(dangling))
        return EXIT_CONFIG_ERROR

    log.info(
        "state ok: %d entities, %d beliefs (v%s), %d queue items",
        len(state.entity_ids),
        len(state.thesis.get("beliefs", [])),
        state.thesis.get("version"),
        len(state.catalyst_queue.get("queue", [])),
    )

    window = resolve_coverage_window(
        root / "issues", today=today, cold_start_days=cadence.cold_start_lookback_days
    )
    if window.previous_issue:
        log.info("coverage %s → %s (joins %s)", window.from_, window.to, window.previous_issue)
    elif window.baseline_expired:
        # Advisory, not fatal: the validator files continuity_baseline_expired.
        log.warning(
            "no issue carrying a coverage window within the last %d — cold-start window %s → %s",
            LOOKBACK_FLOOR,
            window.from_,
            window.to,
        )
    else:
        log.info("no previous issue — run #1, cold-start window %s → %s", window.from_, window.to)

    # --- Stage 1: calendar verification + surge ----------------------------
    # Re-verify every window against its source, write accepted dates as a diff,
    # then re-resolve surge + staleness from the freshened calendar. The never-
    # write-unread-dates rule is mechanical (calendar._accept_dates): the verifier
    # proposes, the orchestrator decides. A verifier failure NEVER crashes the run.
    # Skipped on --dry-run (writes nothing, calls nothing) and when [surge] is
    # absent (the feature is off — no verification, no shadow defaults).
    if not args.dry_run and cadence.surge is not None:
        verifier_model = models_config.get("verifier")
        if not verifier_model:
            return _config_error(
                ValueError("config/models.toml: [models].verifier is required to verify the calendar")
            )
        verification = verify_calendar(
            calendar,
            model=verifier_model,
            max_surge_days=cadence.surge.max_surge_days,
        )
        if verification.updated:
            calendar_path = root / "config" / "calendar.toml"
            dates = {
                v.window_id: {"starts": v.starts, "ends": v.ends}
                for v in verification.windows
                if v.verified
            }
            if write_verified_dates(calendar_path, now.isoformat(timespec="seconds"), dates):
                # The diff is the review; the message cites the source each date was
                # read from. A SEPARATE commit from the run's — verification is worth
                # keeping even if this run later stubs, so it lands before the run
                # knows its own outcome.
                cited = ", ".join(
                    f"{wid} ({verification.sources[wid]})" for wid in verification.updated
                )
                git_commit_run(
                    root, run_id, [calendar_path], message=f"run {run_id}: verify calendar — {cited}"
                )
            calendar = load_calendar(calendar_path)  # reload with the fresh dates

    # Authoritative post-verification resolution. Staleness is the one failure that
    # would otherwise be silent: it disables surge AND files a calendar_stale
    # advisory in stage 4, so the marker rides on every issue whether or not the
    # critic runs (spec/02).
    surge, calendar_stale, stale_detail = _resolve_surge_and_staleness(
        cadence, calendar, today, root / "issues"
    )
    if surge is not None:
        log.info("surge: %s day %d of %d", surge.window, surge.day, surge.of)
    if calendar_stale:
        log.warning("%s (%s)", CALENDAR_STALE_MARKER, stale_detail)

    # --- Stage 2: research -------------------------------------------------
    try:
        beats = load_beats(root / "config" / "beats.toml")
        template = load_template(root / "prompts" / "researcher.md")
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    if args.beats:
        wanted = [b.strip() for b in args.beats.split(",")]
        unknown = set(wanted) - {b.id for b in beats}
        if unknown:
            return _config_error(ValueError(f"unknown beat(s): {', '.join(sorted(unknown))}"))
        beats = [b for b in beats if b.id in wanted]

    ctx = RunContext(
        run_id=run_id,
        coverage_window_from=window.from_.isoformat(),
        coverage_window_to=window.to.isoformat(),
        surge=surge,
    )

    if args.dry_run:
        for beat_id, prompt in render_all_prompts(beats, template, ctx, state).items():
            log.info("[dry-run] %s: rendered %d chars, no placeholders left", beat_id, len(prompt))
        log.info("[dry-run] complete — nothing called, nothing written")
        return EXIT_OK

    log.info("fanning out %d researcher(s): %s", len(beats), ", ".join(b.id for b in beats))
    stage = run_research_stage(beats, template, ctx, state, root)

    if stage.all_failed:
        # Every beat died: there are no facts to synthesize from, so this is a
        # stub, not a degradation. The dashboard shows the miss; the next
        # successful run widens its window over the days this one dropped.
        detail = f"all {len(stage.beats_failed)} beat(s) failed validation — see the run log"
        return _fail_run(
            root,
            run_id=run_id,
            now=now,
            window=ctx.window,
            stage="research",
            detail=detail,
            thesis_version=state.thesis.get("version"),
            beats_failed=stage.beats_failed,
        )

    log.info(
        "research complete: %d ran, %d failed", len(stage.beats_run), len(stage.beats_failed)
    )
    if stage.beats_failed:
        # Declared degradation: the run continues, and the ids land in the
        # issue's sources_and_method.beats_failed once the manager authors it —
        # with inline markers on the sections each beat fed.
        log.warning("beats_failed: %s", ", ".join(stage.beats_failed))

    # --- Stage 3: synthesize ----------------------------------------------
    # models_config was loaded once at the Stage-0 config gate — one home.
    try:
        manager_template = load_template(root / "prompts" / "manager.md")
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    prior_quiet = resolve_prior_quiet(root / "issues")

    identity = IssueIdentity(ctx=ctx, issue_id=today.isoformat(), published_at=now.isoformat())

    try:
        result, draft_path = run_synthesis_stage(
            root,
            identity=identity,
            state=state,
            beats=beats,
            stage=stage,
            models_config=models_config,
            manager_template=manager_template,
            prior_quiet=prior_quiet,
        )
    except ManagerFailed as exc:
        # Facts to synthesize, but no issue to publish: a synthesis stub, not a
        # degradation. Same immutability guard as the research path — a rerun
        # that fails must not overwrite a real issue with a stub.
        log.error("manager failed: %s", exc)
        return _fail_run(
            root,
            run_id=run_id,
            now=now,
            window=ctx.window,
            stage="synthesis",
            detail=str(exc),
            thesis_version=state.thesis.get("version"),
            beats_failed=stage.beats_failed,
        )

    log.info(
        "manager: draft %s (%d turn(s), $%.4f, attempt %d)",
        draft_path.relative_to(root),
        result.num_turns,
        result.cost_usd,
        result.attempts,
    )

    # --- Stage 4: validate -------------------------------------------------
    # The free deterministic gate, before a single token of critic budget is
    # spent. A block is handed back to the manager to EDIT (two retries, a budget
    # separate from the critic's); exhaustion is a validation stub, not a
    # degradation — a degradation explains an absence inside a VALID issue.
    try:
        retry_template = load_template(root / "prompts" / "manager-retry.md")
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    try:
        validation = run_validation_stage(
            draft=result.draft,
            draft_path=draft_path,
            state=state,
            issues_dir=root / "issues",
            beats_failed=stage.beats_failed,
            retry_template=retry_template,
            model=models_config["manager"],
            run_id=run_id,
            thesis_version=state.thesis.get("version"),
            calendar_stale=calendar_stale,
        )
    except (ManagerFailed, ValidationExhausted) as exc:
        # Either the retry manager died, or the budget ran out still blocking.
        # Both mean a draft exists but cannot be published: a validation stub,
        # with the same immutability guard as every other stub path.
        log.error("validation failed: %s", exc)
        return _fail_run(
            root,
            run_id=run_id,
            now=now,
            window=ctx.window,
            stage="validation",
            detail=str(exc),
            thesis_version=state.thesis.get("version"),
            beats_failed=stage.beats_failed,
        )

    log.info(
        "validation passed: %d retr%s, %d advisory finding(s)",
        validation.retries_used,
        "y" if validation.retries_used == 1 else "ies",
        len(validation.advisory),
    )

    # --- Stage 5: critique -------------------------------------------------
    # The cross-family gate: Codex judges what Claude wrote. One pass, its verdict
    # mapped to the run.status the issue publishes under (the retry loop is #35).
    # A missing or unparseable critic is NOT a failed run — run_critique_stage
    # resolves it to not_run and the digest publishes published_uncritiqued with a
    # banner. There is deliberately NO try/except here laundering a critic failure
    # into a stub: the handled failures are already not_run, and a genuine bug in
    # this wiring should escape loudly rather than masquerade as a critique stub.
    try:
        critic_template = load_template(root / "prompts" / "critic.md")
        critic_retry_template = load_template(root / "prompts" / "critic-retry.md")
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    critic = run_critique_stage(
        root,
        draft=validation.draft,
        state=state,
        run_id=run_id,
        beats_run=stage.beats_run,
        issues_dir=root / "issues",
        critic_template=critic_template,
        retry_template=critic_retry_template,
        model=models_config["critic"],
        manager_model=models_config["manager"],
        draft_path=draft_path,
        thesis_version=state.thesis.get("version"),
        schema_file=root / "prompts" / "critic-output-schema.json",
        surge=surge,
    )
    log.info(
        "critic: %s → %s (%d retr%s)%s",
        critic.verdict,
        critic.status,
        critic.retries_used,
        "y" if critic.retries_used == 1 else "ies",
        f" ({critic.reason})" if critic.reason else "",
    )
    # The retry loop may have edited the draft (manager fixes, rebuttals): publish
    # the draft that came OUT of stage 5, not the one that went in. The stage always
    # returns one, so there is no fallback.
    published_draft = critic.draft

    # --- Stage 6: publish --------------------------------------------------
    # Derived stats, the immutable issue, the regenerated manifest, the state
    # edits, and one git commit. A publish-stage failure becomes a publish stub
    # under the same immutability guard as every other stub path — but if the
    # issue was already written before the failure, publish already best-effort
    # regenerated the manifest and committed what exists, and the immutability
    # guard fires here so the run reports the failure without laundering it.
    try:
        publish = run_publish_stage(
            root, draft=published_draft, state=state, run_id=run_id, now=now, critic=critic,
            surge=surge,
        )
    except PublishedIssueExists as exc:
        log.error("%s", exc)
        return EXIT_RUN_FAILED
    except Exception as exc:  # noqa: BLE001 — any publish failure becomes a stub
        log.error("publish failed: %s", exc)
        return _fail_run(
            root,
            run_id=run_id,
            now=now,
            window=ctx.window,
            stage="publish",
            detail=str(exc),
            thesis_version=state.thesis.get("version"),
            beats_failed=stage.beats_failed,
        )

    log.info(
        "published %s (%s)%s",
        publish.issue_path.relative_to(root),
        publish.status,
        "" if publish.committed else " — commit skipped (issue is on disk)",
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
