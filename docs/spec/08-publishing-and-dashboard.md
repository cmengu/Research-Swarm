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

## The program registry

`issues/index.json` — **what the program switcher reads.** One row per program, so the switcher can label every detective without opening any program's manifest.

```jsonc
{
  "generated_at": "2026-07-18T07:41:00+08:00",
  "programs": [
    {
      "program_id": "hmbd-001",
      "display_name": "HMBD-001",
      "sponsor": "Hummingbird Bioscience",
      "mechanism": "HER3 × EGFR signalling blockade",  // the five-second test
      "latest_issue": "2026-07-18",                    // null — never run
      "latest_published_at": "2026-07-18T07:41:00+08:00",
      "issue_count": 4,
      "flags": ["interest_list_stale"]
    }
  ]
}
```

This is a **third kind of file**, and naming that is the point. Issues are immutable and per-program; manifests are derived and per-program; the registry is derived and **cross-program**, rewritten by whichever run happened last. Rules:

- **Regenerated wholesale on every run**, from `config/programs/*.toml` **joined with** the issues on disk. Never row-patched. A run touches one program but rewrites every row, so a stale row is impossible by construction rather than by locking — the same reconciliation the manifest already uses, inherited rather than reinvented.
- **Config ⋈ state is the join, and config is the left side.** A program exists because it has a `.toml`, not because it has published.
- **A program that has never run still appears**, with `latest_issue: null` and `issue_count: 0`. The switcher shows it and the page renders its identity card over a "no issues yet" empty state. Fail-visible over clean: a detective that exists but is invisible is exactly the silent absence this design refuses, and it is the program-altitude twin of *stubs appear*.
- **`sponsor` and `mechanism` ride here on purpose** — they are the five-second test, and carrying them in the registry lets the identity card paint before the issue is fetched.
- **Written by `publish.py`, called by `run.py`.** The sole-writer invariant is unbroken: `publish.py` is `run.py`'s library, not a second actor.

*Resolves the spec bug where this line and the per-program manifest claimed the same job: both files exist, and they feed two different dropdowns.*

## The issue manifest

Per program: `issues/<program_id>/index.json` — what that program's **issue** dropdown reads so it needn't open every issue.

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

## The data layer

The page fetches **registry → program manifest → issue**. Three hops, each one small, each one a file the pipeline already writes.

**First paint does not wait for the issue.** The registry alone renders the switcher and the program identity card — that is what `sponsor` and `mechanism` are doing in the registry — so the reader sees which detective they are looking at while the issue is still in flight. The issue body renders a skeleton until its fetch lands.

### The prototype/production split

The tension is real and neither previous answer was right: v3 was production and could not be opened; v4 is a prototype and fetches nothing. The resolution is that **the page branches on `location.protocol`**, because that is the one fact that actually distinguishes the two situations:

- **On `file://`** — fetch is blocked by the browser, by design. The page uses its inlined sample and renders a **persistent banner saying so**: this is prototype mode, showing a fixed sample, not published data. `dashboard/index.html` stays double-clickable with no server and no sibling files.
- **On `http(s)://`** — the page fetches. A failure at any stage renders `showError` with the "serve the repo root over HTTP" hint, and **the inlined sample is never substituted.**

That last clause is the whole lesson of the `_sample.js` incident, and it is worth being precise about why this is not that mistake wearing a new hat. The original failure was an *external* asset that did not travel, producing an empty page that looked deliberate. Here the sample is **inlined**, so it always travels; and it is **protocol-gated**, so it can never masquerade as published data. The failure mode that shipped an empty page was silence — a fetched page that quietly falls back to a sample is silence too. A page that says *"prototype mode"* or says *"could not load"* is not.

**Failure is graded by stage**, because the stages fail differently:

| Fails | Page shows |
|---|---|
| Registry | Whole-page error — nothing can be labelled, not even the switcher |
| Program manifest | Switcher renders; that program's issue list is an error state; other programs remain selectable |
| Issue | Identity card and switcher hold; the issue body is an error state with the issue id that failed |

**The pickers fetch at different depths.** Changing the *issue* is one hop (that issue). Changing the *program* is two (its manifest, then its latest issue) — the registry is already in hand and is not re-fetched.

**First-publish bootstrap.** `issues/<program_id>/` exists but is empty, or the program has never run: the identity card renders from the registry and the issue body is an explicit empty state. Run #1 is not special-cased — it is this state until it publishes.

## Vocabulary homes

v3 carried curated single-home constants (`STATUS`, `FLAG_LABEL`, `FINDING`, `BEAT_SECTIONS`, `MARKER`); v4 deleted them. They are **restored in the page**, with two changes the v2 schema earns:

- **The frontend owns its own reader-facing wording, and Python emits no vocabulary block.** Serving the vocabulary would move a presentation concern into `publish.py`; the page is the only consumer, so the interface belongs where it is read. The duplication is accepted deliberately — it is wording, not truth.
- **`MARKER`'s regex is retired.** v3 conceded it was "HEURISTIC, not a contract" and that a reworded marker silently degraded to prose. v2 carries `degradation.kind` and `run.status` as **typed fields**, so the chrome keys on the type and never greps the prose.
- **`BEAT_SECTIONS`' successor is derived, not hand-listed.** Apertures are `relation-tier × scope`, so which sections a dead aperture leaves thin follows from its scope rather than from a maintained table.
- **An unknown kind renders visibly** — a neutral chip carrying the raw kind — rather than v3's render-nothing default. A marker the page does not recognise is exactly when the reader most needs to know something was raised.

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
