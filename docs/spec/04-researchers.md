# 4. Researchers

Read-only agents that report facts. The pivot replaced the six fixed beats with **apertures** — scans defined by `relation-tier × scope` — but the template pattern, the read-only wall, the sourcing rules and the `findings.json` contract survived. Covers the aperture roster, the registry watch, the shared prompt, and the contract.

**Inputs:** `config/programs/<id>.toml`, `config/interests.toml`, `prompts/researcher.md`, the state layers ([03](03-state-and-governance.md)).
**Output:** one `findings.json` per aperture, at `runs/<run_id>/findings/<aperture_id>.json`.
**Consumers:** the manager ([05](05-manager.md)) and — crucially — the critic ([06](06-validator-and-critic.md#the-receipt-rule)).

## One template, N apertures

**Not N prompts.** Apertures differ in **scope**, never in **rules**: trust tiers, citation discipline, read-only expectations and the output contract are identical for all and live exactly once, in `prompts/researcher.md`. Per-aperture scope comes from the program config and is interpolated at render time. `researcher.md` survived the pivot; `beats.toml` is deleted, superseded by `config/programs/` + `config/interests.toml`.

## The aperture roster — what replaced the six beats

The beats died as structure and survive as an **event-type checklist** (readout, deal, regulatory, financing, people) each aperture still sweeps. What a run scans is now derived from the program ([#56](https://github.com/cmengu/Research-Swarm/issues/56)):

| Aperture | Scope | Carries | Count |
|---|---|---|---|
| **Biology scan** | `target` + `moa`, indication-blind | mechanism twins + target twins | 1 per program |
| **Arena scan** | one indication (`indication × line × biomarker`) | setting rivals + benchmark/SOC | N = one per indication |
| **House sweep** | the wider oncology board, aimed | interest-steering, partnership/BD, threat/financing, platform threat, **blind-spot detection** | 1, fixed |

**Agent count is `1 + N + 1`, bounded by config, not by the competitor list.** The biology scan carries the program's biology across every indication; each arena scan carries its patients; the house sweep is descended from the old backstop — one cheap aimed round-up **every run**. Cost is `FIXED + N × (one sonnet arena scan)` ([09](09-orchestrator.md#scaling-to-many-programs)).

**Two evidence streams are two lenses on one scan, not two scans** — a shallow tag on house-sweep findings (partnership/BD vs threat/financing), not a doubling of agents. Discovery is folded into the house sweep, not a separate agent.

Defaults: `model = "sonnet"`, `max_turns = 30` per researcher. Surge runs inherit both unchanged.

## Apertures overlap by design

**Report anyway.** A HER3-DXd squamous readout legitimately belongs to the biology scan (it's a target twin) and the squamous arena scan (it moves that setting). A duplicate costs the manager one merge — trivially detectable on `entity_ids` — while a dropped story costs a critic block or a missed repricing. Two apertures independently finding the same readout is signal, and the overlap is what makes this corpus useful as the critic's receipt pool. Never "leave it for the other aperture."

## The registry watch and the feed set

The pivot added a genuinely new **input class**, not just new source URLs ([source set #51](https://github.com/cmengu/Research-Swarm/issues/51)): **for a program detective, most competitor-program updates are registry facts, not news.**

- **ClinicalTrials.gov v2 API** ([docs](https://clinicaltrials.gov/data-api/api)) is the load-bearing feed: free JSON, no auth, and `lastUpdatePostDate` is a filterable/sortable field. A set of tracked NCT IDs (the program's twins, setting rivals and SOC comparators) is a **standing registry watch**, polled by `lastUpdatePostDate`, feeding a diff the researcher summarizes. A phase transition, a new arm, a quietly-changed endpoint, or a status change to *terminated* shows up here **weeks before** the press release.
- **Emission-ordered feed set:** registry deltas → company IR → **AACR/ASCO/ESMO abstracts** (the embargo calendar drives the surge trigger — [02](02-cadence-and-surge.md#surge-mode)) → peer journals via PubMed. **SEC EDGAR full-text** covers US-listed financing.
- **Named blind spots, carried into the house sweep's blind-spot section:** China-first assets (SDP0505 via CDE/chictr; Akeso/ivonescimab financing via HKEX) are language-gated with no clean free feed — the single largest coverage gap, landing on the pilot's closest competitors. Analyst/broker interpretation is paywalled with no free substitute, and out of scope while the reader is the decision-owner, not an investor.
- **Patents are ruled out of v1** — a record, not a signal (~18-month latency); manual low-cadence enrichment only.

The trust tiers below still apply to whatever a registry diff cites; the registry watch extends the **emission** axis (which feed emits a move first), not the **trust** axis.

## What a researcher is told

The full template is `prompts/researcher.md`; `{{double_brace}}` placeholders are filled by `run.py`.

### Role

> You report FACTS with sources. You do not interpret, editorialize, type a competitor, or argue a worldview — interpretation and the read-through are the manager's job.

### The competitor set and interests — a coverage duty

Every **typed competitor** of the program (from the relation edges) and every **strong-tier interest** (from `config/interests.toml`) whose scope touches the aperture must be explicitly checked each run and recorded in `coverage_notes` either way. The set is rendered compactly — `entity_id · name · relation · note`, one line each. The interest **note** steers what a researcher notices; the tier sets the default bar. A researcher **may propose** a `proposed_relation` on an off-edge find, but **never writes an edge** — typing is the manager's, confirmed by governance.

### The thesis is a lens, not a conclusion

> This is an ATTENTION LENS, not a conclusion. Use it to notice which facts matter. Do NOT argue for or against any stance, do NOT include stance language in your summaries, do NOT tag findings with thesis judgments.

Stances are read fresh from `state/thesis.json` at run time ([03](03-state-and-governance.md#the-propagation-contract)); a dormant slot renders `(no stance seeded)`. Stance text is **never** baked into the template. This is what contains the blast radius of the provisional stances: a lens changes what a researcher *notices*, never what it *claims*.

### The catalyst queue — a standing duty

Active items only (`status` in `pending`/`slipped`). If a researcher finds dated evidence that an item **delivered**, **slipped**, or **died**, it reports a finding with the source and references the item id in `catalyst_refs`. Every status transition requires a citation.

### Sourcing rules — non-negotiable

Tiers:

- **primary** — FDA/EMA, ClinicalTrials.gov, SEC filings, company press releases, PubMed/bioRxiv/medRxiv, conference abstracts
- **trade** — Endpoints News, Fierce Biotech, STAT (free), BioPharma Dive, Reuters — named, staffed publications
- **aggregator** — everything else that repackages reporting

Rules:

1. Every finding carries at least one source with **all four** fields: `url`, `publisher`, `tier`, `published_at`. No source, no finding.
2. **An aggregator can never be the only source.** Chase to the primary/trade origin and cite that. If no origin is found, **still report** the finding but set `unconfirmed: true` and say so.
3. Rumours are reportable from trade-tier outlets, but the summary must say **"rumour"** explicitly.
4. `published_at` must fall inside the coverage window — with the surge carve-out ([02](02-cadence-and-surge.md#the-critics-bar-does-not-move--with-one-fix)).
5. Named publishers only.
6. Paywalled primary: cite the best free secondary, **also** link the paywalled primary with `paywalled: true`, and note "primary paywalled — assess manually".

Rule 2 is the first line of defence against SEO content farms; the critic handles whatever the manager publishes anyway ([06](06-validator-and-critic.md#blocking-findings)).

### Budget

A hard cap of `max_turns` (default 30) tool turns. Sweep the scope broadly first, deepen the most important stories second, reserve final turns for output.

> If you run low, ship what you have with honest `coverage_notes` — **thin is acceptable, empty-by-truncation is not.**

## The contract

One `findings.json` per aperture per run. **Not** throwaway scratch — see [retention](#this-corpus-is-evidence). The shape is v1's, with aperture-scoped fields:

```jsonc
{
  "aperture": "arena_scan:squamous-nsclc",   // biology_scan | arena_scan:<indication> | house_sweep
  "program_id": "hmbd-001",
  "run_id": "run_20260718_0700",
  "coverage_window": {"from": "2026-07-14", "to": "2026-07-18"},
  "quiet": false,
  "findings": [
    {
      "summary": "2-4 sentences, factual, no worldview, no read-through",
      "entity_ids": ["asset_her3_dxd"],       // resolve against state/entities/; [] if none
      "proposed_entity": null,                 // or {name, type, what_it_is} — a discovery candidate
      "proposed_relation": null,               // or a relation enum — a TYPING PROPOSAL, not a write
      "house_lens": null,                      // house_sweep only: partnership_bd | threat_financing
      "registry_delta": null,                  // {nct_id, module, from, to} when sourced from the registry watch
      "sources": [ { "url": "...", "publisher": "...", "tier": "...", "published_at": "...", "paywalled": false } ],
      "catalyst_refs": [],
      "priority_hint": "high | medium | low",  // within-aperture hint, NOT published ranking
      "unconfirmed": false
    }
  ],
  "coverage_notes": { "scope_run": ["..."], "entities_checked": ["..."], "notes": "honest self-assessment" },
  "errors": []
}
```

### Field rules

- **`entity_ids`** — the spine, resolving against `state/entities/` ([03](03-state-and-governance.md#the-entity_id-spine)). Off-roster finds carry `entity_ids: []` and a `proposed_entity`; the manager decides promotion.
- **`proposed_relation`** — a typing *proposal*. Researchers propose; the manager types; governance confirms the edge write. A researcher that writes an edge or types a competitor has broken the contract.
- **`registry_delta`** — present when the finding came from the registry watch, carrying the changed module so the manager can render "status → active, not recruiting" rather than a prose paraphrase.
- **`sources`** — objects, never strings; all four fields plus `paywalled`; at least one per finding.
- **`coverage_notes`** — **always required**, quiet or busy. It makes `quiet: true` falsifiable and exposes thin coverage.

### Why it isn't issue.json-shaped

Researchers report **facts**; the manager authors **interpretation** — including the read-through and the typed relation. So these fields are deliberately **absent**: `read_through`, `thesis_bearing`, `so_what`, published `priority`, section placement. A researcher that emits any of them has broken the contract — and the contract is shaped so there is no field in which it could. `priority_hint` is the one triage hint that crosses, and it is explicitly within-aperture.

## Transport

Read-only is a hard wall: researchers get web search, the registry/SEC feeds, and read tools — **zero writes**, enforced by Claude Code permission flags, not prompt text. A researcher therefore *cannot* persist its own file.

1. **Transport = stdout.** `claude -p` returns exactly one JSON object — no fences, no preamble.
2. **`run.py` is the sole writer** of `runs/<run_id>/findings/<aperture_id>.json`.
3. **Validate at the seam** — `run.py` schema-checks each output immediately; determinism-before-judgment ([01](01-overview.md#3-determinism-before-judgment)) one stage earlier.
4. **One retry** on parse/schema failure, error appended.
5. **Retry exhausted → the aperture fails visibly and the run continues.**

### When an aperture dies

The aperture lands in `sources_and_method.apertures_degraded`, the manager is told which are missing, and the failure is a **declared degradation** (`arena_scan_failed` / `arena_scan_dormant` in [the register](06-validator-and-critic.md#the-degradation-register)). One dead scan must not kill the issue. **The entry is not the render** — every section the dead aperture would have fed carries an inline marker (*"squamous arena coverage unavailable this cycle — scan failed"*), because a reader who never scrolls to Sources & Method reads a thin section as a fact about the world. All apertures dead is a failed-run stub.

A dormant aperture (an indication with no active arena scan this cycle) renders `arena_scan_dormant` — a no-op landscape, not a failure.

### `errors[]` is a different animal

A researcher reporting an unreachable source raises `source_unreachable` (**advisory**, no exemption): a model self-report cannot satisfy the register's mechanical-detection test. A required section empty with only `errors[]` to explain it **blocks**. The China-registry gap surfaces this way — a poll that returns no parseable delta is `source_unreachable`, and the asset is carried at low confidence, not silently.

## This corpus is evidence

`runs/<run_id>/findings/*.json` is a **retained artifact with a critic-input duty**. The critic reads raw findings and enforces the `dropped_story` receipt rule against them ([06](06-validator-and-critic.md#the-receipt-rule)): a blocking `dropped_story` requires an in-window primary/trade URL present *in these files* and cited nowhere in the issue. Retention: **24 runs** ⚑ ([09](09-orchestrator.md#retention)).

---

*Provenance: scan model [#56](https://github.com/cmengu/Research-Swarm/issues/56) (apertures replace beats), source set [#51](https://github.com/cmengu/Research-Swarm/issues/51) (registry-diff input class, feed set, blind spots); template pattern, read-only wall and sourcing rules inherited from v1 [#6](https://github.com/cmengu/Research-Swarm/issues/6).*
