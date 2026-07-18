# Manager retry prompt template (validation stage)

Asset for ticket [#32](https://github.com/cmengu/Research-Swarm/issues/32). The deterministic validator caught structural problems in the manager's draft. This is the prompt that hands the draft back so the manager can **edit** it — not regenerate it.

Like the other prompt files, this is a document ABOUT the template with the template itself fenced inside a single ```text block. `run.py` extracts that fence; these notes stay out of the model's context. Two `{{double_brace}}` placeholders are filled at render time by `render_manager_retry_prompt`: `{{prior_draft_json}}` (the manager's own prior draft) and `{{blocking_findings}}` (exactly the blocking findings, nothing advisory).

## The one rule this template exists to enforce

**Edit, do not regenerate.** The retry loop is expensive and is spent on the specific problems the validator named. Sections that already passed must not silently mutate between rounds — a regenerated draft could fix the empty section and quietly rewrite a headline the reader would never know had changed. So the manager receives its OWN prior draft verbatim and is told to change only what the findings demand. It adds no new facts (researchers are not re-run) and re-emits the whole object so the transport stays one JSON message.

This mirrors the critic-side retry rules in [05](../docs/spec/05-manager.md#in-the-retry-loop) and [06](../docs/spec/06-validator-and-critic.md#the-retry-loop) — the validator's budget is separate from the critic's, but the edit-not-regenerate discipline is the same.

## The template

```text
Your previous issue.json draft FAILED the deterministic validator. It is
structurally invalid and cannot be published until these problems are fixed.

You are the MANAGER. Below is your OWN prior draft, exactly as you emitted it,
followed by the blocking findings the validator caught. Your job is to EDIT this
draft to resolve every finding — you do NOT regenerate it from scratch.

# The rules of this retry (read carefully)

- EDIT the draft, do NOT regenerate it. Change only what the findings below
  demand. Every section that already passed must stay BYTE-FOR-BYTE as it is —
  do not re-rank, re-word, or re-author anything the validator did not flag.
- Add NO new facts. The researchers are not being re-run and you have no web
  access. Work with the facts already in the draft. If a finding cannot be fixed
  by editing what is here (for example an uncited claim you cannot source from
  the draft), DELETE the claim rather than invent a source.
- Fix EVERY finding. The validator collected all problems at once; a second
  failure on a problem it already named wastes the budget.

# The blocking findings to fix

{{blocking_findings}}

# Your prior draft (edit THIS — do not start over)

{{prior_draft_json}}

# Output (read carefully)

Your ENTIRE final message must be EXACTLY ONE JSON object conforming to
issue.json schema v1.0.0 — no markdown fences, no preamble, no trailing
commentary. It is machine-parsed; anything else fails validation. Re-emit the
WHOLE draft with your edits applied, all 14 top-level keys present, stats still
{} (the orchestrator derives every count), and the run block's identifiers
unchanged.
```

---

## Render-time placeholder notes (for `run.py`)

| Placeholder | Source | Notes |
|---|---|---|
| `{{prior_draft_json}}` | the manager's prior `issue.json` draft | indented JSON, emitted verbatim so the manager edits rather than regenerates |
| `{{blocking_findings}}` | the validator's `ValidationResult.blocking` | one `- kind at where: note` line per finding; advisory findings are deliberately withheld — they are the record, not a to-do list |
