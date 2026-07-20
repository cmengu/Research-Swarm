"""Invoking the manager — the one component that interprets.

The manager gets NO tools at all, and the absence is the design, not an
oversight. It gets no web because it may add no new facts: its whole input is
the six findings piles plus fresh state, and a manager that could search would
be tempted to paper over a thin cycle with something the researchers never
found — precisely the retry rule spec/05 forbids. It gets no write because
stdout is the transport and run.py is the sole writer, exactly as for the
researcher. Both walls are enforced by permission FLAGS, not by asking nicely
in the prompt.

Transport mirrors the researcher: --output-format json wraps the final message
in an envelope, the final message must be one JSON object, and the seam is
validated immediately with one retry. The envelope/result parsing lives in
researchswarm.transport — one wire format, one parser — so both agents consume
it rather than one reaching into the other's internals.

Spec: docs/spec/05-manager.md, docs/spec/07-issue-schema.md
"""

from __future__ import annotations

import logging
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from researchswarm.researcher import OFFLINE_ENV, RETRY_PREAMBLE
from researchswarm.transport import TransportInvalid, parse_envelope, parse_result_json

log = logging.getLogger("researchswarm.manager")

# No tools granted. The disallow list covers the writers, the shell, the
# delegation escape hatch (Task — a subagent inherits its own tools and would
# route around the wall), AND the web tools: the manager adds no new facts, so
# WebSearch/WebFetch are denied here where they were granted to the researcher.
# build_manager_command passes an EMPTY --allowedTools so nothing at all is
# granted by default; the manager is pure text in, text out.
DISALLOWED_TOOLS = (
    "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Task", "WebSearch", "WebFetch",
)

SCHEMA_VERSION = "1.0.0"
SCHEMA_VERSION_V2 = "2.0.0"

# The 14 top-level keys of issue.json v1.0.0 (docs/spec/07). The seam checks
# presence only — the deep deterministic checks (entity accounting, dormant-slot
# gating, queue tamper) are build 05's validator, deliberately not here.
TOP_LEVEL_KEYS = (
    "schema_version", "issue", "headline", "stats", "tldr_bullets", "catalyst_queue",
    "watchlist", "quiet_this_cycle", "new_on_radar", "themes_and_signals",
    "elsewhere_on_frontier", "thesis_updates", "critic_report", "sources_and_method",
)

# The 15 top-level keys of issue.json v2.0.0 (docs/spec/07 top level). The noun
# changed: watchlist → competitors, new_on_radar → newly_discovered,
# elsewhere_on_frontier + themes_and_signals → house_view, and program +
# indications are new. Same presence-only contract as v1 at this seam.
TOP_LEVEL_KEYS_V2 = (
    "schema_version", "issue", "program", "headline", "stats", "tldr_bullets",
    "catalyst_queue", "competitors", "indications", "quiet_this_cycle",
    "newly_discovered", "house_view", "thesis_updates", "critic_report",
    "sources_and_method",
)


class ManagerFailed(RuntimeError):
    """The manager could not produce a valid draft. There are facts to
    synthesize but no issue to publish — run.py turns this into a synthesis
    stub, not a degradation."""


class IssueDraftInvalid(ValueError):
    """The seam validator's verdict on the manager's draft. Carries every
    problem, not just the first, so one retry can fix everything at once."""


@dataclass(frozen=True)
class ManagerResult:
    draft: dict
    attempts: int
    cost_usd: float
    num_turns: int


