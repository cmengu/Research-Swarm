"""Invoking one researcher.

Read-only is a HARD WALL, and the wall is what forces this whole design. The
researcher gets web search and nothing else — enforced by permission flags, not
by asking nicely in the prompt. It therefore CANNOT persist its own file, which
is why transport is stdout and why run.py is the sole writer.

Verified empirically, not assumed: with this flag set the model reports "I don't
have a Write/Edit or shell tool available in this session" and
`permission_denials` stays EMPTY — the tools are never presented at all. That is
stronger than a denial: there is nothing to deny, so no prompt injection can
talk its way past it.

Spec: docs/spec/04-researchers.md
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

from researchswarm.apertures import Aperture
from researchswarm.beats import Beat
from researchswarm.findings import FindingsInvalid, validate_findings, validate_findings_v2
from researchswarm.transport import TransportInvalid, parse_envelope, parse_result_json

log = logging.getLogger("researchswarm.researcher")

# Set by the test suite. A researcher call costs money and takes minutes, so a
# test that reaches one by accident burns both and hangs CI — which is exactly
# what happened the first time stage 2 was wired in: the gate tests from the
# previous build suddenly started doing real oncology research. Refusing loudly
# beats discovering it from a bill.
OFFLINE_ENV = "RESEARCHSWARM_OFFLINE"

# The wall. WebSearch/WebFetch in; everything that could write, run, or delegate
# out. Task is on the deny list because a subagent would inherit its own tool
# set and route around the whitelist — a live probe spawned one before it was
# blocked.
ALLOWED_TOOLS = ("WebSearch", "WebFetch")
DISALLOWED_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Task")

RETRY_PREAMBLE = (
    "Your previous output failed validation: {error}\n\n"
    "Re-emit the ENTIRE findings object as valid JSON only — no markdown fences, "
    "no preamble, no commentary. Fix exactly the problems listed above.\n\n"
)


class ResearcherFailed(RuntimeError):
    """The beat is dead. The run continues without it — one dead researcher
    must not kill the Monday issue."""


@dataclass(frozen=True)
class ResearcherResult:
    findings: dict
    attempts: int
    cost_usd: float
    num_turns: int


def build_command(prompt: str, beat: Beat) -> list[str]:
    """The argv for one read-only researcher.

    Note there is no --max-turns: this CLI has no such flag, so beat.max_turns
    reaches the model as prompt guidance only. See the note in run docs.
    """
    return [
        "claude",
        "-p", prompt,
        "--model", beat.model,
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--output-format", "json",
    ]


def run_researcher(
    beat: Beat,
    prompt: str,
    *,
    run_id: str,
    window: dict,
    known_entity_ids,
    timeout: int = 900,
    runner=subprocess.run,
) -> ResearcherResult:
    """Call one researcher, validate at the seam, retry once, or fail the beat.

    One retry only, with the error appended. Researchers are not re-run beyond
    that: the failure lands in beats_failed, the sections it fed carry an inline
    marker, and the run continues.
    """
    if os.environ.get(OFFLINE_ENV) and runner is subprocess.run:
        raise ResearcherFailed(
            f"{OFFLINE_ENV} is set but {beat.id} tried to call a real model. "
            "Inject a fake runner, or use --dry-run."
        )

    attempt_prompt = prompt
    last_error: str | None = None

    for attempt in (1, 2):
        command = build_command(attempt_prompt, beat)
        try:
            completed = runner(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ResearcherFailed(f"{beat.id}: timed out after {timeout}s") from exc

        if completed.returncode != 0:
            # claude -p reports its failure on stdout (a JSON envelope or plain
            # text), so stderr alone is usually blank — carry both or the
            # operator gets an empty error.
            raise ResearcherFailed(
                f"{beat.id}: claude exited {completed.returncode}: "
                f"stdout={completed.stdout[:400]!r} stderr={completed.stderr[:400]!r}"
            )

        try:
            envelope = parse_envelope(completed.stdout)
            findings = parse_result_json(envelope.get("result", ""))
            validate_findings(
                findings,
                beat_id=beat.id,
                run_id=run_id,
                window=window,
                known_entity_ids=known_entity_ids,
            )
        except (TransportInvalid, FindingsInvalid) as exc:
            last_error = str(exc)
            log.warning("%s: attempt %d failed validation: %s", beat.id, attempt, last_error)
            if attempt == 2:
                break
            attempt_prompt = RETRY_PREAMBLE.format(error=last_error) + prompt
            continue

        return ResearcherResult(
            findings=findings,
            attempts=attempt,
            cost_usd=envelope.get("total_cost_usd", 0.0),
            num_turns=envelope.get("num_turns", 0),
        )

    raise ResearcherFailed(f"{beat.id}: invalid output after 2 attempts: {last_error}")


# ---------------------------------------------------------------------------
# The v2 researcher — keyed by an aperture, not a beat.
#
# Additive alongside the v1 pair above; the v1 functions are untouched and the
# two run side by side while the engine migrates. What changed is only the SCOPE
# UNIT and where the model id comes from: a beat carried its own `model` (a
# per-beat override), an aperture does not — apertures differ in scope, never in
# rules, so one researcher model serves the whole `1 + N + 1` roster and the id
# is passed in from models config. Everything that makes a researcher safe is
# shared verbatim: the same ALLOWED_TOOLS/DISALLOWED_TOOLS wall, the same stdout
# transport, the same validate-retry-once-then-fail seam.
#
# Spec: docs/spec/04-researchers.md
# ---------------------------------------------------------------------------


def build_command_v2(prompt: str, model: str) -> list[str]:
    """The argv for one read-only v2 researcher.

    Identical to `build_command` but for where the model id comes from: an
    aperture has no per-scan model override, so the caller supplies the run's
    researcher id. Same wall, same flags — deliberately NOT a refactor of v1 into
    a shared helper, because the v1 path is frozen while it is being migrated off
    and a shared body would let a v2 change reach it.
    """
    return [
        "claude",
        "-p", prompt,
        "--model", model,
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--output-format", "json",
    ]


def run_researcher_v2(
    aperture: Aperture,
    prompt: str,
    *,
    model: str,
    program_id: str,
    run_id: str,
    window: dict,
    known_entity_ids,
    timeout: int = 900,
    runner=subprocess.run,
) -> ResearcherResult:
    """Call one v2 researcher, validate at the v2 seam, retry once, or fail it.

    The seam is `validate_findings_v2`, NOT the v1 validator: v2 findings are
    keyed by `aperture`/`program_id` and carry `priority_hint` and
    `coverage_notes.scope_run`, so a correct v2 payload FAILS the v1 checks. That
    is not hypothetical — a live v2 run was rejected for a missing `beat`, and on
    retry the model "fixed" it by emitting the v1 fields alongside the v2 ones: a
    false pass that hid the mismatch behind a payload answering to two contracts.
    Version-dispatching the seam is what keeps the retry an honest second chance.

    One retry only, error appended, same as v1. A second failure raises
    ResearcherFailed — the aperture lands in `apertures_degraded`, the sections it
    fed carry an inline marker, and the run continues.
    """
    if os.environ.get(OFFLINE_ENV) and runner is subprocess.run:
        raise ResearcherFailed(
            f"{OFFLINE_ENV} is set but {aperture.id} tried to call a real model. "
            "Inject a fake runner, or use --dry-run."
        )

    attempt_prompt = prompt
    last_error: str | None = None

    for attempt in (1, 2):
        command = build_command_v2(attempt_prompt, model)
        try:
            completed = runner(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ResearcherFailed(f"{aperture.id}: timed out after {timeout}s") from exc

        if completed.returncode != 0:
            # claude -p reports its failure on stdout, so stderr alone is usually
            # blank — carry both or the operator gets an empty error.
            raise ResearcherFailed(
                f"{aperture.id}: claude exited {completed.returncode}: "
                f"stdout={completed.stdout[:400]!r} stderr={completed.stderr[:400]!r}"
            )

        try:
            envelope = parse_envelope(completed.stdout)
            findings = parse_result_json(envelope.get("result", ""))
            validate_findings_v2(
                findings,
                aperture_id=aperture.id,
                program_id=program_id,
                run_id=run_id,
                window=window,
                known_entity_ids=known_entity_ids,
                aperture_kind=aperture.kind,
            )
        except (TransportInvalid, FindingsInvalid) as exc:
            last_error = str(exc)
            log.warning("%s: attempt %d failed validation: %s", aperture.id, attempt, last_error)
            if attempt == 2:
                break
            attempt_prompt = RETRY_PREAMBLE.format(error=last_error) + prompt
            continue

        return ResearcherResult(
            findings=findings,
            attempts=attempt,
            cost_usd=envelope.get("total_cost_usd", 0.0),
            num_turns=envelope.get("num_turns", 0),
        )

    raise ResearcherFailed(f"{aperture.id}: invalid output after 2 attempts: {last_error}")
