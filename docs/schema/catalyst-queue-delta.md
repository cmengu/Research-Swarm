# Catalyst queue — schema v0.3.0 delta

> **Superseded by [`docs/spec/03-state-and-governance.md`](../spec/03-state-and-governance.md) (governance) and [`docs/spec/07-issue-schema.md`](../spec/07-issue-schema.md) (the snapshot block).** Kept for its reasoning. Where the two disagree, **the spec wins** — the v0.3.0 deltas here are folded into issue.json v1.0.0.

Decision asset for ticket [#17](https://github.com/cmengu/Research-Swarm/issues/17). Canonical state file: [`state/catalyst-queue.json`](../../state/catalyst-queue.json).

## Why this is not just another watchlist tier

The watchlist's `frontier_asset` tier already tracks daraxonrasib, ivonescimab and friends. The queue is a **different noun**:

| | Answers | Cardinality | Lifecycle |
|---|---|---|---|
| `watchlist.entities[tier=frontier_asset]` | *What* do we watch? | one per asset | standing |
| `catalyst_queue` | *What, when,* and *so what*? | many per asset | rolling, re-cut monthly |

An asset is a **subscription address**. A catalyst is a **dated expectation emitted at that address**. They link by `entity_ids[]` — nothing is duplicated, and the validator's existing `dangling_entity` check covers the reference.

This is also the only structure in the system that makes a **forward-looking commitment**. Everything else reports what happened; the queue says what *will* happen and when. That is what makes it the product — and what makes its integrity rules load-bearing rather than decorative.

## Where it lives

**Canonical**: `state/catalyst-queue.json` — rolling, self-maintained, drift-logged. Same pattern as `watchlist.json` and `thesis.json`, so every re-cut is a reviewable git diff.

**Per-cycle**: each `issue.json` embeds a **read-only snapshot** under `catalyst_queue`, so a published issue stays truthful about what was expected *at the time*. Rendering a March issue must never show today's queue.

```jsonc
// issue.json v0.3.0
"catalyst_queue": {
  "snapshot_of": "state/catalyst-queue.json",
  "recut_at": "2026-07-01",        // last monthly re-cut as of this issue
  "items": [ /* full queue items, frozen */ ]
}
```

## The accountability invariant

> `first_expected_window` is written once, at item creation, and is **never** edited. `expected_window` may be revised; every revision **must** append to `slip_log`.

Without this, the monthly re-cut can quietly edit windows forward and the queue is *always* accurate — the goalposts just move, invisibly. That would make every prediction worthless. With it, the dashboard can render the most valuable line in the whole digest:

> **expected 2026-Q2 · slipped twice · now 2026-Q4**

The system records what it said and what happened. It does **not** grade its own hit rate — self-scoring is exactly the kind of judgment the critic rubric keeps advisory, and a model marking its own homework is not evidence.

**Validator additions** (deterministic gate, free, per #7's two-gate design):

| Check | Severity |
|---|---|
| `first_expected_window` differs from the value in the previous issue's snapshot | **blocking** — tamper-evident by construction |
| `expected_window` changed without a new `slip_log` entry | **blocking** |
| status transition with no source | **blocking** |
| `entity_ids[]` references an id absent from the watchlist | **blocking** (existing `dangling_entity`) |
| active (`pending`) item count outside 8–18 | advisory |

## Authorship: facts machine, interpretation gated

Consistent with the thesis contract (#5), applied field-by-field:

- **Machine-authored**: `asset`, `entity_ids`, `holders`, `catalyst`, `expected_window`, `window_source`, `status`, `slip_log`, `sources`. These are public record — the loop sources them, no worldview required.
- **Thesis-gated**: `what_it_would_prove`, `bears_on_thesis_slot`. If the bound slot's `stance` is null, the field renders `No thesis seeded — facts only` and the item **still ships**. Seeding a slot activates interpretation for its catalysts.

So the queue is useful from day one and grows opinions only as a human seeds them. The loop never invents interpretation into a dormant slot.

## Cadence

| Clock | May do | May not do |
|---|---|---|
| **Every run** (Mon/Thu) | status transitions, append `slip_log` | add, retire, or edit `first_expected_window` |
| **Monthly** (first run of month) | add, retire, resize toward 10–15, re-anchor windows to the conference calendar | edit `first_expected_window` |

Every transition needs a **source**: `delivered` requires a citation to the readout, `slipped` requires evidence of the revised window. No source, no transition.

The monthly re-cut runs under `auto_promote` — no human approval, consistent with CAPTURE #6 — with every add/retire/resize appended to `drift_log`.

## Dashboard

The queue renders as **its own section, directly under the headline** — above the watchlist. Forward-looking first, retrospective below, because the adopted research argues the queue is the deliverable and the roster is plumbing. Slip history renders inline per item.

Files as a v0.3.0 dashboard delta; the approved v3 prototype (#8) predates this ticket and is not reopened.

## Dependency on the conference calendar (#18)

`expected_window` anchors to the conference calendar defined in **Conference surge mode + calendar config** (#18). The two tickets touch disjoint files and can be worked in parallel, but there is a **content dependency**: until the calendar exists, windows are sourced ad hoc from company guidance and trial registries, or stay null.

## Why the seed is 4 items, not 10–15

Deliberate, and the reasoning matters more than the number.

The evidence base ([oncology-bellwethers-2026.md](../research/oncology-bellwethers-2026.md)) is **retrospective** and states plainly that ESMO 2026 (Oct) has not happened, so H2's agenda-setting readouts are unknown and the map has a ~3-month shelf life. It therefore contains **no citable forward windows**.

The queue's entire value is dated, falsifiable predictions. Fabricating windows to hit a target count would poison the one structure whose whole purpose is being checkable — so all four seeds ship with `first_expected_window: null` and `sources: []`, grounded only in what the research verified. The loop populates windows and grows the queue to target on first run and first monthly re-cut, which is exactly what the facts-are-machine-authored rule is for.
