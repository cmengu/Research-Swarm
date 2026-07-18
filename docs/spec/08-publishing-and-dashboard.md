# 8. Publishing and dashboard

How an issue reaches a reader. Covers the static dashboard, its design system, the issue manifest, and every reader-facing marker the pipeline can raise.

**Inputs:** `issues/<program_id>/*.json`, `issues/<program_id>/index.json`.
**Prototype:** the v4 detective IA ([`dashboard/`](../../dashboard/), [#61](https://github.com/cmengu/Research-Swarm/issues/61)) — the approved v3 design system re-architected for the per-program detective, rendering the v2.0.0 schema. The **design system is unchanged and not reopened**; only the information architecture moved.

## Publishing model

| | |
|---|---|
| **v1 reach** | localhost, reachable from LAN devices via the host PC's IP. |
| **Storage** | One `issues/<program_id>/<date>.json` per cycle, committed to git on every run. Every program has its own history. |
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
| **Program identity card** | Above the tabs: name · sponsor · modality/target/MOA/stage/indications — the five-second-test fix ([#61](https://github.com/cmengu/Research-Swarm/issues/61)). |
| **Tabs** | This Issue / Competitor Set / Treatment Landscape (was Latest Intel / Pipelines / Modality Map). |
| **Picker** | Two-level: program switcher + per-program issue dropdown. |
| **Competitor heads** | 1.65rem name, filled eosin `HIGH` chip, **relation badge** alongside. |
| **TLDR** | Plain bullets, no chips. |
| **Read-throughs** | Always visible on every item — not behind a click. |
| **Open threads** | Greyed box. |

### Epistemic status is the visual thesis

The design's whole argument: **the reader must see how much to trust each claim without asking.** It is the best thing in v1's dashboard and survived the pivot intact, gaining program-specific chrome.

- The **relation badge** on every competitor is the typed answer to "why is this a competitor" — rendered on the page, not left to the reader to infer ([decision 6, #49](https://github.com/cmengu/Research-Swarm/issues/49)).
- `read_through.thesis_bearing` renders as a **stripe** — confirms/challenges/neutral visible at a glance.
- Source **tiers render inline**, not in a footnote — primary vs trade vs aggregator is a property of the claim.
- Critic rejections render **struck through with a REJECTED stamp**, in the issue, not deleted from it.
- **Degradations render at the point of the absence** — a China-first competitor's low-confidence marker, a dormant arena, the interest-list rot line.

## The issue manifest

`issues/index.json` — what the dropdown reads so it needn't open every issue.

Per program: `issues/<program_id>/index.json` — what that program's dropdown reads so it needn't open every issue.

```jsonc
{
  "program_id": "hmbd-001",
  "generated_at": "2026-07-18T07:41:00+08:00",
  "issues": [
    {
      "id": "2026-07-18",
      "published_at": "2026-07-18T07:41:00+08:00",
      "coverage_window": {"from": "2026-07-14", "to": "2026-07-18"},
      "status": "published",
      "headline_title": "ESMO 2026 titles land with HER3 and squamous readouts in HMBD-001's lane",
      "surge": {"window": "ESMO 2026", "day": 2, "of": 5},   // absent on baseline runs
      "stats": {"competitors_moved": 2, "sources_cited": 11},
      "flags": ["interest_list_stale"]                       // markers the reader should see before opening
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
| `No thesis seeded — facts only` | In place of the read-through's argument, on that item | A dormant belief slot |
| `conference calendar stale — surge disabled` | Persistent marker on every issue while stale | An unverified or expired calendar |
| *"squamous arena coverage unavailable this cycle — scan failed"* | Inline, in **each section the dead aperture fed** | A failed arena/biology scan (`arena_scan_failed`) |
| **Dormant-arena marker** | On the indication whose arena scan didn't run this cycle | `arena_scan_dormant` |
| **China-first low-confidence marker** | On a competitor tracked via CDE/chictr | `china_feed_partial` |
| **Interest-list rot line** | On the digest, whole-list | `interest_list_stale` |
| **Uncritiqued banner** | Top of issue: the digest is unvetted and says so | `run.status: published_uncritiqued` |
| **Unresolved-findings banner** | Top of issue, with both the finding and the manager's rebuttal printed | `run.status: published_with_unresolved_findings` |
| **Failed-run stub** | The whole issue: "run failed at stage N", empty sections, link to the log | `run.status: failed` |
| **Surge badge** — `ESMO 2026 · day 2 of 5` | On the issue and in the dropdown grouping | `run.surge` present |

### The two banners

Their exact prose is a design call, but their content is fixed:

- **Uncritiqued** must say the digest was **not adversarially reviewed** and why (the critic was unreachable), without implying the facts are wrong. The digest is good, unvetted, and honest about it.
- **Unresolved findings** must print **both sides**: what the critic found, and what the manager argued back, with the critic's `reaffirmed` adjudication visible. A genuine dispute between two model families is information the reader should have — the banner is not an apology, it's a disclosure.

## Sections, in reader order

Above the tabs: the **program identity card** — the five-second-test fix. The **This Issue** tab, top to bottom:

1. **TLDR headline** — the biggest story *for this program* + `so_what`.
2. **Stats bar** — derived counts + coverage window.
3. **TLDR bullets** — one per main topic.
4. **Catalyst queue** — *directly under the headline.* Forward-looking first; slip history renders inline (*expected 2026-Q2 · slipped twice · now 2026-Q4*); items carry a `fed_by` badge.
5. **Competitors that moved** — per typed competitor: the **relation badge** (why it's a competitor), priority, sourced summary, the always-visible **read-through**, an inline `failure`/degradation marker, and a double-click **dossier** (data, dev/BD history, next catalyst, patents-not-tracked).
6. **Indications** — each first-class arena: its setting rivals + SOC, and the `indication × line × biomarker` **treatment landscape**.
7. **Quiet this cycle** — no-news competitors with `cycles_quiet`, **critic catches** (struck through, REJECTED), open threads, and seen-and-dropped-with-receipt.
8. **Newly discovered** — new competitors with a proposed relation and a promotion/interest proposal.
9. **House view** — *subordinate, set apart:* partnership/BD and threat/financing lenses, surviving themes, and the capped ranked blind spots. Not equal billing, not a footer.
10. **Thesis drift** — before/after, the visible worldview log.
11. **Validator & critic report** — both gates' records.
12. **Sources & method** — apertures run and degraded, the registry watch, tier counts, paywalled flags, the interest-list rot status.

Two standing tabs render derived views over the same data: **Competitor Set** (the full typed roster grouped by relation tier; discontinued entries demoted-and-archived inline) and **Treatment Landscape** (per-indication SOC, the slow cross-issue state). The queue-under-headline placement and the house-view subordination are the deliberate departures from v1's dictated order; the rest of the v3 design system is not reopened.

## Deferred by decision

- **Horizontal stats bar** as an alternative to the rail — an aesthetic option the owner may want; the rail ships until then.
- **Entity history view** ("show me every ASCO-week issue", "every Merck entry") — the `entity_id` spine and `surge.window` make it queryable, and it is the natural first feature after the SQLite migration.

---

*Provenance: dashboard ticket [#8](https://github.com/cmengu/Research-Swarm/issues/8) (v3 approved); manifest, surge badge and stale marker graduated from map fog under [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381); queue placement from [#17](https://github.com/cmengu/Research-Swarm/issues/17); banners from [#7](https://github.com/cmengu/Research-Swarm/issues/7).*
