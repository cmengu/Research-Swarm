"""The `claude -p --output-format json` wire format — one parser, one place.

Every agent this system runs (researcher, manager, and the critic to come)
talks over the SAME wire: `claude -p` with `--output-format json` wraps the
model's final message in a result envelope, and the final message is required
to be exactly one JSON object. That is a transport fact, independent of WHOSE
output rides inside — findings, an issue draft, a critic verdict. So the two
parsing steps live here, once, and every caller consumes them.

The exception is transport-NEUTRAL on purpose. A malformed envelope is not a
"bad findings" problem or a "bad issue" problem; it is a wire problem. Each
caller's seam then folds `TransportInvalid` into its own retry flow alongside
its own schema-validation error — the researcher into ResearcherFailed, the
manager into ManagerFailed — without either module reaching into the other's
privates for the shared step.

Spec: docs/spec/04-researchers.md#transport, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import json


class TransportInvalid(ValueError):
    """The `claude -p` envelope or its final message did not parse. A wire
    failure, not a schema failure — each caller wraps it into its own retry."""


def parse_envelope(stdout: str) -> dict:
    """--output-format json wraps the final message in a result envelope."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise TransportInvalid(f"claude did not return a JSON envelope: {exc}") from exc

    if envelope.get("is_error"):
        raise TransportInvalid(f"claude reported an error: {envelope.get('result')!r}")

    return envelope


def parse_result_json(result_text: str) -> dict:
    """The final message must be exactly one JSON object.

    We strip a ```json fence if one appears. The prompts forbid fences, and the
    retry will say so — but a fence is a formatting slip around otherwise good
    output, and burning a retry (and a fresh, expensive model call) to
    re-punctuate is a bad trade.
    """
    text = result_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TransportInvalid(f"final message was not one JSON object: {exc}") from exc
