# 7. issue.json schema v1.0.0

The complete field-level contract for a published issue. This is what the manager emits, the validator checks, the critic judges, and the dashboard renders.

**Version:** `1.0.0`. Consolidates every delta deferred during planning into one bump — see [the delta log](#delta-log-what-changed-and-why).
**Reference sample:** [`docs/schema/sample-issue-2026-07-16.json`](../schema/sample-issue-2026-07-16.json) — **all content fabricated** for design purposes, and predating this version; treat this document as authoritative where they differ.

## Design principles

1. **`entity_id` is the spine.** Stable slugs link watchlist ↔ radar ↔ themes ↔ headline ↔ queue ↔ findings. This is what makes entity history queryable and the later SQLite migration mechanical — one row per entity per issue.
2. **Sources are objects, never strings.** `{url, publisher, tier, published_at}`. The critic checks claims against `tier`, and `published_at` is what catches recycled news.
3. **`thesis_impact` is the self-evolution engine**, not decoration. Enough `challenges` on one belief mechanically triggers a logged `thesis_updates` entry.
4. **`stats` is derived, never authored.** The orchestrator computes counts from the arrays so the bar cannot lie.
5. **A failed run is the same schema.** `status: "failed"`, empty sections, `failure.stage` names where — so the dashboard needs no separate stub renderer.
6. **Every published issue is immutable.** Later runs never edit earlier issues; the catalyst snapshot exists precisely so a March issue keeps showing what March expected.

## Top level

```jsonc
{
  "schema_version": "1.0.0",
  "issue": { /* identity, window, run */ },
  "headline": { /* the cycle's biggest story */ },
  "stats": { /* derived counts */ },
  "tldr_bullets": [ /* one per main topic */ ],
  "catalyst_queue": { /* read-only snapshot */ },
  "watchlist": [ /* per tracked entity with news */ ],
  "quiet_this_cycle": { /* no_news, critic_catches, open_threads */ },
  "new_on_radar": [ /* newly surfaced entities */ ],
  "themes_and_signals": [ /* cross-cutting patterns */ ],
  "elsewhere_on_frontier": [ /* incumbent moves */ ],
  "thesis_updates": [ /* the visible drift log */ ],
  "critic_report": { /* both gates' records */ },
  "sources_and_method": { /* the audit trail */ }
}
```

## `issue`

```jsonc
"issue": {
  "id": "2026-07-16",                       // the dated issue id; also the filename
  "published_at": "2026-07-16T07:42:11+08:00",
  "coverage_window": {"from": "2026-07-13", "to": "2026-07-16"},
  "run": {
    "run_id": "run_20260716_0700",
    "status": "published | published_uncritiqued | published_with_unresolved_findings | failed",
    "critic_verdict": "pass | pass_with_advisories | blocked | not_run",
    "critic_retries": 1,
    "thesis_version": 2,                    // which thesis version the angles argued
    "models": {
      "researchers": "claude-sonnet-5",
      "manager": "claude-opus-4-8",
      "critic": "gpt-5.6-codex"
    },
    "surge": {                              // ABSENT (not null) on a baseline run
      "window": "ASCO 2026",
      "day": 2,
      "of": 5
    }
  },
  "failure": {                              // present only when status == "failed"
    "stage": "research | synthesis | validation | critique | publish",
    "detail": "human-readable, points at the log"
  }
}
```

`thesis_version` is what makes an issue's Research Angles auditable: they are valid only against the version they argued ([03](03-state-and-governance.md#the-propagation-contract)).

`coverage_window.from` joins to the most recent issue that actually covered a window — **not** the positionally previous one, so a stub doesn't break the chain ([06](06-validator-and-critic.md#continuity-across-stubs)).

## `headline`

```jsonc
"headline": {
  "title": "Merck's $9B Verastem buy resets ADC pricing",
  "summary": "2-4 sentences, every claim covered by sources[]",
  "so_what": "The editorial judgment: why this reader should care today.",
  "entity_refs": ["merck", "verastem"],
  "confidence": "high | medium | low",
  "sources": [ /* source objects */ ]
}
```

`so_what` is **always present and thesis-independent** — it is not the same field as `research_angle`, and a dormant thesis never silences it ([05](05-manager.md#so_what-and-research_angle-are-different-fields)).

## `stats`

Derived by the orchestrator, never authored by the manager.

```jsonc
"stats": {
  "tracked_updates": 9,
  "tracked_quiet": 7,
  "new_on_radar": 3,
  "frontier_items": 5,
  "sources_cited": 34,
  "critic_catches": 2,
  "previous_issue": "2026-07-13"    // null on run #1 — true, not a bootstrap flag
}
```

## `tldr_bullets`

```jsonc
{"text": "...", "entity_refs": ["merck"], "priority": "high | medium | low"}
```

## `catalyst_queue`

A **read-only snapshot** of `state/catalyst-queue.json` frozen at publication, so a published issue stays truthful about what was expected at the time.

```jsonc
"catalyst_queue": {
  "snapshot_of": "state/catalyst-queue.json",
  "recut_at": "2026-07-01",         // last monthly re-cut as of this issue
  "items": [
    {
      "id": "asco26_daraxonrasib_os",
      "asset": "daraxonrasib",
      "entity_ids": ["asset_daraxonrasib", "revolution_medicines"],
      "holders": ["Revolution Medicines"],
      "catalyst": "RASolute 302 final OS readout",
      "first_expected_window": "2026-Q2",   // IMMUTABLE after creation — validator blocks on change
      "expected_window": "2026-Q4",         // revisable, but only with a slip_log entry
      "window_source": {"url": "...", "publisher": "...", "tier": "primary", "published_at": "..."},
      "status": "pending | slipped | delivered | dead",
      "slip_log": [
        {"from": "2026-Q2", "to": "2026-Q4", "date": "2026-06-02", "source": { /* source */ }}
      ],
      "what_it_would_prove": "thesis-gated — renders the marker if the bound slot is dormant",
      "bears_on_thesis_slot": "pharma-ma-appetite",
      "sources": [ /* source objects */ ]
    }
  ]
}
```

The invariant this schema exists to protect: **`first_expected_window` is written once and never edited; `expected_window` revisions must append to `slip_log`.** The validator's `queue_tamper` check blocks on violations, comparing against the most recent snapshot that carries the field ([03](03-state-and-governance.md#the-accountability-invariant)).

The dashboard derives the digest's most valuable line from these two fields: *expected 2026-Q2 · slipped twice · now 2026-Q4*.

## `watchlist`

One entry per tracked entity **with news** this cycle. Entities without news go to `quiet_this_cycle` — every tracked entity appears in exactly one of the two, every cycle.

```jsonc
{
  "entity_id": "merck",
  "name": "Merck & Co.",
  "type": "big_pharma",
  "status": "developing | concluded",
  "priority": "high | medium | low",
  "categories": ["deal_ma"],        // trial_readout | deal_ma | funding | regulatory | people | platform_tech
  "summary": "Factual, sourced.",
  "research_angle": "The opinionated take, argued against a thesis stance.",
  "thesis_impact": "confirms | challenges | neutral",
  "confidence": "high | medium | low",
  "degradation": null,              // or e.g. {"kind": "beat_failed", "marker": "M&A coverage unavailable this cycle — beat failed"}
  "sources": [ /* source objects */ ]
}
```

`research_angle` is **thesis-gated**: if its slot is dormant, it renders `No thesis seeded — facts only` and the entry still ships with facts intact. `thesis_impact` is then omitted — there is no stance for it to bear on.

`degradation` is how a marker renders **at the point of the absence** rather than only in a footer ([06](06-validator-and-critic.md#where-a-degradation-renders)). Any section that a failed beat would have fed carries one.

## `quiet_this_cycle`

```jsonc
"quiet_this_cycle": {
  "no_news": [
    {"entity_id": "pfizer", "name": "Pfizer", "cycles_quiet": 2}
  ],
  "critic_catches": [
    {
      "claim": "Zentalis raising $400M at a $2.1B valuation",
      "rejected_because": "provenance_stale",
      "detail": "Every July 14-15 aggregator repeat traces to a single 12 Mar 2026 Bloomberg piece.",
      "caught_by": "critic | validator",
      "sources": [ /* source objects */ ]
    }
  ],
  "open_threads": [
    {
      "entity_id": "verastem",
      "thread": "Antitrust review timeline for the Merck deal",
      "since": "2026-07-15",
      "next_expected": "FTC second-request window closes ~Aug 2026"
    }
  ]
}
```

**Critic rejections are published, not silently dropped** — they are part of the product. `cycles_quiet` increments honestly and joins across failed-run stubs.

## `new_on_radar`

```jsonc
{
  "entity_id": "callio_tx",
  "name": "Callio Therapeutics",
  "type": "startup",
  "priority": "medium",
  "categories": ["funding"],
  "what_they_do": "Factual.",
  "development": "Factual, sourced.",
  "why_we_care": "Thesis-gated — the marker renders here if the slot is dormant.",
  "promotion_proposal": {
    "promote_to_watchlist": true,
    "reason": "Second dual-payload financing above $150M this quarter."
  },
  "sources": [ /* source objects */ ]
}
```

`promotion_proposal` is the self-maintaining watchlist's mechanism: no human approval, but the reason is written down so drift is auditable. `run.py` executes accepted promotions against `state/watchlist.json` with a `drift_log` entry.

## `themes_and_signals`

```jsonc
{
  "theme": "Pre-readout premiums are back",
  "evidence_refs": ["merck", "callio_tx"],   // entity_ids
  "argument": "The cross-cutting claim and what it would mean if it holds.",
  "thesis_impact": "confirms | challenges | neutral"
}
```

## `elsewhere_on_frontier`

```jsonc
{
  "actor": "FDA",
  "move": "Final rule on accelerated-approval confirmatory trials takes effect.",
  "detail": "Factual, sufficiently detailed to act on.",
  "why_it_matters": "Why this reprices something.",
  "sources": [ /* source objects */ ]
}
```

## `thesis_updates`

The visible drift log — the reason thesis self-evolution is watchable rather than spooky.

```jsonc
{
  "change": "amended | added | retired",
  "field": "pharma-ma-appetite",             // the belief slot id
  "before": "Acquirers will wait for Phase 3 readouts...",
  "after": "Acquirers are paying pre-readout premiums...",
  "triggered_by": ["merck", "callio_tx"]     // entity_ids whose challenges accumulated
}
```

Only `stance` transitions appear here. `confidence`, `falsifier`, `drift_log` and `candidate_evidence` are **internal** and never rendered ([03](03-state-and-governance.md#reader-visibility)).

## `critic_report`

Both gates' records ride in the published issue.

```jsonc
"critic_report": {
  "verdict": "pass | pass_with_advisories | blocked | not_run",
  "retries_used": 1,
  "blocking_findings": [
    {
      "kind": "dropped_story",
      "where": "watchlist.merck",
      "note": "...",
      "source": {                        // REQUIRED when kind == "dropped_story" — the receipt
        "url": "...", "publisher": "...", "tier": "primary", "published_at": "2026-07-15"
      },
      "rebuttal": {                      // present only if the manager rebutted
        "text": "A sourced argument for why the finding is wrong.",
        "sources": [ /* source objects */ ],
        "adjudication": "withdrawn | reaffirmed"    // SET BY THE CRITIC, never the manager
      }
    }
  ],
  "advisory_findings": [
    {"kind": "thin_sourcing", "where": "watchlist.hengrui", "note": "..."}
  ],
  "validator_report": {                  // stage 1's record
    "passed": true,
    "retries_used": 0,
    "findings": [{"kind": "...", "where": "...", "note": "..."}]
  }
}
```

A `dropped_story` without a well-formed receipt is **automatically downgraded** into `advisory_findings` by the orchestrator ([06](06-validator-and-critic.md#the-receipt-rule)). Surviving blocking findings publish **with their rebuttals** under a banner — both sides, always.

## `sources_and_method`

```jsonc
"sources_and_method": {
  "beats_run": ["ma_dealmaking", "startup_frontier", "clinical_scientific",
                "policy_regulation", "incumbent_moves", "backstop"],
  "beats_failed": [],                    // audit trail — NOT the reader-facing render
  "source_tier_counts": {"primary": 14, "trade": 17, "aggregator": 3},
  "paywalled_flagged": [
    {
      "claim": "Verastem board rejected an earlier $7.2B approach",
      "publisher": "STAT+",
      "url": "...",
      "note": "Primary paywalled — assess manually."
    }
  ]
}
```

`beats_failed` serves the audit trail and the critic. The **reader's** marker is the inline `degradation` on each affected section — a reader who never scrolls this far must still see the absence.

## The source object

Used identically everywhere in this schema and in `findings.json`:

```jsonc
{
  "url": "https://...",
  "publisher": "Endpoints News",
  "tier": "primary | trade | aggregator",
  "published_at": "2026-07-15",
  "paywalled": false                     // optional; findings.json always sets it
}
```

All four core fields are required — the validator's `malformed_source` check blocks otherwise. Tier definitions and sourcing rules: [04](04-researchers.md#sourcing-rules--non-negotiable).

## Delta log: what changed, and why

v1.0.0 consolidates every schema change deferred during planning. Nothing here is new — each was decided by the ticket named, then held back rather than stacking three minor bumps that would each need re-litigating.

| Change | Source |
|---|---|
| `blocking_findings[].source` — the receipt, required for `dropped_story` | Critic rubric — the receipt rule was unimplementable without it |
| `blocking_findings[].rebuttal` with critic-set `adjudication` | Critic rubric — the rebuttal channel |
| `critic_report.validator_report` block | Critic rubric — two gates need two records |
| `run.status` gains `published_uncritiqued`, `published_with_unresolved_findings` | Critic rubric — a missing critic is not a failed run |
| `failure.stage` accepts `"validation"` | Critic rubric — validator exhaustion produces a stub |
| `catalyst_queue` snapshot block + `queue_tamper` validator checks | Catalyst queue — a published issue must stay truthful about what it expected |
| `run.surge = {window, day, of}`, absent on baseline runs | Surge mode — five ASCO-week issues must not read as a bug |
| `advisory_findings[].kind` gains `calendar_stale` | Surge mode — staleness is the only otherwise-silent failure |
| `advisory_findings[].kind` gains `continuity_baseline_expired` | The lookback floor |
| `blocking_findings[].kind` gains `unconfirmed_as_fact` | The `unconfirmed` publication rule |
| `degradation` field on sections | The degradation register — markers render at the absence, not in a footer |
| `watchlist[].entity_id` naming aligned across all files | The `entity_id` spine ruling |
| `headline.so_what` and `research_angle` both retained, distinct duties | The `so_what` ruling — the one open question from the original schema |

---

*Provenance: ticket [#3](https://github.com/cmengu/Research-Swarm/issues/3) (schema v0.1.0) plus deltas from [#7](https://github.com/cmengu/Research-Swarm/issues/7), [#17](https://github.com/cmengu/Research-Swarm/issues/17), [#18](https://github.com/cmengu/Research-Swarm/issues/18), [#23](https://github.com/cmengu/Research-Swarm/issues/23), [#6](https://github.com/cmengu/Research-Swarm/issues/6); consolidated under [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381). Supersedes `docs/schema/README.md`.*
