# findings.json — researcher output contract (v0.1.0)

Asset for ticket [#6](https://github.com/cmengu/Research-Swarm/issues/6). This is what one researcher hands back; [`issue.json`](README.md) is what the manager hands back. They are deliberately different shapes.

Template that produces it: [`prompts/researcher.md`](../../prompts/researcher.md). Beat roster: [`config/beats.toml`](../../config/beats.toml).

## What this is

One `findings.json` per beat per run, persisted by `run.py` at `runs/<run_id>/findings/<beat_id>.json`.

It is **not** throwaway scratch. The critic rubric ([#7](https://github.com/cmengu/Research-Swarm/issues/7)) reads raw researcher findings as one of its five inputs and enforces the `dropped_story` receipt rule against them: a blocking `dropped_story` requires an in-window primary/trade URL present *in these files* and cited nowhere in the issue. That makes this corpus the evidentiary record of what the pipeline found and then lost — a first-class artifact with a retention duty.

## Why it isn't issue.json-shaped

Researchers report **facts**; the manager authors **interpretation**. Section-shaped researcher output would invite the manager to paste rather than synthesize, and the manager is the only writer (CAPTURE #3).

So these fields are deliberately **absent** from findings: `thesis_impact`, `research_angle`, `so_what`, priority-as-published, section placement. That is the same field-by-field authorship rule the thesis ([#5](https://github.com/cmengu/Research-Swarm/issues/5)) and catalyst queue ([#17](https://github.com/cmengu/Research-Swarm/issues/17)) already use: facts are machine-authored, interpretation is thesis-gated. A researcher that emits a stance has broken the contract.

`beat_priority` is the one triage hint that crosses — explicitly a *within-beat* hint, not published ranking. The manager still ranks.

## The contract

```jsonc
{
  "beat": "ma_dealmaking",              // must match a beat id in config/beats.toml
  "run_id": "2026-07-16-mon",
  "coverage_window": {"from": "2026-07-13", "to": "2026-07-16"},
  "quiet": false,                        // true only when findings is empty
  "findings": [
    {
      "summary": "string, 2-4 sentences, factual",
      "entity_ids": ["merck", "asset_daraxonrasib"],   // watchlist ids; [] if none
      "proposed_entity": null,           // or {name, type, what_they_do} — radar candidate
      "sources": [
        {
          "url": "https://...",
          "publisher": "Endpoints News",
          "tier": "primary | trade | aggregator",
          "published_at": "2026-07-15",
          "paywalled": false
        }
      ],
      "catalyst_refs": [],               // catalyst-queue item ids this finding bears on
      "beat_priority": "high | medium | low",
      "unconfirmed": false               // true = no primary/trade origin found
    }
  ],
  "coverage_notes": {
    "angles_run": ["merck oncology acquisition", "..."],
    "entities_checked": ["merck", "fda_oce"],
    "notes": "string — honest self-assessment of coverage"
  },
  "errors": []                           // non-fatal problems, as strings
}
```

### Field rules

- **`entity_ids`** — the spine, resolving against `state/watchlist.json` → `entities[].id` (schema note 1 in [README](README.md)). Same referencing convention the catalyst queue already uses, so the validator's existing `dangling_entity` check covers findings for free. Note the roster mixes companies and **assets** (`tier: frontier_asset`, e.g. `asset_daraxonrasib`) — both are valid refs. A finding about something off-roster carries `entity_ids: []` and a `proposed_entity`; the manager decides whether it becomes a `new_on_radar` entry with a `promotion_proposal`.
- **`sources`** — objects, never strings, all four fields required (+ `paywalled`). At least one per finding.
- **`unconfirmed`** — set when an aggregator was the only traceable source. The finding still ships to the manager; it just can't pass as solid. See sourcing rule 2 in the template.
- **`catalyst_refs`** — how every-run queue status transitions get their mandatory citation: a finding referencing a queue item is the evidence a `delivered`/`slipped` transition needs.
- **`coverage_notes`** — always required, quiet or busy. Makes `quiet: true` falsifiable, grounds the manager's `tracked_quiet` stat, and exposes thin coverage that would otherwise be invisible.

## Transport and validation

Read-only is a hard wall (CAPTURE #14): researchers get web search and read tools, **zero writes**. A researcher therefore cannot persist its own file.

1. **Transport = stdout.** `claude -p` returns the final message; the prompt requires it to be exactly one JSON object, no fences, no preamble.
2. **`run.py` is the sole writer** of `runs/<run_id>/findings/<beat_id>.json`. Persistence can't be forgotten by an agent.
3. **Validate at the seam.** run.py schema-checks each researcher's output immediately — the deterministic-validator-runs-free principle ([#7](https://github.com/cmengu/Research-Swarm/issues/7)) applied one stage earlier, where it costs nothing.
4. **One retry** on parse/schema failure, with the error appended to the prompt: *"your previous output failed validation: `<error>` — re-emit valid JSON only."*
5. **Retry exhausted → the beat fails visibly, the run continues.** The beat lands in `sources_and_method.beats_failed`, the manager is told which beats are missing, and the failed beat is a **declared degradation** — registered as `beat_failed` in the [degradation register](../critic-rubric.md#the-register) (#23), which is its single home; this document references that declaration rather than making its own. It can explain a thin section to the critic without blocking. One dead researcher must not kill the Monday issue; all six dead is a failed-run stub (existing pattern, `run.status: "failed"`).

   **The `beats_failed` entry is not the render** (#23). Every section the dead beat would have fed carries an **inline marker** — *"M&A coverage unavailable this cycle — beat failed"* — because a reader who never scrolls to Sources & Method reads a thin section as a fact about the world ("a quiet week for deals") rather than as an absence. `beats_failed` serves the audit trail and the critic; the inline marker serves the reader.

   **`errors[]` is a different animal.** A researcher reporting an unreachable source raises a `source_unreachable` **advisory** and earns **no exemption** — a model self-report cannot satisfy the register's mechanical-detection test. A required section left empty with only `errors[]` to explain it **blocks**.

## Beat overlap

Beats overlap by construction and researchers are told to **report anyway** — a Pfizer acquisition of an oncology startup legitimately belongs to three beats. A duplicate costs the manager one merge (trivially detectable on `entity_ids`); a dropped story costs a critic block or a missed repricing. Two beats independently finding the same readout is signal, and it is what makes this corpus useful as the critic's receipt pool.

## Consequences for the spec

- `runs/<run_id>/findings/*.json` is a **retained** artifact with a critic-input duty, not a temp file. Retention policy (how many runs deep) is unresolved. Note it is no longer simply "the current run plus the previous issue": #23 binds continuity to the most recent issue *carrying* the compared field, so a run of stubs makes the lookback unbounded in principle. The floor rides with spec compilation.
- Adding a beat is a `beats.toml` block; the contract is beat-agnostic.
- **Entity-key naming is inconsistent across assets and needs one ruling at spec compilation.** `watchlist.json` keys entities as **`id`**; `issue.json` calls the same thing **`entity_id`**; the catalyst queue and this contract reference them as **`entity_ids[]`**. Referencing is consistent (`entity_ids[]` everywhere); the *definition* key is not. Found while validating this contract against the real seeded files — the roster render spec here was initially written against the issue.json vocabulary and would have interpolated `type` and `categories` fields that do not exist on any watchlist entity. Rides with spec compilation rather than reopening [#3](https://github.com/cmengu/Research-Swarm/issues/3)/[#4](https://github.com/cmengu/Research-Swarm/issues/4).
