# 3. State, config and governance

The files the system reads and maintains about itself, the one governance contract they all obey, and the `entity_id` spine that links them. The pivot changed the *shape* of state — from one flat watchlist to a program layer, a shared competitor layer, and a steering wheel — but the **governance contract survived unchanged**.

**Config (human-owned):** `config/programs/<id>.toml`, `config/interests.toml`, `config/calendar.toml`.
**State (machine-maintained):** `state/entities/<entity_id>.json`, `state/programs/<id>/` (relation edges, catalyst queue), `state/thesis.json`.
**Consumers:** researchers ([04](04-researchers.md)), the manager ([05](05-manager.md)), the critic ([06](06-validator-and-critic.md)).

## The layers, and why they split

The system has no database and no memory between runs except git. These files *are* its memory. The pivot split the old single `watchlist.json` into three layers so that cost scales with apertures, not programs ([09](09-orchestrator.md#scaling-to-many-programs)):

| Layer | File(s) | Answers | Nature |
|---|---|---|---|
| **Program** | `config/programs/<id>.toml` | Which drug, which indications, which aperture? | Config — human-owned, like cadence |
| **Interest** | `config/interests.toml` | What should the reader lean toward? | Config — the one steering wheel |
| **Shared facts** | `state/entities/<entity_id>.json` | What is true about this competitor? | One record per entity, program-agnostic |
| **Relation edges** | `state/programs/<id>/edges.json` | Why is this entity a competitor *to this program*? | `(program_id × entity_id) → relation + read_through` |
| **Worldview** | `state/thesis.json` | What do we believe? | Falsifiable stances, human-seeded |
| **Predictions** | `state/programs/<id>/catalyst-queue.json` | What do we expect, when, so what? | Rolling, dated, forward-looking |

The **split is the load-bearing idea** ([#59](https://github.com/cmengu/Research-Swarm/issues/59)): a fact about HER3-DXd (its BLA withdrawal) lives once in `state/entities/asset_her3_dxd.json` and lifts to every program that competes with it; the *read-through* — what that fact means for HMBD-001 specifically — lives on the per-program edge. Facts lift to global, which kills silo-drift; read-throughs stay per-program, which is where the detective's judgment lives.

## The governance contract

All layers obey the same five clauses. This is stated **once, here**; per-file sections below only note their carve-outs. **The pivot did not touch these.**

### 1. The orchestrator is the sole machine writer

Only `run.py` writes state, and only during a run. Researchers are read-only at the permission level ([04](04-researchers.md#transport)). The manager returns a draft issue on stdout; it does not touch state. No component can forget to persist, and none can persist something the orchestrator didn't sanction.

### 2. Every machine write is a git commit citing its run

A state write is never silent. Each carries the `run_id` and a reason, and appends to that file's own log — `drift_log` for entities, edges and the queue's roster changes, `drift_log` per belief slot for the thesis, `slip_log` for a queue item's window revisions. The commit and the log are redundant on purpose: the commit is how a human reviews it, the log is how the next run and the critic read it.

### 3. The owner may edit any file at any time; owner edits win, with no carry-over

The owner is not a component of the pipeline and needs no permission from it. When the owner edits a file, the next run reads the new value and argues it — prior interpretations are **not** re-validated, and accumulated tallies do **not** transfer. Owner edits and loop edits stay distinguishable via `last_edited_by`.

> **If the owner changes a stance, an interest, or an aperture and the next published issue still argues the old one, the pipeline is broken.**

### 4. Facts are machine-authored; interpretation is human-authored or explicitly delegated and marked

Applied field by field. The loop may author and maintain any factual field with a source. It may **never** author an interpretive field into a slot no human has seeded — it accumulates candidate evidence and renders a marker instead. Where an agent drafted interpretation under explicit delegation, the field carries a provenance label saying so. See [stance provenance](#stance-provenance).

**Two config surfaces are never machine-written:** a program's aperture (`config/programs/<id>.toml`) and the interest list (`config/interests.toml`). The system *proposes* edits to both as findings; the human confirms and owns every write ([the interest list](#the-interest-list)).

### 5. Per-file immutability carve-outs are validator-enforced

Immutable-once-written fields and append-only logs are not conventions — the deterministic validator blocks on violations, because they are the only tamper-evidence the system has:

| Invariant | File | Enforcement |
|---|---|---|
| `first_expected_window` never changes after creation | catalyst queue | **blocking** validator check |
| `expected_window` changes only with a new `slip_log` entry | catalyst queue | **blocking** validator check |
| A belief's `drift_log` is append-only | thesis | append semantics |
| A competitor record's factual fields correct by **appending**, never overwriting | `state/entities/` | append semantics — corrections cite the run that established them ([#54](https://github.com/cmengu/Research-Swarm/issues/54)) |

## The `entity_id` spine

**Ruling (unchanged): the key is `entity_id` everywhere. Arrays are `entity_ids[]`.**

Stable kebab-case slugs (`merck_co`, `asset_her3_dxd`, `asset_ivonescimab`) link every layer. The pivot **extends** the spine across the split: an `entity_id` names a row in `state/entities/`, and an edge is keyed `(program_id, entity_id)`. The validator's cross-file join check now resolves every `entity_ids[]` reference — in an issue, a findings file, a queue item, or a relation edge — to a `state/entities/` record or a `proposed_entity` ([06](06-validator-and-critic.md#stage-1--the-validator)).

The roster mixes **companies and assets**: `asset_her3_dxd` survives its holders being reorganized; `daiichi_sankyo` is a company-unit entity (the platform-threat unit). Both are valid reference targets.

## `config/programs/<id>.toml` — the program instance

**One detective per drug**, human-owned like cadence ([#50](https://github.com/cmengu/Research-Swarm/issues/50)). The pilot is `hmbd-001`.

```toml
[program]
id = "hmbd-001"
name = "HMBD-001"
sponsor = "Hummingbird Bioscience"
modality = "anti-HER3 IgG1 signalling antibody"
target = "HER3 (ERBB3)"
moa = "signalling_blockade"          # load-bearing scan field — separates target twins from mechanism twins

[[indication]]                        # indications are first-class objects
id = "squamous-nsclc"
role = "active_arena"

[[indication]]
id = "nrg1-fusion-solid-tumors"
role = "priority_indication"

[cadence]
baseline = "monthly"                   # ⚑ per-program dial ([02](02-cadence-and-surge.md#baseline-cadence-the-per-program-dial))
cold_start_lookback_days = 7           # ⚑

# Optional cold-start typing; promoted/typed per #53
seed_competitors = ["asset_her3_dxd", "asset_ivonescimab"]
```

`moa` is a **load-bearing scan field**, not description: it distinguishes a *target twin* (same target, different MOA) from a *mechanism twin* (same target **and** MOA). Indications are **first-class** because line is a property of a benchmark, not of an indication. **Adding a program is one new config file.** The system may propose edits to the aperture as findings but **never writes it** — same rule as the interest list.

## The competitor model — typed relations and per-program edges

A **competitor is a program** (or, for platform threat, a company), and the relation is **typed** ([#50](https://github.com/cmengu/Research-Swarm/issues/50), five relations in two tiers):

| Relation | Tier | Unit | Carries |
|---|---|---|---|
| `mechanism_twin` | program (biology) | program | Same target **and** MOA — the true rival to the thesis |
| `target_twin` | program (biology) | program | Same target, different MOA — validates the target, not the mechanism |
| `setting_rival` | indication (arena) | program | Shares the patients, not the biology |
| `benchmark_soc` | indication (arena) | program | The bar the setting is measured against |
| `platform_threat` | **house** | **company** | A modality engine that re-aims cheaply — company-unit, leaves the instance |

### Four things called "tier"

The word is overloaded four ways, and only one of them means what "tier" implies. This is a domain-language problem before it is a rendering one — the page was ranking things that are not ranked ([#82](https://github.com/cmengu/Research-Swarm/issues/82)):

| Called "tier" | Values | What it actually is |
|---|---|---|
| **Source tier** | `primary` / `trade` / `aggregator` | **A genuine rank.** Primary outranks trade outranks aggregator, always, everywhere. Keeps the word. |
| **Relation tier** | the five above | **A scope, plus an order inside it.** Program-level is about our *biology*, indication-level about our *patients* — different questions, not degrees of the same one. |
| **Interest tier** | `strong` / `watching` | **A bar**, not a rank: it sets the default threshold at which something is worth reporting. |
| **Failure tier** | `program_tier` / `indication_tier` | **A blast radius** — how much of an entity a failure archives ([the failed-competitor afterlife](#the-failed-competitor-afterlife)). |

**The rule: only source tier may be rendered as a ranked ramp.** The other three read as categories, because ranking them asserts something false — that a benchmark is "less" than a target twin, when it answers a different question. On-disk field names are unchanged (renaming them is a schema migration, not a vocabulary fix); the rule governs how they are *read and drawn*.

The relation is **a scan instruction, not a taxonomy exercise**: every ticket that touches it must be able to say what changes about a scan. And **platform threat's unit is a company, not a program** — that asymmetry is deliberate ([#49](https://github.com/cmengu/Research-Swarm/issues/49)).

### Discovery, promotion, and the edge

Any competitor (seeded via `seed_competitors` or discovered by the house sweep) is promoted and typed onto a program by writing a relation edge ([#53](https://github.com/cmengu/Research-Swarm/issues/53)):

```jsonc
// state/programs/hmbd-001/edges.json — one entry per (program, entity)
{
  "entity_id": "asset_her3_dxd",
  "relation": "target_twin",
  "read_through": { "text": "...", "thesis_bearing": "neutral", "established_by": "run_..." },
  "promoted_by": "run_20260620_0700",
  "drift_log": [ /* every retype/refine, append-only */ ]
}
```

The **shared fact** (`state/entities/asset_her3_dxd.json`) is one record; the **read-through** is per-program. Publish dedup follows: one shared fact, a per-program read-through in each program's issue.

### The failed-competitor afterlife

Failure is **two-tier and archival, never deletion** ([#54](https://github.com/cmengu/Research-Swarm/issues/54)): a `program_tier` failure demotes the whole entity (a mechanism/target twin that dies); an `indication_tier` failure archives only the affected setting while the entity survives elsewhere. HER3-DXd's withdrawn EGFR-NSCLC BLA is `indication_tier` — it archives for that indication while the program-tier entity continues across ~15 tumour types. A failed entry is demoted and archived, never deleted; the record's `failure` field carries the tier ([07](07-issue-schema.md#competitors)). A discontinued *own* program authors a lessons retrospective ([09](09-orchestrator.md#scaling-to-many-programs)).

## The interest list

`config/interests.toml` — **the one steering wheel** ([#55](https://github.com/cmengu/Research-Swarm/issues/55)). Weight is a **steering instruction, not a filter dial**.

```toml
version = 4
last_edited = "2026-06-30"
last_edited_by = "owner"

[[interest]]
tier = "strong"          # strong | watching — a sort key + default admission bar, not a score
note = "HER3-DXd encroachment into the squamous setting — the arena-overlap risk."

[[interest]]
tier = "watching"
note = "China-first HER3 ADCs (SDP0505 and successors) despite the feed blind spot."
```

An interest is an **enum tier + a free-text note injected into the manager prompt**. The note steers attention, interpretation and the admission bar; the tier is only a sort key and a default bar (honors tags-not-scores). The knob turns **admission + steering + sort** — never scan depth (the house layer is swept cheaply *every* run) and **never cadence** (orthogonal to the per-program dial).

**Governance carve-out — the steering wheel is human-owned:**

- Source of truth is `config/interests.toml`, but the **edit surface is a separate, non-technical guided runtime tool**, not the static digest: dump opinion → an LLM refines it to tier + note → the human confirms → a governed write. The **static digest stays read-only**; the editor is a separate local surface. Its build-time architecture (how to add an LLM-calling, file-writing runtime without breaking the no-build-step digest) is **deferred to execution**.
- **The system proposes, the human confirms.** The loop may propose **refine / prune / add** as findings (a theme proposes a new interest, a non-engaged interest proposes a prune) — the human confirms and owns every write. The system never auto-writes and never auto-scans the aperture. This **redraws** the map's earlier ban on the system proposing its own interests.
- **Rot is fail-visible.** A `last_edited` older than the ⚑ 6-month default renders a whole-list stale marker on the digest — a declared degradation (`interest_list_stale`, passing [admission test 2](06-validator-and-critic.md#admission-test--all-three-must-hold) because the trigger is a date the orchestrator holds). Per-interest non-engagement prunes surface in the editor, not the digest.

`issue.run.interest_list_version` stamps which version steered a run; the propagation contract applies (owner edit → version bump, no carry-over).

## `state/entities/` — the shared fact layer

One record per `entity_id`, program-agnostic, **a materialized index over the append-only issue archive** ([#54](https://github.com/cmengu/Research-Swarm/issues/54)): every factual field cites the `run_id`/issue that established it, so the record has cross-scan memory but **cannot drift** from published truth — corrections append, never overwrite. No new immutability invariant is needed; append semantics plus issue-citation do the work. A competitor's **next catalyst joins the catalyst queue** — there is no second `next_catalyst` field, and competitor discovery is the queue's feeder.

### The kind split — assets and companies

The layer divides by **kind**, because a molecule and a company are different objects with different fields and conflating them was costing real intelligence:

```
state/entities/companies/<entity_id>.json   the company dossier
state/entities/assets/<entity_id>.json      the asset record
```

An asset record **points at the company that holds it**, so a readout is traversable to its sponsor's balance sheet, and a company's dossier lists its pipeline, so a reader can see what else that company would deprioritise this asset for.

### The dossier record

A company's record is the deep, accumulating half of this layer — built by [the dossier scan](04-researchers.md#the-dossier-scan--the-fourth-aperture), shared across programs, holding **facts only**. Its sections: `identity`, `origin`, `funding`, `pipeline`, `deals`, `people`, `pivots`, `setbacks`.

**Provenance is per field, not per record.** Each section is stored as a fact wrapper, so a record assembled across four runs can be audited claim by claim rather than as one undated blob:

```jsonc
{
  "entity_id": "co_remegen",
  "kind": "company",
  "as_of": "2026-07-19",
  "facts": {
    "funding": {
      "value": { "total_raised": "...", "rounds": [ /* ... */ ] },
      "established_by": "run_20260719_0700"    // the run, and the issue it published in
    }
  },
  "coverage": { "thin_sections": ["origin"] },  // marked at the point of the absence
  "drift_log": [ /* corrections, appended */ ]
}
```

The layer's existing rules carry over unchanged and are what make this safe to accumulate: **corrections append with a drift entry, never overwrite**, and every field cites the run that established it. `as_of` dates the record so a **stale dossier says so** — age is never mistaken for absence of activity.

**Interpretation stays off the record**, at every depth. `read_through` and `priority` are already excluded from this layer because both are program-relative; the dossier extends that ban to the shapes an opinion takes when it cannot be called `read_through` ([04](04-researchers.md#the-dossier-contract)). This is not tidiness — it is the property that lets a second program inherit the facts without inheriting the first program's opinions.

Writes go through the existing state-edit path, so **`run.py` remains the sole writer** (clause 1) and every dossier edit is a git diff citing its run (clause 2).

## `state/thesis.json`

Six belief slots, each an opinionated, falsifiable position that read-throughs argue **against**. **Unchanged in shape from v1.**

```jsonc
{
  "id": "her3-target-vs-mechanism",   // stable kebab-case; never reused after deletion
  "title": "...",
  "stance": "string | null",          // the position. null = DORMANT.
  "confidence": "low|medium|high|null",   // internal only
  "falsifier": "string | null",           // internal only
  "candidate_evidence": [],           // accrues while dormant; cap 25, oldest evicted first
  "drift_log": [],                    // append-only
  "origin": "seed | auto",
  "stance_provenance": "owner | agent_draft_delegated"
}
```

### Reader visibility

Only `stance` reaches the reader, indirectly, as the argument behind a read-through's `thesis_bearing`. `confidence`, `falsifier`, `drift_log`, `candidate_evidence` are internal.

**Dormancy and self-evolution** (per-slot, unchanged): a null-stance slot is dormant and may only append `candidate_evidence`; a slot activates the moment its stance is non-empty, after which the loop may revise it, appending every revision to `drift_log`. An empty thesis **degrades rather than breaks** — thesis-dependent fields render `No thesis seeded — facts only` (`thesis_unseeded` in [the register](06-validator-and-critic.md#the-degradation-register)).

### Stance provenance

| `stance_provenance` | Meaning |
|---|---|
| `owner` | Chosen by the owner in a live session. Authoritative. |
| `agent_draft_delegated` | Drafted by an agent under explicit instruction. **Not owner-endorsed.** Provisional. |

**An agent may not author a stance without one of these.** **⚑ Current state: 2 slots `owner`, 4 `agent_draft_delegated`.** The label is normative — render it wherever the lens is shown.

### The propagation contract

| Clause | Rule |
|---|---|
| **Single source of truth** | `state/thesis.json` is the only place a stance exists. Never copied into a prompt template, an issue, or the dashboard at build time. |
| **Read fresh** | Stances are read at run time. A prompt template that inlines stance text is a bug. |
| **Version stamping** | Every issue records `thesis_version`; its read-throughs are valid only against the version they argued. |
| **On owner edit** | Bump `version`; next cycle argues the new stance with no carry-over. `candidate_evidence` is retained (it is fact, not opinion). |
| **On loop evolution** | Same version bump; `drift_log` records from/to and the trigger. `last_edited_by` distinguishes owner from loop. |

The **same propagation contract governs `config/interests.toml`** — read fresh, version-stamped (`interest_list_version`), owner edits win with no carry-over.

## `state/programs/<id>/catalyst-queue.json`

Per-program now, but **the shape and the accountability invariant are unchanged**. A catalyst is a **dated expectation**: a named readout or decision, expected in a window, that would prove or disprove something. This is the only structure that commits to a claim about the future.

### The accountability invariant

> **`first_expected_window` is written once, at item creation, and is never edited. `expected_window` may be revised; every revision must append to `slip_log`.**

Without it, the re-cut can quietly edit windows forward and every prediction is worthless. With it, the dashboard renders *expected 2026-Q2 · slipped twice · now 2026-Q4*. **No self-grading** — the system records what it said and what happened; the reader judges. Validator checks (`queue_tamper`, all blocking) are in [06](06-validator-and-critic.md#stage-1--the-validator).

**What the pivot added:** items carry `fed_by` (`competitor_discovery | scheduled | manual`) and `bears_on_thesis_slot`. Competitor discovery is the queue's feeder — a competitor's next catalyst becomes a queue item rather than a field on the record.

### Cadence

| Clock | May do | May not do |
|---|---|---|
| **Every run** | status transitions, append `slip_log` | add, retire, or edit `first_expected_window` |
| **Monthly re-cut** | add, retire, resize, re-anchor windows to the conference calendar | edit `first_expected_window` |

Every transition needs a **source**: no source, no transition.

## The per-issue snapshot

Each `issue.json` embeds a **read-only snapshot** of the queue *and* of each competitor's relation + read-through, so a published issue stays truthful about what it expected and how it typed a competitor **at the time**. Rendering a July issue must never show today's edges. Full shapes in [07](07-issue-schema.md).

## Migrating the seeded roster

**Deferred by decision — a migration, not a fresh design.** The v1 seed is a 22–24 entity, five-tier bellwether roster (`acquirer`, `china_supply`, `platform`, `regulator`, `frontier_asset`) built for a market-wide digest. Under the per-program model those tiers no longer map cleanly: a bellwether is not the same object as a typed competitor of HMBD-001. The migration — which seed entities become `state/entities/` records, which become HMBD-001 edges, which retire — is **named open** ([#49](https://github.com/cmengu/Research-Swarm/issues/49)) and is a curation session, not a compilation ruling. The `seed_competitors` list in `config/programs/hmbd-001.toml` is the cold-start path in the meantime.

## What a program's run #1 does

Nothing special. It reads the seeded program config and shared state, finds the calendar unverified and most queue windows null, publishes a full program issue with the stale-calendar marker, and creates the baseline values later continuity checks compare against. No bootstrap flag, no cold-start branch — run #1 falls out of the [backwards search](06-validator-and-critic.md#continuity-across-stubs) for free.

---

*Provenance: pivot children [#50](https://github.com/cmengu/Research-Swarm/issues/50) (program instance), [#53](https://github.com/cmengu/Research-Swarm/issues/53) (discovery/promotion), [#54](https://github.com/cmengu/Research-Swarm/issues/54) (competitor record + failure), [#55](https://github.com/cmengu/Research-Swarm/issues/55) (interest weight), [#59](https://github.com/cmengu/Research-Swarm/issues/59) (scaling/state split). Governance contract, `entity_id` spine, thesis and queue invariants inherited unchanged from v1 ([#24](https://github.com/cmengu/Research-Swarm/issues/24)). Evidence base: [`docs/research/oncology-bellwethers-2026.md`](../research/oncology-bellwethers-2026.md), [`docs/research/program-detective-source-set-2026.md`](../research/program-detective-source-set-2026.md).*
