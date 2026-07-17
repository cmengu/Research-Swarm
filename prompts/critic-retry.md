# Manager retry prompt template (critic stage)

Asset for ticket [#35](https://github.com/cmengu/Research-Swarm/issues/35). The Codex critic blocked the manager's draft: at least one finding asserts the digest would mislead a reader about a fact. This is the prompt that hands the draft back so the manager can **edit** it — either fixing the finding, or filing a **sourced rebuttal** for why the critic is wrong.

Like the other prompt files, this is a document ABOUT the template with the template itself fenced inside a single ```text block. `run.py` extracts that fence; these notes stay out of the model's context. Two `{{double_brace}}` placeholders are filled at render time by `render_critic_retry_prompt`: `{{prior_draft_json}}` (the manager's own prior draft) and `{{blocking_findings}}` (exactly the critic's blocking findings, nothing advisory).

## The rules this template exists to enforce

**Edit, do not regenerate** — the same discipline as the validator retry ([05](../docs/spec/05-manager.md#in-the-retry-loop)). Sections the critic did not fault must not silently mutate between rounds. No new facts: the researchers are not re-run and the manager has no web access.

**Rebut once, then comply** ([06](../docs/spec/06-validator-and-critic.md#the-rebuttal-channel)). A manager forced to comply with a false finding silently deletes a true story; a manager free to overrule its own auditor makes the cross-family gate theatre. So the loop splits the difference:

- On a **fresh** finding, the manager may either fix it, or file a `rebuttal` — a sourced argument for why the finding is wrong. It may **not** silently ignore it.
- The critic re-judges each rebuttal on its next pass. It has final say: it marks the rebuttal `withdrawn` (the finding drops) or `reaffirmed` (the finding stands). **The manager never sets `adjudication`.**
- A finding marked **REAFFIRMED** in the list below has already lost its rebuttal round. The manager must **comply** — fix it — not rebut again.

A rebuttal rides on the finding it answers, inside `critic_report.blocking_findings`, so the critic sees it on its next pass and the reader sees it if the dispute survives. It needs `text` and at least one `source` object; an unsourced rebuttal does not count and the finding reads as ignored.

## The template

```text
Your previous issue.json draft was BLOCKED by the CRITIC (Codex, a different model
family). At least one finding says the digest would mislead a reader about a FACT.
The issue cannot publish clean until each finding is resolved.

You are the MANAGER. Below are the blocking findings, then your OWN prior draft,
exactly as you emitted it. Your job is to EDIT this draft — you do NOT regenerate
it from scratch.

# The rules of this retry (read carefully)

- EDIT the draft, do NOT regenerate it. Change only what the findings below
  demand. Every section the critic did not fault must stay BYTE-FOR-BYTE as it is
  — do not re-rank, re-word, or re-author anything not named.
- Add NO new facts. The researchers are not being re-run and you have no web
  access. Work with the facts already in the draft, plus what a rebuttal's own
  sources carry. If a finding cannot be fixed from what is here, soften or DELETE
  the offending claim rather than invent support.
- For each FRESH finding you have a choice:
    1. FIX it — edit the draft so the claim no longer outruns its sources. If you
       fix a finding by removing a claim, record it in
       quiet_this_cycle.critic_catches so the cut leaves a trace.
    2. REBUT it — if you believe the finding is wrong, attach a `rebuttal` to that
       finding inside critic_report.blocking_findings. A rebuttal is
       {"text": "...", "sources": [ <source objects> ]} — a sourced argument, not
       an assertion. You may NOT silently ignore a finding; do one or the other.
- For each finding marked REAFFIRMED below, the critic has already overruled your
  rebuttal. COMPLY: fix it. Do not rebut it a second time.
- Do NOT set `adjudication` on any rebuttal. That is the critic's to set, never
  yours.

# The blocking findings

{{blocking_findings}}

# Your prior draft (edit THIS — do not start over)

{{prior_draft_json}}

# Output (read carefully)

Your ENTIRE final message must be EXACTLY ONE JSON object conforming to issue.json
schema v1.0.0 — no markdown fences, no preamble, no trailing commentary. It is
machine-parsed; anything else fails validation. Re-emit the WHOLE draft with your
edits applied, all 14 top-level keys present, stats still {} (the orchestrator
derives every count), and the run block's identifiers unchanged. Any rebuttal you
file rides in critic_report.blocking_findings[].rebuttal.
```

---

## Render-time placeholder notes (for `run.py`)

| Placeholder | Source | Notes |
|---|---|---|
| `{{prior_draft_json}}` | the manager's prior `issue.json` draft | indented JSON, emitted verbatim so the manager edits rather than regenerates |
| `{{blocking_findings}}` | the critic's surviving `blocking_findings` | one `- kind at where: note` line per finding; a reaffirmed finding gets a COMPLY marker; advisories are deliberately withheld — they are the record, not a to-do list |
