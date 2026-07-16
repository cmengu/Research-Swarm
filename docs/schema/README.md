# issue.json — schema notes (v0.1.0 draft)

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

## Open questions for the human

- Is `priority` (high/medium/low) enough, or do you want a numeric score for ranking?
- Should `headline.so_what` and `research_angle` stay separate, or is the headline just another angle?
- Do you want `confidence` on every claim, or only on the headline (as sampled)?
