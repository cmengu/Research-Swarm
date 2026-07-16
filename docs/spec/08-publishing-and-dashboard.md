# 8. Publishing and dashboard

How an issue reaches a reader. Covers the static dashboard, its design system, the issue manifest, and every reader-facing marker the pipeline can raise.

**Inputs:** `issues/*.json`, `issues/index.json`.
**Prototype:** the approved v3 design ([`dashboard/`](../../dashboard/)) — an owner-approved baseline, not a sketch to redo.

## Publishing model

| | |
|---|---|
| **v1 reach** | localhost, reachable from LAN devices via the host PC's IP. |
| **Storage** | One `issues/<date>.json` per cycle, committed to git on every run. |
| **Later** | A GitHub Pages flip is a ten-minute change — the artifacts are already static and committed. Caveat: Pages is public web unless paid. |
| **Retention** | Every issue is kept forever. They're small, and the archive *is* the product's track record. |

Published issues are **immutable**. A later run never edits an earlier issue — which is exactly why the catalyst queue is snapshotted per issue rather than referenced live ([07](07-issue-schema.md#catalyst_queue)).

## The dashboard is static by construction

**A single self-contained HTML file. No framework, no build step, no bundler, no external asset.**

This is a hard constraint learned the expensive way: an earlier prototype loaded its data from an external `_sample.js` and shipped an **empty page** when published, because the asset didn't travel with it. Verify the *published* artifact, never localhost.

The page reads `issues/index.json` plus the selected `issues/<date>.json`. That's the whole data layer.

## Design system — approved, do not redesign

The v3 prototype was iterated over several rounds with the owner and signed off. Preserve:

| Element | Decision |
|---|---|
| **Palette** | H&E stain — hematoxylin violet `#4A3F8F` + eosin pink `#C8375D`. Literal biopsy-slide colours; the product's domain rendered as its chrome. |
| **Type** | Journal serif for the headline and Research Angles; system sans for body; a mono layer for machine/terminal detail. |
| **Measure** | 48rem shell, ~74ch text measure. **Not wider** — 60rem left ~370px of dead space. 17px base. |
| **Tabs** | Latest Intel / Pipelines / Modality Map. |
| **Entity heads** | 1.65rem name, filled eosin `HIGH` chip. |
| **TLDR** | Plain bullets, no chips. |
| **Angles** | All open by default. |
| **Open threads** | Greyed box. |

### Epistemic status is the visual thesis

The design's whole argument: **the reader must see how much to trust each claim without asking.**

- `thesis_impact` renders as a **stripe** — confirms/challenges/neutral visible at a glance.
- Source **tiers render inline**, not in a footnote — primary vs trade vs aggregator is a property of the claim, not of the bibliography.
- Critic rejections render **struck through with a REJECTED stamp**, in the issue, not deleted from it.

## The issue manifest

`issues/index.json` — what the dropdown reads so it needn't open every issue.

```jsonc
{
  "generated_at": "2026-07-16T07:42:11+08:00",
  "issues": [
    {
      "id": "2026-07-16",
      "published_at": "2026-07-16T07:42:11+08:00",
      "coverage_window": {"from": "2026-07-13", "to": "2026-07-16"},
      "status": "published",
      "headline_title": "Merck's $9B Verastem buy resets ADC pricing",
      "surge": {"window": "ASCO 2026", "day": 2, "of": 5},   // absent on baseline runs
      "stats": {"tracked_updates": 9, "sources_cited": 34},
      "flags": ["calendar_stale"]                            // markers the reader should see before opening
    }
  ]
}
```

Rules:

- **Rewritten by `run.py` on every run** — derived, never hand-edited. If it disagrees with the issues on disk, the issues win and the manifest is regenerated.
- **Newest first.**
- **`surge.window` is load-bearing**: it's what lets the dropdown group an ASCO week under its conference name without opening five files.
- **Stubs appear.** A failed run is in the dropdown with `status: "failed"` — a missing issue is worse than a flagged one, and an unexplained gap in the dates is exactly the silent failure the whole design refuses.

## Reader-facing markers

Every marker the pipeline can raise, and where it renders. The rule behind all of them: **a marker renders at the point of the absence, in the reader's path — never only in a footer** ([06](06-validator-and-critic.md#where-a-degradation-renders)).

| Marker | Renders | Raised by |
|---|---|---|
| `No thesis seeded — facts only` | In place of the Research Angle / why-we-care, on that item | A dormant belief slot |
| *"M&A coverage unavailable this cycle — beat failed"* | Inline, in **each section the dead beat fed** | A failed researcher beat |
| `conference calendar stale — surge disabled` | Persistent marker on every issue while stale | An unverified or expired calendar |
| **Uncritiqued banner** | Top of issue: the digest is unvetted and says so | `run.status: published_uncritiqued` |
| **Unresolved-findings banner** | Top of issue, with both the finding and the manager's rebuttal printed | `run.status: published_with_unresolved_findings` |
| **Failed-run stub** | The whole issue: "run failed at stage N", empty sections, link to the log | `run.status: failed` |
| **Surge badge** — `ASCO 2026 · day 2 of 5` | On the issue and in the dropdown grouping | `run.surge` present |

### The two banners

Their exact prose is a design call, but their content is fixed:

- **Uncritiqued** must say the digest was **not adversarially reviewed** and why (the critic was unreachable), without implying the facts are wrong. The digest is good, unvetted, and honest about it.
- **Unresolved findings** must print **both sides**: what the critic found, and what the manager argued back, with the critic's `reaffirmed` adjudication visible. A genuine dispute between two model families is information the reader should have — the banner is not an apology, it's a disclosure.

## Sections, in reader order

1. **TLDR headline** — biggest story + `so_what`.
2. **Stats bar** — derived counts + coverage window.
3. **TLDR bullets** — one per main topic.
4. **Catalyst queue** — *directly under the headline, above the watchlist.* Forward-looking first, retrospective below: the queue is the deliverable and the roster is plumbing. Slip history renders inline per item (*expected 2026-Q2 · slipped twice · now 2026-Q4*).
5. **Tracked watchlist** — per entity: priority, category, sourced summary, developing/concluded, **Research Angle**.
6. **Quiet this cycle** — no-news entities with `cycles_quiet`, **critic catches** (struck through, REJECTED), open threads.
7. **New on our radar** — what they do, development, why we care.
8. **Themes & signals** — cross-cutting patterns.
9. **Elsewhere on the frontier** — incumbent moves.
10. **Sources & method** — beats run and failed, tier counts, paywalled flags.

The catalyst-queue section is the one deliberate departure from the originally dictated order, and it files as a v0.3.0 dashboard delta against the approved prototype. The rest of v3 is not reopened.

## Deferred by decision

- **Horizontal stats bar** as an alternative to the rail — an aesthetic option the owner may want; the rail ships until then.
- **Entity history view** ("show me every ASCO-week issue", "every Merck entry") — the `entity_id` spine and `surge.window` make it queryable, and it is the natural first feature after the SQLite migration.

---

*Provenance: dashboard ticket [#8](https://github.com/cmengu/Research-Swarm/issues/8) (v3 approved); manifest, surge badge and stale marker graduated from map fog under [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381); queue placement from [#17](https://github.com/cmengu/Research-Swarm/issues/17); banners from [#7](https://github.com/cmengu/Research-Swarm/issues/7).*
