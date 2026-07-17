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
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from researchswarm.beats import load_beats
from researchswarm.cadence import is_run_day, load_cadence
from researchswarm.calendar import (
    load_calendar,
    resolve_surge,
    runs_since_verified,
    stale_reason,
    verify_calendar,
    write_verified_dates,
)
from researchswarm.critique import run_critique_stage
from researchswarm.manager import ManagerFailed, load_models
from researchswarm.prompts import RunContext, load_template
from researchswarm.publish import git_commit_run, publish_stub, run_publish_stage
from researchswarm.research import render_all_prompts, run_research_stage
from researchswarm.runs import (
    LOOKBACK_FLOOR,
    resolve_coverage_window,
    resolve_prior_quiet,
    resolve_run_id,
)
from researchswarm.state import check_entity_refs, load_state
from researchswarm.stub import PublishedIssueExists
from researchswarm.synthesis import IssueIdentity, run_synthesis_stage
from researchswarm.validation import ValidationExhausted, run_validation_stage
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


def main(argv: list[str] | None = None) -> int:
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
