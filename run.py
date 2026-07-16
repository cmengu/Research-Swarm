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
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from researchswarm.beats import load_beats
from researchswarm.cadence import is_run_day, load_cadence
from researchswarm.prompts import RunContext, load_template, render_researcher_prompt
from researchswarm.researcher import ResearcherFailed, run_researcher
from researchswarm.runs import LOOKBACK_FLOOR, resolve_coverage_window, resolve_run_id
from researchswarm.state import check_entity_refs, load_state

REPO_ROOT = Path(__file__).resolve().parent

EXIT_OK = 0
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
        help="Comma-separated beat ids to run (default: all). The fan-out to all "
        "six lands in build 03; until then this is how you drive one.",
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


def _persist_findings(root: Path, run_id: str, beat_id: str, findings: dict) -> Path:
    """run.py is the SOLE writer of the findings corpus.

    The researcher physically cannot do this — it has no write tool — which is
    the point: persistence can't be forgotten by an agent, and the corpus is
    evidence rather than scratch. The critic reads it to catch what the manager
    found and then dropped.
    """
    findings_dir = root / "runs" / run_id / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    path = findings_dir / f"{beat_id}.json"
    path.write_text(json.dumps(findings, indent=2) + "\n")
    return path


def _run_research(
    beats, template: str, ctx: RunContext, state, root: Path, run_id: str, dry_run: bool
) -> tuple[list[str], list[str]]:
    """Stage 2. Returns (beats_run, beats_failed).

    Sequential for now — the parallel fan-out to all six lands in build 03,
    which is also where an all-six failure becomes a stub.
    """
    window = {"from": ctx.coverage_window_from, "to": ctx.coverage_window_to}
    beats_run: list[str] = []
    beats_failed: list[str] = []

    for beat in beats:
        prompt = render_researcher_prompt(template, beat, ctx, state)

        if dry_run:
            log.info("[dry-run] %s: rendered %d chars, no placeholders left", beat.id, len(prompt))
            continue

        log.info("%s: researching (%s)…", beat.id, beat.model)
        try:
            result = run_researcher(
                beat, prompt, run_id=run_id, window=window,
                known_entity_ids=state.entity_ids,
            )
        except ResearcherFailed as exc:
            # One dead researcher must not kill the Monday issue. The beat lands
            # in beats_failed; the manager marks the sections it fed inline.
            log.warning("%s: BEAT FAILED — %s", beat.id, exc)
            beats_failed.append(beat.id)
            continue

        path = _persist_findings(root, run_id, beat.id, result.findings)
        beats_run.append(beat.id)
        log.info(
            "%s: %d finding(s), quiet=%s, %d turn(s), $%.4f, attempt %d → %s",
            beat.id,
            len(result.findings.get("findings", [])),
            result.findings.get("quiet"),
            result.num_turns,
            result.cost_usd,
            result.attempts,
            path.relative_to(root),
        )

    return beats_run, beats_failed


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
    except (FileNotFoundError, ValueError) as exc:
        return _config_error(exc)

    if not is_run_day(cadence, today) and not args.force:
        log.info("%s is not a run day (cadence: %s) — no-op", today, ", ".join(cadence.days))
        return EXIT_OK

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
    )

    beats_run, beats_failed = _run_research(
        beats, template, ctx, state, root, run_id, args.dry_run
    )

    if args.dry_run:
        log.info("[dry-run] complete — nothing called, nothing written")
        return EXIT_OK

    log.info("research complete: %d ran, %d failed", len(beats_run), len(beats_failed))

    # --- Stages 3-6 --------------------------------------------------------
    log.info("synthesize → publish land in builds 04-06")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
