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

import json
import logging
import os
import subprocess
from dataclasses import dataclass

from researchswarm.beats import Beat
from researchswarm.findings import FindingsInvalid, validate_findings

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


def _parse_envelope(stdout: str) -> dict:
    """--output-format json wraps the final message in a result envelope."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise FindingsInvalid(f"claude did not return a JSON envelope: {exc}") from exc

    if envelope.get("is_error"):
        raise FindingsInvalid(f"claude reported an error: {envelope.get('result')!r}")

    return envelope


def _parse_findings(result_text: str) -> dict:
    """The final message must be exactly one JSON object.

    We strip a ```json fence if one appears. The prompt forbids fences, and the
    retry will say so — but a fence is a formatting slip around otherwise good
    facts, and burning a retry (and a fresh set of web searches) to re-punctuate
    is a bad trade.
    """
    text = result_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FindingsInvalid(f"final message was not one JSON object: {exc}") from exc


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
            raise ResearcherFailed(
                f"{beat.id}: claude exited {completed.returncode}: {completed.stderr[:400]}"
            )

        try:
            envelope = _parse_envelope(completed.stdout)
            findings = _parse_findings(envelope.get("result", ""))
            validate_findings(
                findings,
                beat_id=beat.id,
                run_id=run_id,
                window=window,
                known_entity_ids=known_entity_ids,
            )
        except FindingsInvalid as exc:
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
