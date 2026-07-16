# issue.json — schema notes (v0.1.0 draft)

> **Superseded by [`docs/spec/07-issue-schema.md`](../spec/07-issue-schema.md), which defines v1.0.0.** Kept for its design rationale. Where the two disagree, **the spec wins** — v1.0.0 consolidates every deferred delta and rules this document's one open question (`headline.so_what` vs `research_angle`: two fields, distinct duties, both kept).

Prototype asset for ticket [#3](https://github.com/cmengu/Research-Swarm/issues/3). Sample: [sample-issue-2026-07-16.json](sample-issue-2026-07-16.json) — **all content fabricated** for design purposes.

## Design choices to react to

1. **`entity_id` is the spine.** Stable slugs (`merck`, `hengrui`) link watchlist ↔ radar ↔ themes ↔ headline. This is what makes entity history queryable later and makes the SQLite migration mechanical (one row per entity per issue).
2. **Sources are objects, never strings.** `{url, publisher, tier, published_at}`. `tier` ∈ primary | trade | aggregator — the critic checks claims against tier, and `published_at` is what caught the stale-provenance rejection in the sample.
3. **`research_angle` is a required field on every watchlist entry**, and it carries `thesis_impact` ∈ confirms | challenges | neutral. That single enum is what lets the thesis self-evolve mechanically: enough `challenges` on one belief triggers a `thesis_updates` entry.
4. **`thesis_updates` is a first-class section** — the visible drift log decided in grilling. Before/after text plus what triggered it.
5. **`critic_report` ships inside the issue**, blocking vs advisory split, matching the fail-visible rule. `critic_catches` (rejected stories) live under `quiet_this_cycle` because that's where they were dictated to appear.
6. **`promotion_proposal` on radar items** — the self-maintaining watchlist's mechanism; no human approval, but the reason is written down so drift is auditable.
7. **`stats` is derived, not authored** — the orchestrator computes counts from the arrays so the bar can't lie.
8. **`run` block** carries run_id, status, models used, retry count — makes a failed-run stub the same schema with `status: "failed"` and empty sections.

## Resolved in grilling (16 Jul 2026)

- **`thesis_impact` stays and is the self-improvement engine.** Every `research_angle` declares confirms | challenges | neutral; enough `challenges` accumulating on one belief mechanically triggers a logged `thesis_updates` revision. Drift is measurable, not vibes-based.
- **`priority` stays a three-value tag** (high | medium | low). No numeric score — 82-vs-79 is false precision. Ranking within a tier = document order, i.e. the manager's judgment.
- **`confidence` lives on the headline and on each watchlist entry** — the two places a reader acts on. Not on radar items, themes, or frontier moves: scoring everything makes the manager stamp 'high' everywhere and kills the signal.

## Thesis propagation (v0.2.0)

`issue.run.thesis_version` stamps which thesis version an issue argued against. This exists to make one invariant checkable: **if the owner edits a stance and the next issue still argues the old one, the pipeline is broken.** Stances live in exactly one place (`state/thesis.json`), are read fresh at run time, and are never inlined into a prompt template — a template that bakes in stance text is a bug. On an owner edit the version bumps and nothing carries over: prior Research Angles are not re-validated and accumulated `thesis_impact` tallies do not transfer. `candidate_evidence` is retained, because it is sourced fact rather than opinion. Full contract: `state/thesis.json` → `propagation_contract`.

## Still open (for the spec)

- Whether `headline.so_what` and `research_angle` are the same field wearing two hats.
