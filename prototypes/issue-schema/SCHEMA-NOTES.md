# issue.json — field-level contract (draft for reaction)

One file per cycle: `issues/<issue_id>.json`, where `issue_id` is the date (`YYYY-MM-DD`).
Sample: `2026-07-16.issue.json` (published). Stub variant: `2026-07-13.issue.failed.json`.

## Design rules baked into the sample

1. **Sources are a table, referenced by id.** Every section cites `source_refs: ["src-01"]`
   instead of inlining a URL. One source cited from five places is stored once, and the
   critic can audit provenance by walking one list. This is also the single biggest reason
   the SQLite swap stays mechanical (see below).
2. **The thesis is referenced, not copied.** `thesis_refs` point at stable ids in the thesis
   state file. The issue records *the argument*, the thesis file holds *the worldview*.
3. **Every top-level section from CAPTURE.md is a top-level key**, in digest order. The
   dashboard renders top-to-bottom by walking the keys — no layout logic hidden in the data.
4. **Failed runs use the same schema**, with `status: "failed"`, a populated `failure`
   object, and empty/null sections. The dropdown renders both from one code path.
5. **Nothing is silently dropped.** Critic rejections live in
   `quiet_this_cycle.critic_catches`; the sources they cite stay in the `sources` table so
   the rejection is auditable.

## Top-level fields

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | semver; bump on any breaking field change |
| `issue_id` | string | `YYYY-MM-DD`, matches filename, primary key |
| `status` | enum | `published` \| `failed` |
| `generated_at` | ISO-8601 string | with offset |
| `coverage_window` | object | `from`, `to`, `widened_from_failure` (bool — decision #16) |
| `run` | object | run_id, trigger, models, beats_run, critic_retries_used, duration_seconds, stage_status |
| `failure` | object \| absent | present only when `status = failed` |
| `tldr_headline` | object \| null | title, summary, primary_source_ref, beat |
| `stats_bar` | object \| null | the five counters from CAPTURE.md §2 |
| `tldr_bullets` | array | text, beat, source_refs |
| `tracked_watchlist` | array | one per tracked entity with news |
| `quiet_this_cycle` | object \| null | `no_news[]`, `critic_catches[]`, `open_threads[]` |
| `new_on_radar` | array | entity, priority, topic, what_they_do, why_we_care, auto_promoted_to_watchlist |
| `themes_and_signals` | array | theme, signal, evidence_refs, thesis_refs, direction |
| `elsewhere_on_frontier` | array | entity, move, why_it_matters, source_refs |
| `sources` | array | the source table — every `src-NN` referenced anywhere |
| `method` | object \| null | source_universe, trust_tiers, paywall_policy, notes |
| `critic_report` | object \| null | verdict, retries_used, blocking_findings, advisory_findings, unresolved_banner |
| `thesis_changelog` | array | visible drift (decision #7) |
| `watchlist_changelog` | array | visible self-maintenance (decision #6) |

## Enums

- `status`: `published` | `failed`
- `run.trigger`: `scheduled` | `manual`
- `stage_status.*`: `ok` | `failed` | `skipped`
- `beat`: `ma-dealmaking` | `onc-startup-frontier` | `clinical-scientific` | `policy-regulation` | `incumbents-entrants` | `catch-all` (the roster from decision #4)
- `priority`: `high` | `medium` | `low`
- `category` (tracked watchlist): `trial-readout` | `deal-ma` | `funding` | `regulatory` | `people` | `platform-tech`
- `status` (tracked entry): `developing` | `concluded`
- `entity.type`: `company` | `lab` | `investor` | `regulator`
- `source.tier`: `primary` | `trade` | `aggregator` (decision #9)
- `research_angle.conviction`: `high` | `medium` | `low`
- `critic_report.verdict`: `pass` | `pass_with_advisories` | `fail_published_with_banner` (decision #8)
- `finding.severity`: `blocking` | `advisory`
- `thesis_changelog.direction`: `strengthened` | `weakened` | `amended` | `new` | `retired`
- `watchlist_changelog.action`: `added` | `removed` | `promoted` | `flagged` | `repriorized`
- `themes.direction`: `consolidating` | `accelerating` | `fragmenting` | `stalling`

## SQLite mapping (decision #11 — "mechanical swap")

Each array of objects becomes a table keyed by `issue_id`; the source table is the join hub:

- `issues` (top-level scalars + embedded singletons flattened)
- `sources` (issue_id, ref, …) — PK `(issue_id, ref)`
- `tracked_entries`, `radar_entries`, `themes`, `frontier_moves`, `tldr_bullets`,
  `critic_findings`, `thesis_changelog`, `watchlist_changelog`, `open_threads`,
  `critic_catches`, `quiet_entries` — each FK `issue_id`
- `source_refs` arrays become a `citations` join table `(issue_id, section, row_id, source_ref)`

This works because **no section nests deeper than two levels** and every cross-reference is
already an id (`src-NN`, `entity_id`, `thesis_id`) rather than an inline object. Keep that
invariant and the migration is a script, not a redesign.

## Open questions — react to these

1. **`entity_id` stability.** Slugs (`helix-therapeutics`) are readable but break on rename
   and on acquisition (does Helix survive as an entity after Aventis closes?). Alternative:
   opaque ids + a `names[]` history in the watchlist state file. Slug now, or opaque now?
2. **Cross-issue diffs.** Nothing here says "this is the 3rd cycle Meridian has been
   developing." Do open threads carry an `opened_on` (as sampled) and the dashboard computes
   age, or does each issue store a precomputed `cycles_open`?
3. **`valuation` shape.** Currently one loose object doing double duty for acquisition prices
   and funding rounds (`per_share_usd`/`premium_pct` are null for raises). Split into
   `deal_terms` vs `funding_terms`, or keep one nullable bag?
4. **Paywalled-source flag.** Sampled as `source.paywalled: bool`, but decision #9 wants a
   visible "primary paywalled — assess manually" marker on the *claim*, not just the source.
   Does the flag belong on the tracked entry too?
5. **Does the sample's `research_angle.disagrees_with_consensus` earn its place?** It's the
   most opinionated field here — good for investor/BD tone, but it forces the manager to
   model "consensus," which nothing in the pipeline actually measures.
6. **Stats-bar derivability.** Every counter is computable from the arrays. Store it (fast,
   can drift) or compute it in the dashboard (always correct, more render logic)?
