"""The `claude -p --output-format json` wire format — one parser, one place.

The two CLAUDE-family agents (researcher, manager) talk over the SAME wire:
`claude -p` with `--output-format json` wraps the model's final message in a
result envelope, and the final message is required to be exactly one JSON object.
That is a transport fact, independent of WHOSE output rides inside — findings or
an issue draft — so the two parsing steps live here, once, and both callers
consume them.

The critic is deliberately NOT here. It is Codex (`codex exec --json`), and its
wire is different in both shape and failure semantics: the final message lands in
a `-o` file rather than an envelope, stdout is a JSONL event stream, and a broken
critic resolves to `not_run` (a publishable outcome) rather than a retry. Folding
that into this parser would force one seam to serve two envelopes and two failure
policies. So the critic's wire is parsed in critic.py, and the two parsers stay
separate — a Claude wire failure is a retry, a Codex wire failure is not_run.

`TransportInvalid` is transport-NEUTRAL on purpose. A malformed envelope is not a
"bad findings" problem or a "bad issue" problem; it is a wire problem. Each
caller's seam then folds it into its own retry flow alongside its own
schema-validation error — the researcher into ResearcherFailed, the manager into
ManagerFailed — without either module reaching into the other's privates for the
shared step.

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