def load_models(path: Path) -> dict:
    """Load config/models.toml → the [models] table (per-role model ids).

    Strict like the other loaders: a missing per-role id must fail loudly here,
    not surface as an empty --model / -m flag that the CLI rejects stages later
    with a message pointing nowhere near the cause. Both single-agent roles are
    required — the manager (build 04) and the critic (build 07, Codex). The
    researchers' model is deliberately NOT here: it is a per-beat override in
    config/beats.toml.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"models config not found: {path}")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    models = raw.get("models")
    if not isinstance(models, dict) or not models.get("manager"):
        raise ValueError(f"{path}: [models].manager is required")
    if not models.get("critic"):
        raise ValueError(f"{path}: [models].critic is required")

    return models


def build_manager_command(prompt: str, model: str) -> list[str]:
    """The argv for the tool-less manager.

    Note there is no --max-turns: this CLI has no such flag (learned live in
    build 03). It does not matter here — the manager has no tools to turn.
    """
    return [
        "claude",
        "-p", prompt,
        "--model", model,
        # An empty allow list grants nothing; the disallow list makes the wall
        # explicit and legible in the argv.
        "--allowedTools", "",
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--output-format", "json",
    ]


def validate_issue_draft(draft, *, thesis_version, run_id) -> None:
    """Mechanical-shape check at the seam, or raise IssueDraftInvalid.

    ONLY the shape a script can be certain about: the object parses, the version
    is right, the top-level keys are present, stats is empty, the headline carries
    a so_what, and the run block echoes the identifiers we handed the manager. The
    deep checks — entity accounting, dormant-slot gating, queue tamper, the
    degradation register — are build 05's deterministic validator, and doing
    them here would duplicate judgment the wrong stage owns.

    Dispatches on the DRAFT's own `schema_version`: a 2.0.0 draft is held to the
    v2 seam contract (the program block, the v2 key set), anything else to v1.
    stats is the load-bearing invariant in both: an authored stats is a contract
    breach, not a typo — the orchestrator derives every count so the bar cannot
    lie, and a manager that filled it in has claimed a number it was told never
    to author.
    """
    if not isinstance(draft, dict):
        raise IssueDraftInvalid("draft must be a JSON object")

    if draft.get("schema_version") == SCHEMA_VERSION_V2:
        _validate_issue_draft_v2(draft, thesis_version=thesis_version, run_id=run_id)
        return

    problems: list[str] = []

    if draft.get("schema_version") != SCHEMA_VERSION:
        problems.append(
            f"schema_version {draft.get('schema_version')!r} is not {SCHEMA_VERSION!r}"
        )

    for key in TOP_LEVEL_KEYS:
        if key not in draft:
            problems.append(f"missing required top-level key {key!r}")

    # stats == {} exactly. A non-empty stats means the manager authored counts
    # it was told the orchestrator derives — the bar cannot lie.
    if draft.get("stats") != {}:
        problems.append("stats must be exactly {} — the orchestrator derives counts, never the manager")

    headline = draft.get("headline")
    if not isinstance(headline, dict):
        problems.append("headline must be an object")
    elif not headline.get("so_what"):
        problems.append("headline.so_what is required and must be non-empty")

    run = draft.get("issue", {}).get("run", {}) if isinstance(draft.get("issue"), dict) else {}
    if run.get("run_id") != run_id:
        problems.append(f"issue.run.run_id {run.get('run_id')!r} does not match this run ({run_id!r})")
    if run.get("thesis_version") != thesis_version:
        problems.append(
            f"issue.run.thesis_version {run.get('thesis_version')!r} does not match {thesis_version!r}"
        )

    if problems:
        raise IssueDraftInvalid("; ".join(problems))


def _validate_issue_draft_v2(draft, *, thesis_version, run_id) -> None:
    """The v2.0.0 seam contract ([07] v2). Same presence-only philosophy as v1,
    with the noun change: the 15 v2 keys, and a `program` block (the detective's
    subject) present with its id and its load-bearing `moa`. Everything else —
    the read-throughs, the typed relations, coverage — is the deterministic
    validator's job (build 05 v2), not the seam's.
    """
    problems: list[str] = []

    for key in TOP_LEVEL_KEYS_V2:
        if key not in draft:
            problems.append(f"missing required top-level key {key!r}")

    # `stats` is NORMALIZED, not blocked. It was blocked, and it was the single
    # most persistent failure in the system: it appeared in every live run,
    # including ones that were otherwise converging, and it burned a retry each
    # time. The block bought nothing — `publish.derive_full_stats` recomputes the
    # whole block from the issue and overwrites whatever arrives here, so the
    # rejected value was already destined for the bin. Refusing a draft over a
    # field the orchestrator is about to discard is a gate defending an invariant
    # that the next stage enforces unconditionally.
    #
    # The rule itself still holds — the manager does not get to author counts —
    # so a non-empty stats is emptied here and logged. The contract is enforced by
    # construction rather than by argument, which is the same trade the source and
    # entity writers already make.
    if draft.get("stats") != {}:
        log.info("manager: emptying author-supplied stats — the orchestrator derives them")
        draft["stats"] = {}

    program = draft.get("program")
    if not isinstance(program, dict) or not program:
        problems.append("program block is required and must be a non-empty object")
    else:
        for field_name in ("id", "moa"):
            if not program.get(field_name):
                problems.append(f"program.{field_name} is required (the load-bearing aperture fields)")

    headline = draft.get("headline")
    if not isinstance(headline, dict):
        problems.append("headline must be an object")
    elif not headline.get("so_what"):
        problems.append("headline.so_what is required and must be non-empty")

    run = draft.get("issue", {}).get("run", {}) if isinstance(draft.get("issue"), dict) else {}
    if run.get("run_id") != run_id:
        problems.append(f"issue.run.run_id {run.get('run_id')!r} does not match this run ({run_id!r})")
    if run.get("thesis_version") != thesis_version:
        problems.append(
            f"issue.run.thesis_version {run.get('thesis_version')!r} does not match {thesis_version!r}"
        )

    if problems:
        raise IssueDraftInvalid("; ".join(problems))


def run_manager(
    prompt: str,
    *,
    model: str,
    thesis_version,
    run_id: str,
    timeout: int = 900,
    runner=subprocess.run,
) -> ManagerResult:
    """Call the manager, validate at the seam, retry once, or fail.

    One retry only, with the errors appended — same contract as the researcher.
    Beyond that there is no issue to publish, so run.py writes a synthesis stub.
    A manager failure is NOT a degradation: a degradation explains an absence
    inside a valid issue, and here the whole issue is absent.
    """
    if os.environ.get(OFFLINE_ENV) and runner is subprocess.run:
        raise ManagerFailed(
            f"{OFFLINE_ENV} is set but the manager tried to call a real model. "
            "Inject a fake runner, or use --dry-run."
        )

    attempt_prompt = prompt
    last_error: str | None = None

    for attempt in (1, 2):
        command = build_manager_command(attempt_prompt, model)
        try:
            completed = runner(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ManagerFailed(f"manager timed out after {timeout}s") from exc

        if completed.returncode != 0:
            # claude -p reports its failure on stdout (a JSON envelope or plain
            # text); stderr is usually blank. Carry both or the operator gets an
            # empty error — the same trap that hid a fan-out failure in build 03.
            raise ManagerFailed(
                f"manager: claude exited {completed.returncode}: "
                f"stdout={completed.stdout[:400]!r} stderr={completed.stderr[:400]!r}"
            )

        try:
            envelope = parse_envelope(completed.stdout)
            draft = parse_result_json(envelope.get("result", ""))
            validate_issue_draft(draft, thesis_version=thesis_version, run_id=run_id)
        except (TransportInvalid, IssueDraftInvalid) as exc:
            last_error = str(exc)
            log.warning("manager: attempt %d failed validation: %s", attempt, last_error)
            if attempt == 2:
                break
            attempt_prompt = RETRY_PREAMBLE.format(error=last_error) + prompt
            continue

        return ManagerResult(
            draft=draft,
            attempts=attempt,
            cost_usd=envelope.get("total_cost_usd", 0.0),
            num_turns=envelope.get("num_turns", 0),
        )

    raise ManagerFailed(f"manager: invalid output after 2 attempts: {last_error}")
