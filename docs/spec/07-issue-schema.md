# 7. issue.json schema v2.0.0

The complete field-level contract for a published issue. This is what the manager emits, the validator checks, the critic judges, and the dashboard renders.

**Version:** `2.0.0`. The top-level noun changed — from a market-wide digest to a **per-program detective** — so this is a major bump, not a delta consolidation. See [the delta log](#delta-log-v100--v200).
**Reference sample:** [`docs/schema/sample-issue-hmbd-001-2026-07-18.json`](../schema/sample-issue-hmbd-001-2026-07-18.json) — a real HMBD-001 issue assembled by hand from public facts (grounded in [`docs/research/program-detective-source-set-2026.md`](../research/program-detective-source-set-2026.md)). Read-throughs are illustrative; facts are real. This document is authoritative where they differ. The v1 sample [`sample-issue-2026-07-16.json`](../schema/sample-issue-2026-07-16.json) is retained as history.

This schema is chartered by [the per-program detective map (#49)](https://github.com/cmengu/Research-Swarm/issues/49) and its resolved children. It **does not re-open the machinery** — validator, critic, degradation register, retry/rebuttal, snapshot immutability all survive from v1 ([06](06-validator-and-critic.md), [08](08-publishing-and-dashboard.md)). What changed is the *product the pipeline emits*, not the pipeline.

## What changed in one paragraph

v1 published because something *happened*; v2 publishes because something happened *to a specific drug program*. The `watchlist` of tracked entities becomes a **typed competitor set** keyed by relation to the program. Every published competitor carries a **read-through** — a required, structured "what this means for us / why it is a competitor" — which is the field that makes the admission rule mechanically checkable. `elsewhere_on_frontier` and `themes_and_signals` collapse into a **house view**: one section of the same digest, at a wider aperture, organized by two lenses (partnership/BD and threat/financing) plus surviving themes and a capped blind-spot list. Indications become **first-class objects**, each carrying its arena (setting rivals + SOC) and a thin **treatment landscape**.

## Design principles

1. **`entity_id` is still the spine.** Unchanged from v1 ([03](03-state-and-governance.md#the-entity_id-spine)). What is new is that facts about an entity live in a **shared global layer** (`state/entities/`) while the *relation* to a program lives on a **per-program edge** (`(program_id × entity_id) → relation + read_through`), per [scaling #59](https://github.com/cmengu/Research-Swarm/issues/59). The issue snapshots both.
2. **The read-through is a field, not prose.** Every published competitor and house item carries a structured `read_through`. Its structured parts (the typed `relation` / `lens`, a non-empty `text`) are validator-checkable; its prose quality is the critic's call. This is the resolution of the admission rule — see [the read-through](#the-read-through) and [question 2](#the-six-questions-this-schema-settles).
3. **The relation is the answer to "why is it a competitor."** It is not state-only bookkeeping — it renders on the page, snapshotted onto every competitor item ([decision 6, #49](https://github.com/cmengu/Research-Swarm/issues/49)).
4. **Sources are objects, never strings.** Unchanged: `{url, publisher, tier, published_at}`. The [source object](#the-source-object) and its tiers are exactly v1's.
5. **`stats` is derived, never authored.** Unchanged. New counts, same rule.
6. **A failed run is the same schema.** Unchanged: `status: "failed"`, empty sections, `failure.stage`.
7. **Every published issue is immutable.** Unchanged, and now load-bearing in a second place: a competitor's *relation* and *read-through* are snapshotted, so a July issue keeps showing how it typed a competitor in July even after the record is corrected.
8. **The program issue and the house view share one schema.** One `issue.json`, one envelope. They share every primitive (source object, read-through, entity spine, the degradation register). They diverge only in item shape, because the house view is organized by lens, not by typed relation ([house view #58](https://github.com/cmengu/Research-Swarm/issues/58)).

## Top level

```jsonc
{
  "schema_version": "2.0.0",
  "issue": { /* identity, program, window, run */ },
  "program": { /* the detective's subject — which drug, which indications */ },
  "headline": { /* the cycle's biggest story for this program */ },
  "stats": { /* derived counts */ },
  "tldr_bullets": [ /* one per main topic */ ],
  "catalyst_queue": { /* read-only snapshot; now also competitor-fed */ },
  "competitors": [ /* typed competitors with news — replaces watchlist */ ],
  "indications": [ /* first-class; each carries its arena + treatment landscape */ ],
  "quiet_this_cycle": { /* no_news, critic_catches, open_threads, dropped_with_receipt */ },
  "newly_discovered": [ /* discovery → promotion proposals — replaces new_on_radar */ ],
  "house_view": { /* the wider-aperture section — replaces elsewhere_on_frontier + themes */ },
  "thesis_updates": [ /* the visible drift log */ ],
  "critic_report": { /* both gates' records */ },
  "sources_and_method": { /* the audit trail — apertures, registry watch, interest list */ }
}
```

## `issue`

```jsonc
"issue": {
  "id": "2026-07-18",                       // the dated issue id
  "program_id": "hmbd-001",                 // NEW — the top-level noun is now the program
  "published_at": "2026-07-18T07:41:00+08:00",
  "coverage_window": {"from": "2026-07-14", "to": "2026-07-18"},
  "run": {
    "run_id": "run_20260718_0700",
    "status": "published | published_uncritiqued | published_with_unresolved_findings | failed",
    "critic_verdict": "pass | pass_with_advisories | blocked | not_run",
    "critic_retries": 1,
    "thesis_version": 3,                     // which thesis version the read-throughs argued
    "interest_list_version": 4,              // NEW — which interest-list version steered the run (#55)
    "models": { "researchers": "...", "manager": "...", "critic": "..." },
    "surge": { "window": "ESMO 2026", "day": 2, "of": 5 }  // ABSENT on a baseline run
  },
  "failure": { "stage": "...", "detail": "..." }   // present only when status == "failed"
}
```

`program_id` is the new join key. Issues are stored per program (`issues/<program_id>/<date>.json`, per [#59](https://github.com/cmengu/Research-Swarm/issues/59)); every program has its own history and its own dropdown ([dashboard IA #61](https://github.com/cmengu/Research-Swarm/issues/61) owns the surface).

`interest_list_version` does for the interest list what `thesis_version` does for the thesis: a read-through's steering is valid only against the interest-list version that argued it. The propagation contract ([03](03-state-and-governance.md#the-propagation-contract)) extends to interests — owner edits win with no carry-over.

`coverage_window.from` still joins to the most recent issue that actually covered a window, per program — a stub does not break the chain ([06](06-validator-and-critic.md#continuity-across-stubs)).

## `program`

New in v2. The detective's subject: what drug this issue is about, and its aperture ([program instance #50](https://github.com/cmengu/Research-Swarm/issues/50)).

```jsonc
"program": {
  "id": "hmbd-001",
  "name": "HMBD-001",
  "sponsor": "Hummingbird Bioscience",
  "modality": "anti-HER3 IgG1 signalling antibody",
  "target": "HER3 (ERBB3)",
  "moa": "signalling_blockade",             // load-bearing scan field — separates target twins from mechanism twins
  "one_line": "...",
  "priority_indications": ["nrg1-fusion", "mcrpc", "mcrc", "sccHN"],
  "clinical_stage": "Phase 1b in squamous NSCLC (chemotherapy backbone, ± cetuximab)",
  "config_source": "config/programs/hmbd-001.toml",
  "aperture": {
    "biology_scan": { "target": "HER3 (ERBB3)", "moa": "signalling_blockade" },
    "arena_scans": ["squamous-nsclc", "nrg1-fusion-solid-tumors"]
  }
}
```

`moa` is a **load-bearing scan field**, not description ([#50](https://github.com/cmengu/Research-Swarm/issues/50)): it is what distinguishes a *target twin* (same target, different MOA) from a *mechanism twin* (same target **and** MOA). HER3-DXd shares HMBD-001's target but not its mechanism, so an ADC's win validates HER3 *expression*, not HMBD-001's *signalling* thesis — a distinction the whole competitor model turns on.

The program is **config, not state** ([#50](https://github.com/cmengu/Research-Swarm/issues/50)): the system may *propose* edits to the aperture as findings, but `run.py` never writes it. Same rule as the interest list.

## The read-through

**This is the central new object in v2**, and the resolution of the admission rule. Every published competitor and house item carries one.

```jsonc
"read_through": {
  "relation": "target_twin",         // program-competitor items — the typed "why it's a competitor"
  // OR
  "lens": "partnership_bd",           // house items — the two lenses (#58)
  "thesis_bearing": "confirms | challenges | neutral",   // program items; feeds the drift engine
  "text": "What this means for HMBD-001, in prose. Required, non-empty.",
  "established_by": "run_20260620_0700"  // the run/issue that first established this field (#54)
}
```

The **typed relation set** ([#50](https://github.com/cmengu/Research-Swarm/issues/50), five relations in two tiers):

| Relation | Tier | Unit | Carries |
|---|---|---|---|
| `mechanism_twin` | program-level (biology) | program | Same target **and** MOA — a true rival to the thesis. Indication-blind. |
| `target_twin` | program-level (biology) | program | Same target, **different** MOA (HER3-DXd, SDP0505). Validates the target, not the mechanism. Indication-blind. |
| `setting_rival` | indication-level (arena) | program | Shares the *patients*, not the biology (ivonescimab). Lives on an indication. |
| `benchmark_soc` | indication-level (arena) | program | The bar the setting is measured against. Line is a property of the benchmark, not the indication. |
| `platform_threat` | **house-level** | **company** | A modality engine that can be re-aimed (the DXd platform). **Company-unit — leaves the program instance and renders in the house view.** |

The **`platform_threat` asymmetry is deliberate** ([#49 standing caution](https://github.com/cmengu/Research-Swarm/issues/49)): its unit is a company, not a program, so it never appears in `competitors[]` — it appears in `house_view.threat_financing` with `relation: platform_threat`.

### The ternary receipt — where an item goes

The admission rule is **ternary** ([scan model #56](https://github.com/cmengu/Research-Swarm/issues/56)): a scanned item lands in exactly one of three places, and **nothing is silently omitted**.

| Disposition | Destination | Meaning |
|---|---|---|
| **has a read-through** | `competitors[]`, an indication `arena`, `newly_discovered[]`, or a `house_view` lens | Admitted with its `read_through`. |
| **capped blind spot** | `house_view.blind_spots` (N=5, ranked) | The system sees it but cannot yet place it; the cap emits an `overflow` receipt if exceeded. |
| **dropped with receipt** | `quiet_this_cycle.dropped_with_receipt[]` | Surfaced, judged out of scope, recorded with a source so a later run does not rediscover it as novel. Feeds the critic's `dropped_story` receipt rule. |

### What the validator checks, and what it does not

The presence and shape of a read-through is **deterministic**, so it belongs to the free validator, not the critic — this is the split that answers "does admission become a blocking check":

- **`missing_read_through` (blocking):** a `competitors[]` / arena / `house_view` item with no `read_through`, or with empty `read_through.text`, or with `relation`/`lens` outside its enum. The admission rule, made mechanical.
- **`untyped_competitor` (blocking):** a `competitors[]` entry whose `relation` is not one of the four program-level relations, or a `platform_threat` placed in `competitors[]` instead of the house view.
- **The prose stays the critic's call.** A read-through that exists but merely restates facts without an argument is `weak_read_through` — **advisory**, mirroring v1's `weak_angle`. The validator checks that the field is there; the critic judges whether it earns its place.

This keeps the [degradation register's test 2](06-validator-and-critic.md#admission-test--all-three-must-hold) satisfied: the trigger ("no read-through field") is mechanically detectable from facts the orchestrator holds, so no exemption ever rests on a model's self-report.

## `headline`

```jsonc
"headline": {
  "title": "...",                    // the cycle's biggest story FOR THIS PROGRAM
  "summary": "2-4 sentences, every claim covered by sources[]",
  "so_what": "The editorial judgment: why this reader — the program's decision-owner — should care today.",
  "entity_refs": ["asset_ivonescimab", "asset_her3_dxd"],
  "confidence": "high | medium | low",
  "sources": [ /* source objects */ ]
}
```

`so_what` is unchanged from v1: **always present, thesis-independent**, distinct from any read-through. The reader is the program's decision-owner ([#49](https://github.com/cmengu/Research-Swarm/issues/49) supersedes v1's investor framing), so `so_what` is written to the one decision they own, not to a market.

## `stats`

Derived by the orchestrator, never authored. New program-scoped counts.

```jsonc
"stats": {
  "competitors_moved": 2,       // competitors[] length
  "competitors_quiet": 1,       // quiet_this_cycle.no_news length
  "newly_discovered": 1,
  "indications_covered": 2,
  "house_items": 3,
  "blind_spots_ranked": 2,
  "sources_cited": 11,
  "critic_catches": 1,
  "previous_issue": "2026-07-11" // null on this program's run #1 — true, not a bootstrap flag
}
```

## `tldr_bullets`

Unchanged shape: `{"text": "...", "entity_refs": ["..."], "priority": "high | medium | low"}`.

## `catalyst_queue`

A **read-only snapshot** of `state/programs/<id>/catalyst-queue.json`, frozen at publication. The v1 snapshot shape and its immutability invariant are **unchanged** ([03](03-state-and-governance.md#the-accountability-invariant), [06](06-validator-and-critic.md#the-validator)) — `first_expected_window` is written once and never edited; `expected_window` revisions must append to `slip_log`; `queue_tamper` blocks on violations.

**What is new:** competitor discovery is now the queue's **feeder** ([competitor record #54](https://github.com/cmengu/Research-Swarm/issues/54)). A competitor's next catalyst does **not** get a separate `next_catalyst` field on the competitor record — it joins the one governed prediction surface, the queue, with `fed_by: "competitor_discovery"`. Items gain `bears_on_thesis_slot` bindings to the program's belief slots.

```jsonc
"catalyst_queue": {
  "snapshot_of": "state/programs/hmbd-001/catalyst-queue.json",
  "recut_at": "2026-07-01",
  "items": [
    {
      "id": "sdp0505_china_bla",
      "asset": "SDP0505",
      "entity_ids": ["asset_sdp0505", "hengrui"],
      "holders": ["Shengdi Pharmaceutical", "Jiangsu Hengrui Pharma"],
      "catalyst": "China BLA submission for SDP0505 in EGFR-TKI-resistant NSCLC",
      "first_expected_window": "2026-Q4",   // IMMUTABLE after creation — validator blocks on change
      "expected_window": "2026-Q4",         // revisable, but only with a slip_log entry
      "window_source": { /* source */ },
      "status": "pending | slipped | delivered | dead",
      "slip_log": [],
      "what_it_would_prove": "thesis-gated — renders the marker if the bound slot is dormant",
      "bears_on_thesis_slot": "her3-target-vs-mechanism",
      "fed_by": "competitor_discovery | scheduled | manual",   // NEW
      "sources": [ /* source objects */ ]
    }
  ]
}
```

## `competitors`

**Replaces `watchlist`.** One entry per typed competitor **with news** this cycle. Competitors without news go to `quiet_this_cycle.no_news`. Only the two program-level relations (`mechanism_twin`, `target_twin`) and — where an entity moved at the biology altitude — appear here; indication-level rivals live inside `indications[].arena`, and `platform_threat` lives in the house view.

```jsonc
{
  "entity_id": "asset_her3_dxd",
  "name": "Patritumab deruxtecan (HER3-DXd)",
  "type": "frontier_asset | big_pharma | china_pharma | startup",
  "holders": ["Daiichi Sankyo", "Merck & Co."],   // companies developing the asset
  "status": "developing | concluded",
  "priority": "high | medium | low",
  "categories": ["trial_readout", "regulatory"],
  "summary": "Factual, sourced.",
  "read_through": { /* REQUIRED — relation, thesis_bearing, text, established_by */ },
  "failure": {               // OPTIONAL — present when the competitor has failed; two-tier (#54)
    "tier": "program_tier | indication_tier",   // program-tier for mechanism/target twins; indication-tier for the affected setting
    "indication": "EGFR-mutant NSCLC",           // present when tier == indication_tier
    "status": "BLA withdrawn May 2025 (HERTHENA-Lung02 OS miss)",
    "archived": true,                            // demote-and-archive, NEVER delete
    "note": "...",
    "established_by": "run_20250520_0700"
  },
  "degradation": null,       // or e.g. {"kind": "china_feed_partial", "marker": "..."}
  "sources": [ /* source objects */ ]
}
```

**Failure is two-tier and archival, never deletion** ([competitor record #54](https://github.com/cmengu/Research-Swarm/issues/54)): a `program_tier` failure demotes the whole entity (a mechanism/target twin that dies), an `indication_tier` failure archives only the affected setting while the entity survives elsewhere — HER3-DXd's withdrawn EGFR-NSCLC BLA is `indication_tier`; the program continues across ~15 other tumour types. This is why the dashboard renders failure **inline as a demoted state, not a separate "failed programs" tab** ([dashboard IA #61](https://github.com/cmengu/Research-Swarm/issues/61), Q3) — failure is per-indication, so a top-level tab is the wrong shape.

The `summary` is machine-authorable fact; the `read_through` is the manager's interpretation — the authorship split ([03 clause 4](03-state-and-governance.md#the-governance-contract)) applied field by field. Facts about the entity are lifted to the shared global layer (`state/entities/<entity_id>.json`); the `read_through` is the per-program edge ([#59](https://github.com/cmengu/Research-Swarm/issues/59)). The issue snapshots both, so a published issue cannot drift from — nor be retroactively rewritten by — a later correction to the record.

## `indications`

New in v2. First-class objects ([#50](https://github.com/cmengu/Research-Swarm/issues/50)): line is a property of the benchmark, not the indication, so an indication holds an arena (its setting rivals + SOC) and a landscape.

```jsonc
{
  "indication_id": "squamous-nsclc",
  "name": "Squamous non-small cell lung cancer",
  "role": "active_arena | priority_indication",
  "program_context": "How HMBD-001 sits in this indication.",
  "arena": {
    "setting_rivals": [ { /* competitor item, relation: setting_rival */ } ],
    "benchmark_soc": [ { /* competitor item, relation: benchmark_soc */ } ]
  },
  "treatment_landscape": { /* see below */ }
}
```

Arena items are ordinary competitor items — same shape as `competitors[]`, with an indication-level `relation` and an optional `line` / `biomarker_subgroup`. An indication with no active arena scan this cycle renders a `arena_scan_dormant` degradation rather than an empty section (arena scans are event-triggered and slow; SOC moves in years).

### `treatment_landscape`

A **thin, manager-authored synthesis over the benchmark records** — not a second store of numbers ([per-indication landscape #57](https://github.com/cmengu/Research-Swarm/issues/57)). Slow state, event-triggered, default no-op via `as_of` / `last_changed`. Keyed **indication × line × biomarker-subgroup**.

```jsonc
"treatment_landscape": {
  "as_of": "2026-06-01",
  "last_changed": "2026-06-01",
  "changed_by": "run_20260601_0700",     // issue-cited: updates append the triggering readout
  "keyed_by": "indication x line x biomarker_subgroup",
  "lines": [
    {
      "line": "1L",
      "biomarker_subgroup": "all-comers",
      "standard_of_care": "Pembrolizumab + platinum/taxane (KEYNOTE-407 regimen)",
      "emerging": [ {"entity_id": "asset_ivonescimab", "note": "read-only view over the catalyst queue"} ],
      "bar_direction": "rising | static | falling — the 'where's the bar heading' narrative",
      "efficacy_source": { /* source */ }   // PRIMARY-ONLY — see below
    }
  ]
}
```

**Benchmark efficacy numbers are primary-source-only — stricter than the general admission bar** ([#57](https://github.com/cmengu/Research-Swarm/issues/57)). Trade press may *flag* a number, never *set* it. The validator's **`landscape_number_unsourced` (blocking)** check enforces this: an efficacy number whose `efficacy_source.tier` is not `primary` blocks. Emerging therapies are a **read-only view** over the catalyst queue and the setting-rival records — never an independent list of numbers to drift.

## `quiet_this_cycle`

```jsonc
"quiet_this_cycle": {
  "no_news": [ {"entity_id": "asset_zeno_her3", "name": "...", "cycles_quiet": 3} ],
  "critic_catches": [ { /* unchanged from v1 — rejections are published, not dropped */ } ],
  "open_threads": [ { /* unchanged from v1 */ } ],
  "dropped_with_receipt": [        // NEW — the third leg of the ternary receipt
    {
      "entity_id": "asset_generic_her2_adc",
      "name": "...",
      "dropped_because": "off-target — HER2, not HER3; no typed relation.",
      "source": { /* source — the receipt the critic's dropped_story rule reads */ }
    }
  ]
}
```

`dropped_with_receipt` records a scanned item the manager chose not to feature, with the source that proves it was seen. This is the mechanical counterpart to the critic's [receipt rule](06-validator-and-critic.md#the-receipt-rule): a dropped item recorded with a well-formed receipt is an honest omission; one that surfaces in a researcher's findings but appears nowhere is a `dropped_story` the critic can block on.

## `newly_discovered`

**Replaces `new_on_radar`.** Newly surfaced entities that competitor discovery ([#53](https://github.com/cmengu/Research-Swarm/issues/53)) proposes to **promote and type** onto the program.

```jsonc
{
  "entity_id": "asset_her3_car_t",
  "name": "...",
  "type": "frontier_asset",
  "priority": "low",
  "categories": ["platform_tech"],
  "what_it_is": "Factual.",
  "development": "Factual, sourced.",
  "proposed_relation": "target_twin",       // the type discovery proposes
  "read_through": { /* REQUIRED — the why-we-care, now structured */ },
  "promotion_proposal": {
    "promote_to_competitors": false,
    "reason": "...",
    "proposes_interest": {"tier": "watching", "note": "..."}   // may propose an interest instead (#55)
  },
  "sources": [ /* source objects */ ]
}
```

Discovery **proposes**; `run.py` executes accepted promotions against `state/programs/<id>/` with a `drift_log` entry, and the system **never writes the interest list or the aperture** — a proposal to `proposes_interest` is a finding the human confirms in the interest editor ([#55](https://github.com/cmengu/Research-Swarm/issues/55)). Same governance as v1's `promotion_proposal`, now spanning two config surfaces.

## `house_view`

**Replaces `elsewhere_on_frontier` and `themes_and_signals`.** One section of the same digest, at a wider aperture — the value stream to the program issue's competitive stream ([house view #58](https://github.com/cmengu/Research-Swarm/issues/58)). The streams are **two lenses (questions), not two bins (source types)**: one entity (Merck) can surface under both.

```jsonc
"house_view": {
  "partnership_bd": [ { /* house item, lens: partnership_bd */ } ],
  "threat_financing": [ { /* house item, lens: threat_financing; platform_threat lives here */ } ],
  "themes_and_signals": [ { /* survives from v1, at house altitude */ } ],
  "blind_spots": {
    "cap": 5,
    "ranked": [
      {
        "rank": 1,
        "blind_spot": "China-first HER3 and squamous assets (CDE/chictr; HKEX financing)",
        "why_it_matters": "...",
        "signal_magnitude": "high | medium | low",
        "mitigation": "..."
      }
    ],
    "overflow": null   // a receipt when more than `cap` blind spots exist — overflow is NEVER silent (#56)
  }
}
```

A **house item** carries a `read_through` with a `lens` (not a `relation`), except a `platform_threat`, which carries **both** `lens: threat_financing` and `relation: platform_threat`. Themes survive here and may carry `proposes_interest` — a theme is exactly where a new house-level interest is born, confirmed by the human in the editor ([#55](https://github.com/cmengu/Research-Swarm/issues/55)).

The **blind-spot list is capped at N=5, ranked by signal magnitude, and overflow is never silent** ([#56](https://github.com/cmengu/Research-Swarm/issues/56)). The validator's **`blind_spot_overflow` (blocking)** check fires when `ranked` exceeds `cap` with no `overflow` receipt. This is the house-level escape valve of the admission rule: an item the system genuinely cannot place is admitted *as a known blind spot*, not dropped and not faked.

## `thesis_updates`

Unchanged in shape from v1 — the visible drift log, only `stance` transitions rendered ([03](03-state-and-governance.md#reader-visibility)).

```jsonc
{
  "change": "amended | added | retired",
  "field": "her3-target-vs-mechanism",   // the belief slot id
  "before": "...",
  "after": "...",
  "triggered_by": ["asset_her3_dxd", "asset_sdp0505"]   // entity_ids whose read-through thesis_bearing accumulated
}
```

**Open, inherited from the map:** whether a program carries its own angle or leans entirely on the house thesis is *not yet specified* ([#49](https://github.com/cmengu/Research-Swarm/issues/49) parks it). v2 does the minimum that does not foreclose either answer: `read_through.thesis_bearing` on program items feeds the same drift engine `research_angle.thesis_impact` fed in v1, and `thesis_updates` renders exactly as before. When the program-thesis question resolves, it slots in here without a schema break.

## `critic_report`

Unchanged machinery ([06](06-validator-and-critic.md)), with the new finding kinds wired in.

```jsonc
"critic_report": {
  "verdict": "pass | pass_with_advisories | blocked | not_run",
  "retries_used": 1,
  "blocking_findings": [ /* unchanged shape; kinds now include missing_read_through-adjacent cases */ ],
  "advisory_findings": [
    {"kind": "weak_read_through", "where": "competitors.asset_sdp0505", "note": "..."}  // NEW advisory kind
  ],
  "validator_report": { "passed": true, "retries_used": 0, "findings": [] }
}
```

The receipt rule, rebuttal channel, and `adjudication`-set-by-critic all survive unchanged. New validator kinds (`missing_read_through`, `untyped_competitor`, `blind_spot_overflow`, `landscape_number_unsourced`) ride in `validator_report.findings`; the new critic advisory `weak_read_through` rides in `advisory_findings`.

## `sources_and_method`

Extended for the aperture scan model ([#56](https://github.com/cmengu/Research-Swarm/issues/56)) and the registry-diff input class ([source set #51](https://github.com/cmengu/Research-Swarm/issues/51)).

```jsonc
"sources_and_method": {
  "apertures_run": [                       // replaces beats_run — apertures, not beats
    {"aperture": "biology_scan", "scope": "target=HER3, moa=signalling_blockade", "status": "ok"},
    {"aperture": "arena_scan", "scope": "squamous-nsclc", "status": "ok"},
    {"aperture": "arena_scan", "scope": "nrg1-fusion-solid-tumors", "status": "dormant"},
    {"aperture": "house_sweep", "scope": "partnership_bd + threat_financing + blind_spots", "status": "ok"}
  ],
  "apertures_degraded": ["arena_scan:nrg1-fusion-solid-tumors"],   // replaces beats_failed
  "registry_watch": {                      // NEW — registry-diff is a first-class input class, not a source URL (#51)
    "input_class": "registry_diff",
    "tracked_nct_ids": ["..."],
    "polled_by": "lastUpdatePostDate",
    "diffs_observed": [ {"source": "clinicaltrials.gov v2", "note": "..."} ],
    "coverage_note": "Near-complete for US/global; partial for China-first (CDE/chictr) — the rank-1 blind spot."
  },
  "source_tier_counts": {"primary": 7, "trade": 3, "aggregator": 1},
  "paywalled_flagged": [ { /* unchanged from v1 */ } ],
  "interest_list": {                       // NEW — the steering knob's snapshot (#55)
    "source": "config/interests.toml",
    "version": 4,
    "last_edited": "2026-06-30",
    "last_edited_by": "owner",
    "rot_status": "fresh | stale",         // stale when last_edited > 6 months — a fail-visible degradation
    "interests": [ {"tier": "strong | watching", "note": "..."} ]
  }
}
```

`apertures_run` replaces `beats_run`; a dormant or failed aperture renders an inline degradation in each section it would have fed, exactly as a failed beat did ([06 degradation register](06-validator-and-critic.md#the-register)) — the register gains an `arena_scan_dormant` / `arena_scan_failed` row in place of `beat_failed`.

`registry_watch` is the one genuinely new *input class*: a set of tracked NCT IDs polled by `lastUpdatePostDate`, feeding a diff the researcher summarizes ([#51](https://github.com/cmengu/Research-Swarm/issues/51)). It is not a source in the tiered sense — the trust tiers still apply to whatever the diff cites — it is a mechanism that surfaces a competitor's move weeks before it becomes news.

`interest_list` snapshots the steering knob. **Rot is a degradation, not silent:** a `last_edited` older than the 6-month default renders `rot_status: stale` as a whole-list, fail-visible marker on the digest ([#55](https://github.com/cmengu/Research-Swarm/issues/55)) — it passes [admission test 2](06-validator-and-critic.md#admission-test--all-three-must-hold) because the trigger is a date the orchestrator holds. Per-interest non-engagement prune proposals live in the editor, not the digest.

## The source object

**Unchanged from v1.** Used identically everywhere in this schema and in `findings.json`:

```jsonc
{
  "url": "https://...",
  "publisher": "Endpoints News",
  "tier": "primary | trade | aggregator",
  "published_at": "2026-07-15",
  "paywalled": false                     // optional; findings.json always sets it
}
```

All four core fields are required — `malformed_source` blocks otherwise. Tier definitions and sourcing rules are unchanged: [04](04-researchers.md#sourcing-rules--non-negotiable). The [source-set doc](../research/program-detective-source-set-2026.md) extends the *emission* axis (which feed emits a move first, how fast) without re-tiering *trust*.

## The six questions this schema settles

The [originating ticket (#60)](https://github.com/cmengu/Research-Swarm/issues/60) asked six things. The answers, in one place:

1. **Which v1 sections survive, are renamed, or die.** See the [delta log](#delta-log-v100--v200). In short: `watchlist` → `competitors`; `new_on_radar` → `newly_discovered`; `elsewhere_on_frontier` + `themes_and_signals` → `house_view`; new `program`, `indications`, `treatment_landscape`; everything else survives with additions.
2. **Where the read-through lives — field or prose. Which is normative?** **A structured field on every item, and the field is normative.** Its structured parts (`relation`/`lens`, non-empty `text`) are validator-checked; the prose is critic-judged. It is both, but the field is the contract — see [the read-through](#the-read-through).
3. **Do the program issue and the house view share a schema or diverge?** **Share one schema, one envelope, all primitives.** They diverge only in item shape, because the house view is organized by lens, not typed relation ([principle 8](#design-principles)).
4. **The delta log.** Below, v1.0.0 → v2.0.0, per-field, per-ticket. Nothing dropped without a row.
5. **Does the relation appear in the issue, or is it state-only?** **On the page.** The relation is snapshotted onto every competitor item as `read_through.relation` — it *is* the answer to "why is it a competitor" ([decision 6, #49](https://github.com/cmengu/Research-Swarm/issues/49)). It is state-resident (the per-program edge, #59) **and** issue-rendered, with snapshot immutability.
6. **What does the validator gain?** Four blocking checks — `missing_read_through`, `untyped_competitor`, `blind_spot_overflow`, `landscape_number_unsourced` — plus the `weak_read_through` critic advisory. The admission rule becomes a *deterministic* gate; quality stays the critic's. See [what the validator checks](#what-the-validator-checks-and-what-it-does-not).

## Delta log: v1.0.0 → v2.0.0

A major bump: the top-level noun changed. Every change traces to a resolved child of [map #49](https://github.com/cmengu/Research-Swarm/issues/49). Nothing is dropped without a row.

| Change | Kind | Source |
|---|---|---|
| `issue.program_id` added; issues stored per program | added | [#49](https://github.com/cmengu/Research-Swarm/issues/49), [#59](https://github.com/cmengu/Research-Swarm/issues/59) |
| `issue.run.interest_list_version` added | added | [interest weight #55](https://github.com/cmengu/Research-Swarm/issues/55) |
| `program` block added (identity + aperture; `moa` load-bearing) | added | [program instance #50](https://github.com/cmengu/Research-Swarm/issues/50) |
| `read_through` object added to every competitor / house / discovery item | added | [#49 decision 6](https://github.com/cmengu/Research-Swarm/issues/49), admission rule; typing from [#50](https://github.com/cmengu/Research-Swarm/issues/50) |
| `watchlist` → `competitors`, typed by relation, read-through required | renamed + reshaped | [#50](https://github.com/cmengu/Research-Swarm/issues/50), [competitor record #54](https://github.com/cmengu/Research-Swarm/issues/54) |
| `watchlist[].research_angle` / `thesis_impact` → `read_through.text` / `thesis_bearing` | renamed | [#50](https://github.com/cmengu/Research-Swarm/issues/50); the angle is now a read-through |
| `competitors[].failure` added — two-tier, archival (demote-and-archive, never delete) | added | [competitor record #54](https://github.com/cmengu/Research-Swarm/issues/54); renders inline per [#61](https://github.com/cmengu/Research-Swarm/issues/61) Q3 |
| `indications[]` added; indication a first-class object with an arena | added | [#50](https://github.com/cmengu/Research-Swarm/issues/50) |
| `treatment_landscape` added per indication; efficacy numbers primary-only | added | [per-indication landscape #57](https://github.com/cmengu/Research-Swarm/issues/57) |
| `new_on_radar` → `newly_discovered` with `proposed_relation` + `proposes_interest` | renamed + extended | [competitor discovery #53](https://github.com/cmengu/Research-Swarm/issues/53), [#55](https://github.com/cmengu/Research-Swarm/issues/55) |
| `elsewhere_on_frontier` → `house_view` (two lenses) | replaced | [house view #58](https://github.com/cmengu/Research-Swarm/issues/58) |
| `themes_and_signals` moves under `house_view`, gains `proposes_interest` | moved + extended | [#58](https://github.com/cmengu/Research-Swarm/issues/58), [#55](https://github.com/cmengu/Research-Swarm/issues/55) |
| `house_view.blind_spots` added (cap N=5, ranked, overflow receipt) | added | [scan model #56](https://github.com/cmengu/Research-Swarm/issues/56), admission rule |
| `platform_threat` relation lives in the house view, company-unit | added | [#50](https://github.com/cmengu/Research-Swarm/issues/50), [#49 standing caution](https://github.com/cmengu/Research-Swarm/issues/49) |
| `quiet_this_cycle.dropped_with_receipt` added (third leg of the ternary receipt) | added | [#56](https://github.com/cmengu/Research-Swarm/issues/56), receipt rule [06](06-validator-and-critic.md#the-receipt-rule) |
| `catalyst_queue.items[].fed_by` added; discovery feeds the queue (no `next_catalyst` field) | added | [competitor record #54](https://github.com/cmengu/Research-Swarm/issues/54) |
| `sources_and_method.beats_run` → `apertures_run`; `beats_failed` → `apertures_degraded` | renamed | [scan model #56](https://github.com/cmengu/Research-Swarm/issues/56) |
| `sources_and_method.registry_watch` added (registry-diff input class) | added | [source set #51](https://github.com/cmengu/Research-Swarm/issues/51) |
| `sources_and_method.interest_list` added, with `rot_status` degradation | added | [interest weight #55](https://github.com/cmengu/Research-Swarm/issues/55) |
| `stats` counts re-derived for the program frame | reshaped | this ticket ([#60](https://github.com/cmengu/Research-Swarm/issues/60)) |
| Validator gains `missing_read_through`, `untyped_competitor`, `blind_spot_overflow`, `landscape_number_unsourced` (blocking) | added | admission rule ([#49](https://github.com/cmengu/Research-Swarm/issues/49)), [#57](https://github.com/cmengu/Research-Swarm/issues/57) |
| Critic gains `weak_read_through` (advisory) | added | mirrors v1 `weak_angle` |
| Degradation register gains `arena_scan_dormant`/`arena_scan_failed`, `china_feed_partial`, `interest_list_stale` | added | [#56](https://github.com/cmengu/Research-Swarm/issues/56), [#51](https://github.com/cmengu/Research-Swarm/issues/51), [#55](https://github.com/cmengu/Research-Swarm/issues/55) |
| **Unchanged:** source object, `catalyst_queue` immutability, `stats`-is-derived, failed-run-is-same-schema, per-issue immutability, `so_what`, `thesis_updates` shape, `critic_report` machinery, `entity_id` spine | retained | [v1 07](https://github.com/cmengu/Research-Swarm/issues/24), machinery not re-opened |

### Two v1 concepts explicitly retained and unmoved

- **`headline.so_what`** stays a first-class, thesis-independent field, now written to the program's decision-owner rather than a market. It is *not* a read-through.
- **`thesis_updates`** renders exactly as v1. Whether a program carries its own thesis is inherited-open ([#49](https://github.com/cmengu/Research-Swarm/issues/49)); v2 does not foreclose it.

---

*Provenance: [map #49](https://github.com/cmengu/Research-Swarm/issues/49) and its resolved children — [#50](https://github.com/cmengu/Research-Swarm/issues/50) (program instance), [#51](https://github.com/cmengu/Research-Swarm/issues/51) (source set), [#53](https://github.com/cmengu/Research-Swarm/issues/53) (discovery), [#54](https://github.com/cmengu/Research-Swarm/issues/54) (competitor record), [#55](https://github.com/cmengu/Research-Swarm/issues/55) (interest weight), [#56](https://github.com/cmengu/Research-Swarm/issues/56) (scan model), [#57](https://github.com/cmengu/Research-Swarm/issues/57) (treatment landscape), [#58](https://github.com/cmengu/Research-Swarm/issues/58) (house view), [#59](https://github.com/cmengu/Research-Swarm/issues/59) (scaling) — consolidated by [#60](https://github.com/cmengu/Research-Swarm/issues/60). Supersedes schema v1.0.0. Machinery ([06](06-validator-and-critic.md), [08](08-publishing-and-dashboard.md)) not re-opened. Dashboard rendering of this schema is [#61](https://github.com/cmengu/Research-Swarm/issues/61).*
