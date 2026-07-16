# 3. State files and governance

The three files the system maintains about itself, the one governance contract they all obey, and the `entity_id` spine that links them.

**Files:** `state/watchlist.json`, `state/thesis.json`, `state/catalyst-queue.json`.
**Consumers:** researchers ([04](04-researchers.md)), the manager ([05](05-manager.md)), the critic ([06](06-validator-and-critic.md)).

## Why these three exist

The system has no database and no memory between runs except git. These files *are* its memory:

| File | Answers | Nature |
|---|---|---|
| `watchlist.json` | **What** do we watch? | Standing roster of subscription addresses |
| `thesis.json` | What do we **believe**? | Six falsifiable stances, human-seeded |
| `catalyst-queue.json` | What do we expect, **when**, and so what? | Rolling, dated, forward-looking |

They are version-controlled, so every self-edit is a diff someone can review after the fact — which is what replaces an approval step.

## The governance contract

All three files obey the same five clauses. This is stated **once, here**; the per-file sections below only note their carve-outs.

### 1. The orchestrator is the sole machine writer

Only `run.py` writes state, and only during a run. Researchers are read-only at the permission level and cannot write at all ([04](04-researchers.md#transport)). The manager returns a draft issue on stdout; it does not touch state. No component can forget to persist, and no component can persist something the orchestrator didn't sanction.

### 2. Every machine write is a git commit citing its run

A state write is never silent. Each carries the `run_id` and a reason, and appends to that file's own log — `drift_log` for the watchlist and queue, `drift_log` per belief slot for the thesis, `slip_log` for a queue item's window revisions. The commit and the log are redundant on purpose: the commit is how a human reviews it, the log is how the next run and the critic read it.

### 3. The owner may edit any file at any time; owner edits win, with no carry-over

The owner is not a component of the pipeline and needs no permission from it. When the owner edits a file, the next run reads the new value and argues it — prior interpretations are **not** re-validated, and accumulated tallies do **not** transfer. Owner edits and loop edits stay distinguishable via `last_edited_by`.

The thesis states this as an invariant, and it generalizes to all three:

> **If the owner changes a stance and the next published issue still argues the old one, the pipeline is broken.**

### 4. Facts are machine-authored; interpretation is human-authored or explicitly delegated and marked

Applied field by field, not file by file. The loop may author and maintain any factual field with a source. It may **never** author an interpretive field into a slot no human has seeded — it accumulates candidate evidence and renders a marker instead.

Where an agent has drafted interpretation under explicit delegation, the field carries a provenance label saying so. An agent authoring a stance without one of those two warrants is a contract violation, not a judgment call. See [stance provenance](#stance-provenance).

### 5. Per-file immutability carve-outs are validator-enforced

Two fields are immutable once written, and one log is append-only. These are not conventions — the deterministic validator blocks on violations, because they are the only tamper-evidence the system has:

| Invariant | File | Enforcement |
|---|---|---|
| `first_expected_window` never changes after creation | catalyst queue | **blocking** validator check |
| `expected_window` changes only with a new `slip_log` entry | catalyst queue | **blocking** validator check |
| A belief's `drift_log` is append-only | thesis | append semantics; revisions recorded, never rewritten |

## The `entity_id` spine

**Ruling: the key is `entity_id` everywhere. Arrays are `entity_ids[]`.**

Stable kebab-case slugs (`merck`, `hengrui`, `asset_daraxonrasib`) are what link watchlist to issue to queue to findings. This is the field that makes entity history queryable, and it is what makes the later SQLite migration mechanical — one row per entity per issue.

Three assets disagreed on the *definition* key while agreeing on the *reference* key: `watchlist.json` defined `id`, `issue.json` called the same thing `entity_id`, and both the queue and the findings contract referenced them as `entity_ids[]`. One ruling resolves it:

- **`state/watchlist.json` renames `entities[].id` → `entities[].entity_id`.** Mechanical rename; the seeded roster is the only affected content.
- Everything else already conforms.
- **The validator gains a cross-file join check** so the spine cannot silently fork again: every `entity_ids[]` reference in an issue, a findings file, or a queue item must resolve to a `watchlist.entities[].entity_id`, or be accompanied by a `proposed_entity` ([04](04-researchers.md#the-contract)). This is the existing `dangling_entity` check, extended across files.

Note the roster mixes **companies and assets**. `asset_daraxonrasib` is a valid entity because tickers vanish on acquisition and assets don't — daraxonrasib survives Revolution Medicines being acquired. Both are valid reference targets.

## `state/watchlist.json`

### What it is

A roster of ~24 standing entities across five tiers, seeded from a bellwether power map. Below about 20 loses a tier; above about 30 adds entities that have never once moved the market.

| Tier | Meaning |
|---|---|
| `acquirer` | Can reprice a modality by writing a cheque. |
| `china_supply` | Where the clearing price of an asset class is discovered. |
| `platform` | Sets technical direction others clone. |
| `regulator` | Changes universal behaviour by fiat — highest signal-per-item. |
| `frontier_asset` | Tracked as an asset, not a ticker, because the tickers keep disappearing. |

### The load-bearing insight

**Readouts set the oncology agenda; companies react.** The roster is plumbing — entities exist here as *subscription addresses*, the IR pages and SEC filers and regulator feeds where catalysts surface. The [catalyst queue](#statecatalyst-queuejson) is the product.

This is why the original charted shape (five pharma / six startups / three China / two wildcards) was superseded: PD-1×VEGF spans nine companies and no single ticker covers it. FDA and CMS are on the list because a regulator changes universal behaviour by fiat. Isomorphic, VCs, and pre-IND startups are deliberately **off** — capital without a readout moves nobody.

### Entity shape

```jsonc
{
  "entity_id": "merck",              // the spine — stable, never reused
  "name": "Merck & Co.",
  "tier": "acquirer",
  "priority": "high",
  "why_tracked": "The forced buyer: >$25B/yr Keytruda revenue faces the 2028 LOE cliff...",
  "watch_for": ["M&A", "PD-1 successor deals", "Keytruda LOE mitigation"]
}
```

`watch_for` is the closest thing to a category taxonomy, and it is what makes a researcher's coverage duty actionable. `why_tracked` is deliberately **not** given to researchers — it is a summary, and summaries are the manager's job.

### Self-evolution

`auto_promote`: radar entities may be promoted into `entities` by the loop with no human approval; every promotion appends to `drift_log` with a reason. Demotion after `quiet_cycles_before_review: 6` cycles of silence is **proposed, not automatic**, for anything in `acquirer` or `regulator` tier — a quiet regulator is not an irrelevant one.

## `state/thesis.json`

### What it is

Six belief slots. Each is an opinionated, falsifiable position that Research Angles and "why we care" arguments are argued **against** — the standing worldview that turns a news summary into an intelligence product.

Slots: `adcs`, `radiopharma`, `china-licensing-wave`, `ira-pricing-pressure`, `platform-vs-asset`, `pharma-ma-appetite`.

### Slot shape

```jsonc
{
  "id": "adcs",                      // stable kebab-case; never reused after deletion
  "title": "Antibody-drug conjugates",
  "stance": "string | null",         // the position. null = DORMANT.
  "confidence": "low|medium|high|null",   // internal only
  "falsifier": "string | null",           // what would force revision. Internal only.
  "candidate_evidence": [],          // {date, headline, url, slot_relevance} — accrues while dormant
  "drift_log": [],                   // {date, from_stance, to_stance, trigger, cycle_id} — append-only
  "origin": "seed | auto",
  "stance_provenance": "owner | agent_draft_delegated"
}
```

### Reader visibility

**Only `stance` reaches the reader, and only indirectly** — as the argument behind a Research Angle or a "why we care". `confidence`, `falsifier`, `drift_log` and `candidate_evidence` are internal: machine tripwires plus a developer inspection surface. Never render them in a published issue.

### Dormancy and self-evolution

Per-slot, not global:

- A slot with a **null stance is dormant**. The loop must not author a stance for it. Each cycle a dormant slot may only append to `candidate_evidence` — sourced items that would bear on the belief once a human writes it. Cap 25, oldest evicted first.
- A slot **activates** the moment its stance is non-empty. From then on the loop may revise stance and confidence, and must append every revision to `drift_log`.

An empty thesis therefore **degrades rather than breaks**: thesis-dependent sections render `No thesis seeded — facts only` instead of an opinion, the run completes, and the critic files an advisory. Registered as `thesis_unseeded` in [the degradation register](06-validator-and-critic.md#the-degradation-register).

> An unowned opinion is worse than a visible gap. Researchers and manager **must not** improvise a stance to fill it.

### Stance provenance

Two warrants exist, and every stance carries one:

| `stance_provenance` | Meaning |
|---|---|
| `owner` | Chosen by the owner in a live session. Authoritative. |
| `agent_draft_delegated` | Drafted by an agent under the owner's explicit instruction. **The owner has not endorsed the content.** It exists so the machine has something to argue against. Provisional; expect revision. |

**An agent may not author a stance without one of these.** A prior session did, committing it as "human-approved"; it was found and reset. That incident is why the label exists.

**⚑ Current state: 2 slots are `owner` (`china-licensing-wave`, `pharma-ma-appetite`); 4 are `agent_draft_delegated`.** The spec ships this way deliberately. Endorsing a stance is an *operating* act for the owner, not a build input, and the blast radius is small: researchers never argue the thesis ([04](04-researchers.md#the-thesis-is-a-lens-not-a-conclusion)), so provisionality touches only the manager's tagging. The provenance label is normative — render it wherever the lens is shown.

### The propagation contract

This is the real deliverable of the thesis file, and the thing most likely to be broken by a well-meaning optimisation:

| Clause | Rule |
|---|---|
| **Single source of truth** | `state/thesis.json` is the only place a stance exists. Stance text is never copied into a prompt template, an issue, or the dashboard at build time. |
| **Read fresh** | Researchers and the manager receive stances by reading the file **at run time**. A prompt template that inlines stance text is a bug, not an optimisation. |
| **Version stamping** | Every published issue records `thesis_version` in its run block. An issue's Research Angles are valid only against the version they argued. |
| **On owner edit** | Bump `version`. The next cycle argues the new stance with **no carry-over**: prior angles are not re-validated, accumulated `thesis_impact` tallies do not transfer. `candidate_evidence` **is** retained — it is sourced fact, not opinion. |
| **On loop evolution** | Same version bump, but `drift_log` records from/to and the trigger. `last_edited_by` distinguishes owner edits from loop edits. |

## `state/catalyst-queue.json`

### What it is, and why it isn't a watchlist tier

A catalyst is a **dated expectation**: a named readout or decision, expected in a window, that would prove or disprove something.

| | Answers | Cardinality | Lifecycle |
|---|---|---|---|
| `watchlist` entity, tier `frontier_asset` | *What* do we watch? | one per asset | standing |
| `catalyst_queue` item | *What, when,* and *so what*? | many per asset | rolling, re-cut monthly |

An asset is a subscription **address**; a catalyst is an **event expected at that address**. They link by `entity_ids[]` — nothing is duplicated.

This is **the only structure in the system that commits to a claim about the future.** Everything else reports what happened. That is what makes it the product, and why its integrity rules are load-bearing rather than decorative.

### The accountability invariant

> **`first_expected_window` is written once, at item creation, and is never edited by any process. `expected_window` may be revised; every revision must append to `slip_log`.**

Without this, the monthly re-cut can quietly edit windows forward and the queue is *always* accurate — the goalposts just move, invisibly, and every prediction is worthless. With it, the dashboard renders the most valuable line in the digest:

> **expected 2026-Q2 · slipped twice · now 2026-Q4**

**No self-grading.** The system records what it said and what happened; the reader judges. A model marking its own homework is not evidence.

Validator checks (all deterministic, from [06](06-validator-and-critic.md#stage-1--the-validator)):

| Check | Severity |
|---|---|
| `first_expected_window` differs from the previous snapshot's value | **blocking** — the system's only tamper-evidence rule |
| `expected_window` changed without a new `slip_log` entry | **blocking** |
| Status transition with no source | **blocking** |
| `entity_ids[]` references an unknown entity | **blocking** (the cross-file join check) |
| Active (`pending`) item count outside 8–18 | advisory |

### Authorship, field by field

- **Machine-authored:** `asset`, `entity_ids`, `holders`, `catalyst`, `expected_window`, `window_source`, `status`, `slip_log`, `sources`. Public record; the loop sources them, no worldview required.
- **Thesis-gated:** `what_it_would_prove`, `bears_on_thesis_slot`. If the bound slot's stance is null, `what_it_would_prove` renders the marker and **the item still ships**.

So the queue is useful from day one and grows opinions only as a human seeds them.

### Cadence

| Clock | May do | May not do |
|---|---|---|
| **Every run** | status transitions, append `slip_log` | add, retire, or edit `first_expected_window` |
| **Monthly** (first run of the month) | add, retire, resize toward 10–15, re-anchor windows to the conference calendar | edit `first_expected_window` |

Every transition needs a **source**: `delivered` requires a citation to the readout, `slipped` requires evidence of the revised window. No source, no transition. The monthly re-cut runs under `auto_promote` with every add/retire/resize appended to `drift_log`.

### The per-issue snapshot

Each `issue.json` embeds a **read-only snapshot** under `catalyst_queue`, so a published issue stays truthful about what was expected *at the time*. Rendering a March issue must never show today's queue. Full shape in [07](07-issue-schema.md#catalyst_queue).

### Why the seed is 4 items, not 10–15

Deliberate, and the reasoning matters more than the number. The evidence base is **retrospective** and states plainly that ESMO 2026 has not happened, so H2's agenda-setting readouts are unknown — it contains **no citable forward windows**.

The queue's entire value is dated, falsifiable predictions. Fabricating windows to hit a target count would poison the one structure whose whole purpose is being checkable. So all four seeds ship with `first_expected_window: null` and `sources: []`, grounded only in what the research verified. The loop populates windows and grows the queue toward target on first run and first monthly re-cut — which is exactly what the facts-are-machine-authored rule is for.

## What run #1 does

Nothing special. It reads the seeded files, finds the calendar unverified and most queue windows null, publishes a full digest with the stale-calendar marker explaining why nothing surged, and creates the baseline values that later continuity checks compare against. There is no bootstrap flag and no cold-start branch — see [continuity across stubs](06-validator-and-critic.md#continuity-across-stubs) for why run #1 falls out of the design for free.

---

*Provenance: tickets [#4](https://github.com/cmengu/Research-Swarm/issues/4) (watchlist), [#5](https://github.com/cmengu/Research-Swarm/issues/5) and [#11](https://github.com/cmengu/Research-Swarm/issues/11) (thesis), [#17](https://github.com/cmengu/Research-Swarm/issues/17) (catalyst queue); governance contract and `entity_id` ruling from [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381). Evidence base: [`docs/research/oncology-bellwethers-2026.md`](../research/oncology-bellwethers-2026.md).*
