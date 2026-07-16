# 4. Researchers

Six parallel read-only agents that report facts. Covers the beat roster, the shared prompt template, the `findings.json` contract, and the transport that read-only forces.

**Inputs:** `config/beats.toml`, `prompts/researcher.md`, the three state files ([03](03-state-and-governance.md)).
**Output:** one `findings.json` per beat, persisted at `runs/<run_id>/findings/<beat_id>.json`.
**Consumers:** the manager ([05](05-manager.md)) and — crucially — the critic ([06](06-validator-and-critic.md#the-receipt-rule)).

## One template, six beats

**Not six prompts.** Beats differ in **scope**, never in **rules**: trust tiers, citation discipline, read-only expectations and the output contract are identical for all six and live exactly once, in `prompts/researcher.md`. Per-beat values come from `config/beats.toml` and are interpolated at render time.

Adding a seventh beat is a `[[beat]]` block. No code change, no new prompt, no rule drift between beats.

## The beat roster

Five narrow beats plus a catch-all backstop.

| `id` | Name | Centre of the beat |
|---|---|---|
| `ma_dealmaking` | Pharma M&A & dealmaking | The deal itself: who bought or licensed what, for how much, on what terms. Trade-reported rumours in scope. |
| `startup_frontier` | Oncology startup frontier | Novelty, not size: stealth emergences, first-in-human entries, platform debuts, modality-signalling rounds. |
| `clinical_scientific` | Clinical & scientific developments | What the evidence now says: readouts, publications, preprints, registry changes, safety signals. |
| `policy_regulation` | Policy & regulation | The rule and the ruling: FDA/EMA decisions, CRLs, AdComms, guidance, CMS coverage and pricing. |
| `incumbent_moves` | Incumbent moves & new entrants | Strategic posture: reprioritisations, kills, restructurings, R&D leadership, capacity bets, outside entrants. |
| `backstop` | Biotech-wide catch-all sweep | Precisely what the five narrow beats miss — the story nobody's query was shaped to find. Breadth over depth. |

Each beat carries a `charter` (its scope prose), `seed_angles` (starting directions, explicitly *not* exhaustive), and optional `notes` for beat-specific discipline. Three notes are load-bearing:

- **`ma_dealmaking`** — label rumour vs confirmed in every deal summary. A trade-reported rumour and a signed agreement are different facts.
- **`clinical_scientific`** — distinguish a **readout** (topline results disclosed) from an **abstract title** (presentation scheduled, data not public). They are routinely conflated in coverage and are very different facts.
- **`backstop`** — do *not* self-censor as "probably covered by another beat". Duplication is the point of the backstop. The high-priority coverage duty does not apply; sweep by story size instead.

Defaults: `model = "sonnet"`, `max_turns = 30` per researcher. Surge runs inherit both unchanged.

## Beats overlap by design

**Report anyway.** A Pfizer acquisition of an oncology startup legitimately belongs to three beats. A duplicate costs the manager one merge — trivially detectable on `entity_ids` — while a dropped story costs a critic block or a missed repricing. Two beats independently finding the same readout is signal, and the overlap is what makes this corpus useful as the critic's receipt pool.

Never "leave it for the other beat."

## What a researcher is told

The full template is `prompts/researcher.md`; `{{double_brace}}` placeholders are filled by `run.py`. Its structure:

### Role

> You report FACTS with sources. You do not interpret, editorialize, or argue a worldview — interpretation is the manager's job.

### The watchlist roster — a coverage duty

Every **high-priority** entity whose scope touches the beat must be explicitly checked each run and recorded in `coverage_notes` either way. The roster is rendered compactly — `entity_id · name · tier · priority · watch_for`, one line each.

`why_tracked` is **deliberately excluded** from what researchers see: it is a summary, and summaries are the manager's job. `watch_for` is what makes the duty actionable.

### The thesis is a lens, not a conclusion

> This is an ATTENTION LENS, not a conclusion. Use it to notice which facts matter and deserve deeper chasing. Do NOT argue for or against any stance, do NOT include stance language in your summaries, do NOT tag findings with thesis judgments.

Stances are read fresh from `state/thesis.json` at run time and rendered per slot as `id · title · stance` plus `stance_provenance`. Four of six stances are provisional (`agent_draft_delegated`), and a lens the reader knows is provisional is safer than one presented as settled.

**Stance text must never be baked into the template** — that would break the propagation contract ([03](03-state-and-governance.md#the-propagation-contract)). A dormant slot renders `(no stance seeded)`.

This is what contains the blast radius of the provisional stances: a lens changes what a researcher *notices*, never what it *claims*.

### The catalyst queue — a standing duty

Active items only (`status` in `pending` or `slipped`; `delivered` and `dead` are terminal and not chased). If a researcher finds dated evidence that an item **delivered**, **slipped**, or **died**, it reports a finding with the source and references the item id in `catalyst_refs`.

Every status transition requires a citation, so a transition reported without a source cannot ship.

### Sourcing rules — non-negotiable

Tiers:

- **primary** — FDA/EMA, ClinicalTrials.gov, SEC filings, company press releases, PubMed/bioRxiv/medRxiv
- **trade** — Endpoints News, Fierce Biotech, STAT (free), BioPharma Dive, Reuters and peers — named, staffed publications
- **aggregator** — everything else that repackages reporting

Rules:

1. Every finding carries at least one source with **all four** fields: `url`, `publisher`, `tier`, `published_at`. A finding with no source does not exist.
2. **An aggregator can never be the only source.** Chase what you find on an aggregator to its primary or trade origin and cite that. If no origin can be found, **still report the finding** but set `unconfirmed: true` and say so in the summary. Do not silently drop it; do not let it pass as solid.
3. Rumours (deal talk sourced to "people familiar with the matter") are reportable from trade-tier outlets, but the summary must say **"rumour"** explicitly.
4. `published_at` must fall inside the coverage window — with the surge carve-out ([02](02-cadence-and-surge.md#the-critics-bar-does-not-move--with-one-fix)): during a conference window, anything published within the *conference* window is fair game even if outside this run's one-day coverage window.
5. Named publishers only. If you cannot identify the publisher, it is not citable.
6. Paywalled primary (STAT+, Endpoints premium, PitchBook, Evaluate): cite the best free secondary coverage, **also** link the paywalled primary with `paywalled: true`, and note "primary paywalled — assess manually" in the summary.

Rule 2 is the system's first line of defence against SEO content farms — an earlier research pass caught three fabricated "facts" all traceable to one aggregator. Chase-to-origin catches them upstream; the critic handles whatever the manager publishes anyway ([06](06-validator-and-critic.md#blocking-findings)).

### Budget

A hard cap of `max_turns` (default 30) tool turns. Sweep the charter broadly **first**, deepen the most important stories **second**, reserve final turns for emitting output.

> If you run low, ship what you have with honest `coverage_notes` — **thin is acceptable, empty-by-truncation is not.**

## The contract

One `findings.json` per beat per run. It is **not** throwaway scratch — see [retention](#this-corpus-is-evidence).

```jsonc
{
  "beat": "ma_dealmaking",              // must match a beat id in config/beats.toml
  "run_id": "2026-07-16-mon",
  "coverage_window": {"from": "2026-07-13", "to": "2026-07-16"},
  "quiet": false,                        // true only when findings is empty
  "findings": [
    {
      "summary": "2-4 sentences, factual, no worldview",
      "entity_ids": ["merck", "asset_daraxonrasib"],   // watchlist entity_ids; [] if none
      "proposed_entity": null,           // or {name, type, what_they_do} — a radar candidate
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
    "notes": "honest self-assessment of coverage"
  },
  "errors": []                           // non-fatal problems, as strings
}
```

### Field rules

- **`entity_ids`** — the spine, resolving against `state/watchlist.json` → `entities[].entity_id` ([03](03-state-and-governance.md#the-entity_id-spine)). The roster mixes companies and **assets** (`asset_daraxonrasib`); both are valid refs. A finding about something off-roster carries `entity_ids: []` and a `proposed_entity`; the manager decides whether it becomes a radar entry with a promotion proposal.
- **`sources`** — objects, never strings. All four fields required, plus `paywalled`. At least one per finding.
- **`unconfirmed`** — set when an aggregator was the only traceable source. The finding still ships to the manager; it just can't pass as solid.
- **`catalyst_refs`** — how queue status transitions get their mandatory citation. A finding referencing a queue item *is* the evidence a `delivered`/`slipped` transition needs.
- **`coverage_notes`** — **always required**, quiet or busy. It is the difference between "these findings are everything" and "these findings are what one query surfaced". It makes `quiet: true` falsifiable, grounds the manager's `tracked_quiet` stat, and exposes thin coverage that would otherwise be invisible.

### Why it isn't issue.json-shaped

Researchers report **facts**; the manager authors **interpretation**. Section-shaped researcher output would invite the manager to paste rather than synthesize, and the manager is the only writer.

So these fields are deliberately **absent**: `thesis_impact`, `research_angle`, `so_what`, priority-as-published, section placement. A researcher that emits a stance has broken the contract — and the contract is shaped so there is no field in which it could.

`beat_priority` is the one triage hint that crosses, and it is explicitly a *within-beat* hint, not published ranking. The manager still ranks.

## Transport

Read-only is a hard wall: researchers get web search and read tools, **zero writes**, enforced by Claude Code permission flags rather than prompt text. A researcher therefore *cannot* persist its own file — which settles the transport question.

1. **Transport = stdout.** `claude -p` returns the final message; the prompt requires it to be exactly one JSON object — no fences, no preamble, no trailing commentary.
2. **`run.py` is the sole writer** of `runs/<run_id>/findings/<beat_id>.json`. Persistence cannot be forgotten by an agent.
3. **Validate at the seam.** `run.py` schema-checks each researcher's output immediately — determinism-before-judgment ([01](01-overview.md#3-determinism-before-judgment)) applied one stage earlier, where it costs nothing.
4. **One retry** on parse or schema failure, with the error appended: *"your previous output failed validation: `<error>` — re-emit valid JSON only."*
5. **Retry exhausted → the beat fails visibly and the run continues.**

### When a beat dies

The beat lands in `sources_and_method.beats_failed`, the manager is told which beats are missing, and the failure is a **declared degradation** (`beat_failed` in [the register](06-validator-and-critic.md#the-degradation-register)). One dead researcher must not kill the Monday issue. All six dead is a failed-run stub.

**The `beats_failed` entry is not the render.** Every section the dead beat would have fed carries an **inline marker** — *"M&A coverage unavailable this cycle — beat failed"* — because a reader who never scrolls to Sources & Method reads a thin section as a fact about the world ("a quiet week for deals") rather than as an absence. `beats_failed` serves the audit trail and the critic; the inline marker serves the reader.

### `errors[]` is a different animal

A researcher reporting an unreachable source raises a `source_unreachable` **advisory** and earns **no exemption**. A model self-report cannot satisfy the register's mechanical-detection test — the system cannot tell "FDA published nothing" from "FDA was unreachable". A required section left empty with only `errors[]` to explain it **blocks**. See [ruled out](06-validator-and-critic.md#ruled-out--deliberately-not-degradations).

## This corpus is evidence

`runs/<run_id>/findings/*.json` is a **retained artifact with a critic-input duty**, not a temp file.

The critic reads raw findings as one of its five inputs and enforces the `dropped_story` receipt rule against them: a blocking `dropped_story` requires an in-window primary/trade URL present *in these files* and cited nowhere in the issue. That makes this corpus the evidentiary record of what the pipeline **found and then lost**.

Retention: **24 runs** ⚑. Rationale and the surrounding storage policy: [09 — Orchestrator](09-orchestrator.md#retention).

---

*Provenance: ticket [#6](https://github.com/cmengu/Research-Swarm/issues/6); roster per capture decision #4; read-only wall per capture decision #14.*
