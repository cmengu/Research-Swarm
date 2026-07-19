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
| **Dossier scan** | one company's whole history, program-agnostic | identity, origin, funding, pipeline, deals, people, **pivots, setbacks** | 0..M, **not every cycle** |

**The cycle agent count is `1 + N + 1`, bounded by config, not by the competitor list.** The biology scan carries the program's biology across every indication; each arena scan carries its patients; the house sweep is descended from the old backstop — one cheap aimed round-up **every run**. Cost is `FIXED + N × (one sonnet arena scan)` ([09](09-orchestrator.md#scaling-to-many-programs)).

**Dossier scans sit outside that count** and are the reason it stays `1 + N + 1`: they are a function of **state**, not config, so they are planned separately ([below](#the-dossier-scan--the-fourth-aperture)) and most cycles plan none.

**Two evidence streams are two lenses on one scan, not two scans** — a shallow tag on house-sweep findings (partnership/BD vs threat/financing), not a doubling of agents. Discovery is folded into the house sweep, not a separate agent.

Defaults: `model = "sonnet"`, `max_turns = 30` per researcher. Surge runs inherit both unchanged.

## Apertures overlap by design

**Report anyway.** A HER3-DXd squamous readout legitimately belongs to the biology scan (it's a target twin) and the squamous arena scan (it moves that setting). A duplicate costs the manager one merge — trivially detectable on `entity_ids` — while a dropped story costs a critic block or a missed repricing. Two apertures independently finding the same readout is signal, and the overlap is what makes this corpus useful as the critic's receipt pool. Never "leave it for the other aperture."

## The dossier scan — the fourth aperture

The three cycle apertures answer *what moved in this window*. None answers *who this company is*. A competitor surfacing for the third time arrived with no more history attached than the first time, so every read-through was argued from a standing start and the understanding that should compound lived only in the reader's head.

A **dossier** is a deep, accumulating, program-agnostic record of a **company**, built once and refreshed slowly, that every future read-through argues from. It answers *who they are*; it deliberately does **not** answer *what they mean for us* — that stays on the per-program relation edge ([03](03-state-and-governance.md#the-competitor-model--typed-relations-and-per-program-edges)). A dossier is shared across programs; a read-through is not. **This is the whole reason interpretation is banned from the record**: a second program must inherit the facts without inheriting the first program's opinions.

**It is modelled as an aperture, not a new stage**, so it inherits the existing fan-out, transport, validation seam, degradation handling and cost accounting unchanged. It differs from the other three in exactly two ways, and both are load-bearing.

### It is exempt from the coverage window

Every cycle aperture is bounded by the run's window. A dossier scan is not, because its subject is history — the same seven-day window that once discarded a $1.1B platform acquisition would truncate a company's founding story.

The exemption lives in the aperture's own definition (`window_exempt`), not as an `if kind == "dossier_scan"` branch in the prompt. On the wire it is declared, not omitted: a dossier payload carries **`"coverage_window": null`**.

> **A declared null is auditable; an omission is indistinguishable from a model forgetting the field.** This is why the envelope wins over the record — see [the contract](#the-dossier-contract) below.

### It is not scheduled per cycle

Three triggers, each recorded **on** the aperture so the audit trail can answer "why was this cost spent" without re-deriving the decision:

| Trigger | Fires when | `trigger` |
|---|---|---|
| **First sighting** | a company enters the roster with no dossier | `first_sighting` |
| **Refresh due** | the record's `as_of` is older than the dial | `refresh_due` |
| **Material event** | an acquisition, discontinuation or equivalent lands | `material_event` |

The refresh dial is **⚑ 91 days** (one quarter) — a stated default, flippable, not an open question. It is coarse by design, so calendar-exact quarter arithmetic would be false precision.

**Discovery feeds the roster.** A newly discovered competitor queues a dossier scan, so the roster deepens automatically as it widens rather than being bounded by what a human remembered to seed.

### Cost is capped, and the cap is measured by the orchestrator

History search is unbounded by nature, so each dossier scan carries an explicit ceiling — ⚑ `max_turns=24`, `max_sources=40`, `max_usd=4.0`. Exceeding it degrades with a receipt (`dossier_scan_cost_capped`, [the register](06-validator-and-critic.md#the-register)) rather than truncating silently.

**The cap reads the transport envelope — `num_turns` and `total_cost_usd`, which the orchestrator parses itself — never a spend figure the model reports about itself.** A model self-report can never satisfy [admission test 2](06-validator-and-critic.md#admission-test--all-three-must-hold), which requires the trigger to be mechanically detectable from facts the *orchestrator* holds. An earlier build read a `spend` field off the model's own output; that field did not exist, so the cap was dead code that never fired.

**A failed, capped or dormant dossier scan degrades the run and never fails it.** Background gathering is subordinate to the cycle's intelligence — the same rule as [a dead cycle aperture](#when-an-aperture-dies), and for the same reason.

### The dossier contract

One contract governs **all** model output, so a dossier payload validates at the same seam as every other aperture's, in the same envelope:

```jsonc
{
  "aperture": "dossier_scan:co_remegen",
  "program_id": "hmbd-001",
  "run_id": "run_20260719_0700",
  "coverage_window": null,        // DECLARED null — the window exemption, on the wire
  "quiet": false,
  "findings": [ /* ...as any other aperture... */ ],
  "dossier": { /* the record — sections below */ },
  "coverage_notes": { "...": "..." },
  "errors": []
}
```

The record itself carries identity, origin, funding, pipeline, deals, people, **pivots** and **setbacks**, plus a `coverage` block naming its thin sections.

**`pivots[]` and `setbacks[]` are the differentiated fields** and are prompted for explicitly rather than left to emerge. Identity and funding are table stakes a vendor will sell you. What a company *said* it would do versus what it then did is sold nowhere, because it is an argument assembled over time — the same asymmetry as the read-through: **the fact is cheap, the argument is the asset.** A vendor feed would satisfy the identity, funding and deal questions and none of the strategy ones.

**Interpretation is rejected at every depth of the record** — a `threat_level` on a funding round, a `so_what` on a pivot, an `implication` on a setback. These are the shapes an opinion takes when it is not allowed to be called `read_through`.

**Source order**, chosen for value per unit of effort: primary filings first (SEC EDGAR full-text; HKEX and equivalent for the China-listed names), then ClinicalTrials.gov sponsor history, patent assignments, company press archives, conference abstract archives. The trust tiers [below](#sourcing-rules--non-negotiable) apply unchanged — a filing outranks a trade item — as does the ternary receipt: a dossier claim that cannot be sourced is **omitted with a receipt**, never quietly dropped.

**The China gap is surfaced, not inherited.** Several of the most important competitors are China-listed, already the system's rank-1 blind spot. A dossier assembled from partial sources **marks its thin sections at the point of the absence**, so a sparse dossier reads as unmeasured rather than as a small company. Whether to buy that coverage is a spend decision for the owner; the spec makes the gap visible, it does not close it.

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

*Provenance: scan model [#56](https://github.com/cmengu/Research-Swarm/issues/56) (apertures replace beats), source set [#51](https://github.com/cmengu/Research-Swarm/issues/51) (registry-diff input class, feed set, blind spots); template pattern, read-only wall and sourcing rules inherited from v1 [#6](https://github.com/cmengu/Research-Swarm/issues/6); the dossier scan from [#92](https://github.com/cmengu/Research-Swarm/issues/92) (the fourth aperture, the window exemption, the cost cap measured by the orchestrator).*
