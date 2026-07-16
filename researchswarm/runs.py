"""Run identity and the coverage window.

The window binds `from` to the most recent issue that actually CARRIES a
coverage window — never the positionally-previous issue. A stub published no
window and covered no days, so it cannot be a join point.

This is not a nicety. If "previous issue" meant positionally previous, a single
failed run would leave the days it should have covered unreported by everyone:
the stub covered nothing, and the run after it would start from the stub's own
date. The backwards search closes that, and makes run #1 fall out for free —
the search returns nothing, the window falls back to a cold start, no special
case. The only requirement is that an empty result is tolerated, not an error.

Spec: docs/spec/02-cadence-and-surge.md, docs/spec/06-validator-and-critic.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# How far back the search walks before giving up. Twelve consecutive issues
# carrying no window means the system has a louder problem than a broken join,
# and an unbounded scan would hide it behind a slow check rather than surface it.
# Provisional: calibrated to ~6 weeks at Mon+Thu. Recalibrate on real cadence.
LOOKBACK_FLOOR = 12

# Statuses that mean an issue really published and really covered days.
# A missing critic is not a failed run: published_uncritiqued is a good digest
# that says it is unvetted, and it is a perfectly valid join point.
COVERING_STATUSES = frozenset(
    {"published", "published_uncritiqued", "published_with_unresolved_findings"}
)


@dataclass(frozen=True)
class CoverageWindow:
    from_: date
    to: date
    previous_issue: str | None
    baseline_expired: bool = False


@dataclass(frozen=True)
class ContinuityMatch:
    """The result of a backwards continuity search.

    `payload` is the whole issue file of the most recent COVERING issue carrying
    the compared field, or None if none was found. `expired` is True when the
    search hit the 12-issue floor without a match — the caller files
    `continuity_baseline_expired` (advisory), never an error.
    """

    payload: dict | None
    expired: bool


def resolve_run_id(now: datetime) -> str:
    """run_YYYYMMDD_HHMM — stable for the whole run; names the findings dir."""
    return f"run_{now:%Y%m%d_%H%M}"


def _covering_issues_newest_first(issues_dir: Path) -> list[Path]:
    if not issues_dir.exists():
        return []
    # Issue filenames are dated ids, so lexical sort is chronological.
    # index.json is the manifest, not an issue.
    return sorted(
        (p for p in issues_dir.glob("*.json") if p.name != "index.json"),
        reverse=True,
    )


def find_latest_issue_with(issues_dir, field, *, floor: int = LOOKBACK_FLOOR) -> ContinuityMatch:
    """Walk COVERING issues newest-first, return the first carrying `field`.

    The one continuity primitive: the coverage window, the prior quiet counts,
    and the validator's queue-tamper baseline all bind to *the most recent issue
    that actually carries the thing being compared*, walking back past stubs — a
    stub published no snapshot and covered no window, so it cannot be a join
    point. Binding positionally instead would let a single failed run launder
    every cross-issue invariant.

    `field` is either a top-level issue-payload key (matched when its value is
    present and non-empty) or a callable predicate(payload) -> bool.

    The scan is bounded at `floor` issues (stubs counted, exactly as the older
    walkers counted them): twelve issues without the field means a louder
    problem than a broken join, and an unbounded scan would hide it behind a slow
    check. An empty result on run #1 is tolerated — no special case, no bootstrap
    flag; `expired` is False (there was nothing to expire), True only when the
    floor was actually reached without a match.
    """
    matches = field if callable(field) else (lambda p: p.get(field) not in (None, {}, [], ""))
    scanned = 0

    for path in _covering_issues_newest_first(Path(issues_dir)):
        if scanned >= floor:
            break
        scanned += 1

        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue  # an unreadable issue is not a join point

        if payload.get("issue", {}).get("run", {}).get("status") not in COVERING_STATUSES:
            continue
        if matches(payload):
            return ContinuityMatch(payload=payload, expired=False)

    return ContinuityMatch(payload=None, expired=scanned >= floor)


def resolve_coverage_window(
    issues_dir: Path,
    today: date,
    cold_start_days: int = 7,
) -> CoverageWindow:
    """Where this run's coverage starts, and what it joins to.

    Binds `from` to the most recent issue that carries a coverage window,
    skipping stubs. On run #1 (or once the floor is hit) there is no baseline to
    join to, and the window falls back to `cold_start_days` before today. Run #1
    needs no protection from any of the checks this baseline feeds: it CREATES
    the values they guard.
    """
    match = find_latest_issue_with(
        issues_dir,
        lambda p: p.get("issue", {}).get("coverage_window", {}).get("to"),
    )
    if match.payload is not None:
        issue = match.payload["issue"]
        return CoverageWindow(
            from_=date.fromisoformat(issue["coverage_window"]["to"]),
            to=today,
            previous_issue=issue.get("id"),
        )

    return CoverageWindow(
        from_=today - timedelta(days=cold_start_days),
        to=today,
        previous_issue=None,
        baseline_expired=match.expired,
    )


def resolve_prior_quiet(issues_dir: Path) -> dict[str, int]:
    """Prior cycles_quiet per entity, from the most recent COVERING issue.

    The manager increments cycles_quiet honestly across issues; this hands it
    the previous counts to increment from. It joins to the last issue that
    actually covered days — walking past stubs exactly like the coverage window,
    so a failed run does not reset every entity's quiet streak to zero. An empty
    map is the honest value on run #1: nothing to increment from, so every quiet
    entity this cycle starts at 1.
    """
    match = find_latest_issue_with(issues_dir, lambda p: True)
    if match.payload is None:
        return {}

    no_news = match.payload.get("quiet_this_cycle", {}).get("no_news", [])
    return {
        entry["entity_id"]: entry.get("cycles_quiet", 0)
        for entry in no_news
        if entry.get("entity_id")
    }
