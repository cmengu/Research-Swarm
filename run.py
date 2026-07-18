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
import sys
from datetime import date, datetime
from pathlib import Path

from researchswarm.apertures import plan_apertures
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
from researchswarm.manager import ManagerFailed, load_models
from researchswarm.programs import (
    load_edges,
    load_entities,
    load_interests,
    load_program,
    program_roster,
)
from researchswarm.prompts import RunContext, load_template
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
    render_all_prompts,
    render_all_prompts_v2,
    run_research_stage,
    run_research_stage_v2,
)
from researchswarm.runs import (
    LOOKBACK_FLOOR,
    latest_covering_issue,
    resolve_coverage_window,
    resolve_prior_quiet,
    resolve_run_id,
)
from researchswarm.state import _load_json as load_state_json, check_entity_refs, load_state
from researchswarm.state_edits import apply_state_edits_v2
from researchswarm.stub import PublishedIssueExists
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


def _fail_run_v2(stage: str, detail: str) -> int:
    """A v2 run that died. Logs where and why, and returns EXIT_RUN_FAILED.

    v1's twin publishes a failed-run stub here. v2 still does not, and the reason
    has narrowed: #83 settled the three PUBLISHING shapes (issue path, manifest,
    registry), but a stub is "the same schema with empty sections, status failed,
    and failure.stage naming where it died" (spec/09 failure handling) — an
    EMITTED per-program issue, which needs the v2 `program` block (spec/07
    `program`) that `stub.write_failed_stub` does not write. That stub writer is
    its own ticket; inventing its shape here is the thing this run is gated on not
    doing. The failure stays loud in the log and in the exit code.

    What #83 DID settle is that this costs the program its *visibility* only in the
    dropdown, not in the switcher: the registry is a wholesale config ⋈ state join
    with config on the left, so a program whose only run failed — or which has
    never run at all — still appears, with `latest_issue: null`. Once the v2 stub
    writer lands, the stub reaches the registry for free, with no special case.
    """
    log.error("run failed at %s — %s", stage, detail)
    log.error(
        "no stub written: the v2 stub is an emitted issue carrying the v2 program "
        "block, and that stub writer is its own ticket"
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


def _main_v2(args, *, root: Path, now: datetime, publisher=None) -> int:
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

    ctx = RunContext(
        run_id=run_id,
        coverage_window_from=window.from_.isoformat(),
        coverage_window_to=window.to.isoformat(),
        surge=surge,
    )

    try:
        researcher_template = load_template(root / "prompts" / "researcher-v2.md")
        manager_template = load_template(root / "prompts" / "manager-v2.md")
        retry_template = load_template(root / "prompts" / "manager-retry.md")
        critic_template = load_template(root / "prompts" / "critic-v2.md")
        critic_retry_template = load_template(root / "prompts" / "critic-retry.md")
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    if args.dry_run:
        rendered = render_all_prompts_v2(
            active, researcher_template, program=program, interests=interests,
            edges=edges, thesis=thesis, ctx=ctx,
        )
        for aperture_id, prompt in rendered.items():
            log.info("[dry-run] %s: rendered %d chars, no placeholders left", aperture_id, len(prompt))
        log.info("[dry-run] complete — nothing called, nothing written")
        return EXIT_OK

    # --- Stage 2: research -------------------------------------------------
    # The known-entity set is WIDE (every shared record plus this program's
    # roster): it answers "does this entity_id resolve to anything", which is the
    # findings-contract's dangling check. The narrow roster travels separately to
    # the validator, where it is the coverage accountability set.
    known_entity_ids = set(entities) | roster
    researcher_model = models_config.get("researchers", RESEARCHER_MODEL_DEFAULT_V2)

    log.info("fanning out %d researcher(s) on %s", len(active), researcher_model)
    stage = run_research_stage_v2(
        apertures, researcher_template, ctx, root,
        program=program, interests=interests, edges=edges, thesis=thesis,
        known_entity_ids=known_entity_ids, model=researcher_model,
    )

    if stage.all_failed:
        return _fail_run_v2(
            "research",
            f"no aperture produced findings ({len(stage.apertures_degraded)} degraded) — "
            "see the run log",
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
        return _fail_run_v2("synthesis", str(exc))

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
        return _fail_run_v2("validation", str(exc))

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

    # One commit for the whole run, citing the run_id (spec/09 stage 6 step 5). A
    # failed commit is NOT a failed run: the artifacts are on disk either way.
    committed = git_commit_run(
        root,
        run_id,
        [p for p in [*touched, *artifacts] if p is not None],
        message=f"run {run_id}: {program.id} {today.isoformat()} ({critic.status})",
    )
    log.info(
        "run %s complete (%s)%s", run_id, critic.status,
        "" if committed else " — commit skipped (artifacts are on disk)",
    )
    return EXIT_OK


def main(argv: list[str] | None = None, *, publisher=None) -> int:
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
        return _main_v2(args, root=root, now=now, publisher=publisher)
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
