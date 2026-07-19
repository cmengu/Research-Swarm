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


def issue_path(root: Path, issue_id: str) -> Path:
    """The path an issue with this id lives at: issues/<id>.json.

    One home for the layout rule, shared by the stub writer and the publisher so
    the two can never disagree about where a dated issue lands.
    """
    return root / "issues" / f"{issue_id}.json"


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
    path = issue_path(root, issue_id)
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


# ===========================================================================
# v2 — the per-program stub
# ===========================================================================
#
# Everything above this line is v1's flat single-digest stub and is UNTOUCHED:
# v1 is deleted whole as its own ticket, not eroded in place. The v2 twin is
# additive beside it for the same reason every other v2 twin is — `--program`
# selects the whole v2 stage machine, so no function has to ask which schema it
# is serving.
#
# Two things differ, and only two. The SHAPE is v2.0.0 (spec/07): the issue block
# carries `program_id`, and the emitted issue carries a top-level `program` block,
# because a v2 issue is a per-program detective's issue and a stub is "the same
# schema with empty sections" (spec/09 failure handling). The WRITE is somebody
# else's: this module only BUILDS the dict. run.py hands it to the publisher seam
# (`publish.run_publish_stage_v2`), which is what makes the stub reach the
# manifest and the registry — "stubs appear" in the dropdown (spec/08) — and what
# keeps run.py the sole writer.

SCHEMA_VERSION_V2 = "2.0.0"


def program_block_v2(program) -> dict:
    """The v2 `program` block (spec/07 `program`) for a run that never got a draft.

    On a successful run the manager authors this block from the program config;
    on a stub there is no manager, so it is assembled straight from the same
    config the manager would have been handed. Every field that config actually
    holds is filled; `clinical_stage` is not in `config/programs/<id>.toml` at all
    and is emitted as null rather than guessed — a stub inventing a clinical stage
    would be a fact nobody sourced.

    `one_line` reads the config's `mechanism` (spec/08 names it the five-second
    test, and [07] sources `program.one_line` from the same file), defaulting to
    empty when the program has not written one — an unlabelled stub is a smaller
    failure than a crashed failure path.
    """
    return {
        "id": program.id,
        "name": program.name,
        "sponsor": program.sponsor,
        "modality": program.modality,
        "target": program.target,
        "moa": program.moa,
        "one_line": getattr(program, "mechanism", "") or "",
        "priority_indications": [
            i.id for i in program.indications if i.role == "priority_indication"
        ],
        "clinical_stage": None,
        "config_source": f"config/programs/{program.id}.toml",
        "aperture": {
            "biology_scan": {"target": program.target, "moa": program.moa},
            "arena_scans": list(program.active_arena_ids),
        },
    }


def failed_stub_v2(
    *,
    program,
    issue_id: str,
    run_id: str,
    now: datetime,
    window: dict,
    stage: str,
    detail: str,
    thesis_version: int | None = None,
    interest_list_version: int | None = None,
    apertures_degraded: list[str] | None = None,
) -> dict:
    """Build the v2.0.0 issue a run that died at `stage` publishes in place of one.

    Same schema, empty sections, `run.status: "failed"`, and a `failure` block
    naming where it died (spec/09 failure handling). The field names in that block
    are `stage` and `detail` — [07] `issue.failure` — which is exactly what
    `dashboard/index.html`'s `renderStub` reads, so the reader sees the stage and
    the reason rather than its "unknown" / "See the run log." fallbacks.

    The empty sections follow v1's stub choices unchanged — `null` headline,
    empty containers — because "empty section" is not a shape decision to reinvent
    per version. Only the KEY SET is v2's (competitors, indications, house_view,
    newly_discovered), and `sources_and_method.apertures_degraded` carries what
    v1's `beats_failed` carried: which apertures died on the way down.

    The `coverage_window` records what the run ATTEMPTED to cover. Safe to
    include, and informative: the continuity join is gated on `run.status`, never
    on the window's presence, so the next successful run still walks PAST this
    stub and widens to reclaim the days it never covered ([06], spec/09).

    Nothing here writes. run.py owns every write and every git call.
    """
    if stage not in FAILURE_STAGES:
        raise ValueError(f"unknown failure stage {stage!r}; expected one of {FAILURE_STAGES}")

    return {
        "schema_version": SCHEMA_VERSION_V2,
        "issue": {
            "id": issue_id,
            "program_id": program.id,
            "published_at": now.isoformat(),
            "coverage_window": dict(window),
            "run": {
                "run_id": run_id,
                "status": "failed",
                "critic_verdict": "not_run",
                "critic_retries": 0,
                "thesis_version": thesis_version,
                "interest_list_version": interest_list_version,
                "models": {},
            },
            "failure": {"stage": stage, "detail": detail},
        },
        "program": program_block_v2(program),
        "headline": None,
        "stats": {},
        "tldr_bullets": [],
        "catalyst_queue": {},
        "competitors": [],
        "indications": [],
        "quiet_this_cycle": {},
        "newly_discovered": [],
        "house_view": {},
        "thesis_updates": [],
        "critic_report": {},
        "sources_and_method": {"apertures_degraded": list(apertures_degraded or [])},
    }
