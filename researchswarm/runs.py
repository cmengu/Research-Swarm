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


def resolve_coverage_window(
    issues_dir: Path,
    today: date,
    cold_start_days: int = 7,
) -> CoverageWindow:
    """Where this run's coverage starts, and what it joins to.

    Walks back from the newest issue looking for one that carries a coverage
    window, skipping stubs. Stops at LOOKBACK_FLOOR.

    On run #1 (or once the floor is hit) there is no baseline to join to, and
    the window falls back to `cold_start_days` before today. Run #1 needs no
    protection from any of the checks this baseline feeds: it CREATES the values
    they guard.
    """
    issues_dir = Path(issues_dir)
    scanned = 0

    for path in _covering_issues_newest_first(issues_dir):
        if scanned >= LOOKBACK_FLOOR:
            break
        scanned += 1

        try:
            issue = json.loads(path.read_text()).get("issue", {})
        except json.JSONDecodeError:
            continue  # an unreadable issue is not a join point

        window = issue.get("coverage_window")
        status = issue.get("run", {}).get("status")
        if status in COVERING_STATUSES and window and window.get("to"):
            return CoverageWindow(
                from_=date.fromisoformat(window["to"]),
                to=today,
                previous_issue=issue.get("id"),
            )

    # No baseline found: run #1, or nothing but stubs within the floor.
    return CoverageWindow(
        from_=today - timedelta(days=cold_start_days),
        to=today,
        previous_issue=None,
        baseline_expired=scanned >= LOOKBACK_FLOOR,
    )
