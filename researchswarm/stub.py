"""The failed-run stub.

A degradation explains an absence inside a valid issue; a stub says there is
NO valid issue. Same schema as a published issue — status "failed", empty
sections, failure.stage naming where it died — so the dashboard needs no
separate stub renderer and the archive keeps an honest record of the miss.

Two invariants downstream code relies on:

- A stub is TRANSPARENT to continuity. Its run.status is "failed", which the
  coverage-window search skips, so the next successful run automatically
  widens to reclaim the days the failed one never covered.
- A stub is still an issue file. It appears in the dashboard dropdown; a
  failed run is visible, not silent (no alerting infrastructure in v1).

Spec: docs/spec/07-issue-schema.md, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "1.0.0"

# The stages a run can die at, in pipeline order. "gate" is absent on purpose:
# a non-run day is a no-op, not a failure — only a stage inside an actual run
# can produce a stub.
FAILURE_STAGES = ("research", "synthesis", "validation", "critique", "publish")


class PublishedIssueExists(RuntimeError):
    """The date already has a real issue. Every published issue is immutable —
    a failed rerun must not replace one with a stub."""


def check_overwritable(path: Path) -> None:
    """Guard the one immutability rule every writer of issues/<date>.json obeys.

    A published issue is immutable: no later run edits an earlier one ([08]).
    The single carve-out is that a same-day rerun MAY replace its own earlier
    STUB — retrying a failure that then succeeds is the desired behaviour, and a
    stub is not a published issue. So this raises PublishedIssueExists only when
    the file already holds a NON-failed issue.

    Shared by both writers of that path — the stub writer here and the publisher
    ([publish.py]) — so the immutability seam lives in exactly one place and the
    two cannot disagree about what "already published" means. An unreadable file
    is treated as replaceable: it is not a published issue we can prove exists,
    and refusing to touch it would strand the date on corrupt bytes.
    """
    if not path.exists():
        return
    try:
        existing_status = json.loads(path.read_text())["issue"]["run"]["status"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return  # unreadable ≠ published; a fresh write may replace it
    if existing_status != "failed":
        raise PublishedIssueExists(
            f"{path} already holds a published issue (status {existing_status!r}) — "
            "refusing to overwrite it; published issues are immutable"
        )


def write_failed_stub(
    root: Path,
    *,
    run_id: str,
    now: datetime,
    window: dict,
    stage: str,
    detail: str,
    thesis_version: int | None = None,
    beats_failed: list[str] | None = None,
) -> Path:
    """Publish issues/<date>.json for a run that died at `stage`.

    The coverage_window records what the run ATTEMPTED to cover — informative
    for the dashboard, and safe to include because joining is gated on
    run.status, never on the window's presence.

    A same-day rerun may overwrite its own earlier STUB — retrying a failure
    is the desired behaviour — but a published (non-failed) issue is immutable
    and this path refuses to touch one. Without the refusal, a forced rerun of
    one dead beat (--beats) that failed again would replace the morning's real
    issue with a stub claiming the whole day failed.
    """
    if stage not in FAILURE_STAGES:
        raise ValueError(f"unknown failure stage {stage!r}; expected one of {FAILURE_STAGES}")

    issue_id = now.date().isoformat()
    path = root / "issues" / f"{issue_id}.json"
    check_overwritable(path)
    stub = {
        "schema_version": SCHEMA_VERSION,
        "issue": {
            "id": issue_id,
            "published_at": now.isoformat(),
            "coverage_window": dict(window),
            "run": {
                "run_id": run_id,
                "status": "failed",
                "critic_verdict": "not_run",
                "critic_retries": 0,
                "thesis_version": thesis_version,
            },
            "failure": {"stage": stage, "detail": detail},
        },
        "headline": None,
        "stats": {},
        "tldr_bullets": [],
        "catalyst_queue": {},
        "watchlist": [],
        "quiet_this_cycle": {},
        "new_on_radar": [],
        "themes_and_signals": [],
        "elsewhere_on_frontier": [],
        "thesis_updates": [],
        "critic_report": {},
        "sources_and_method": {"beats_failed": list(beats_failed or [])},
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stub, indent=2) + "\n")
    return path
