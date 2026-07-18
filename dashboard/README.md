# Dashboard

The reader-facing digest. Design asset for ticket [#8](https://github.com/cmengu/Research-Swarm/issues/8) (v3 approved); made to render **real** published issues under ticket [#36](https://github.com/cmengu/Research-Swarm/issues/36).

Run it: serve the **repo root** over HTTP and open the dashboard from there —

```
python -m http.server 8899        # from the repo root, NOT from dashboard/
# → http://localhost:8899/dashboard/index.html
```

The page reads `../issues/index.json` (the manifest the dropdown lists) and the selected `../issues/<id>.json`, both relative to itself, so it must be served from a root that also serves `issues/`. `fetch` is blocked on `file://`, so opening the file directly will show an in-page "could not load" message rather than a blank — every failure announces itself in the reader's path.

## Stack decision

**Single self-contained HTML file, no framework, no build step, no external asset.** Vanilla JS fetches the two JSON documents and renders them into the DOM. The whole data layer is those two same-origin fetches.

This is deliberately not the old prototype shape. An earlier version inlined its data in a `<script>` block with `_sample.js` as the readable source; when the fetch swap landed, a missing sibling asset could ship an **empty page**. `_sample.js` is now deleted and nothing is load-bearing but the two fetches — verified by serving the repo root and rendering the real `issues/` directory headlessly, never localhost-only.

## Data-shape divergences handled (found against live artifacts)

The dashboard renders defensively where the **live manager** diverges from [`docs/spec/07`](../docs/spec/07-issue-schema.md). Two divergences, pinned by `tests/test_dashboard.py`:

- **`quiet_this_cycle.open_threads`** — spec/07 defines objects `{entity_id, thread, since, next_expected}`; the live manager emits **plain strings**. The renderer handles both.
- **`new_on_radar[].promotion_proposal`** — spec/07 defines `{promote_to_watchlist, reason}`; the live manager emits `{proposed_entity_id, proposed_tier, proposed_priority, reason}`. The renderer handles both.

These are manager-side, not `publish.py`-side; reported, not silently patched.

## Dormant-marker matching is a wording heuristic

Dormant-thesis markers in `research_angle`, `why_we_care` and `what_it_would_prove` are **free manager text**, not a typed field — the manager writes `No thesis seeded — facts only` and omits `thesis_impact`. The dashboard recognises them with a small wording regex (`No thesis seeded…`, `… beat failed`, `… coverage unavailable`, `conference calendar stale`, `… surge disabled`). This is a heuristic, not a contract: if a marker is ever **reworded**, it stops matching and degrades to ordinary prose — it still renders (as text, never a blank), but loses the styled-absence treatment. The durable fix is a typed marker/`degradation` field the renderer can key on; until then, keep the marker wording and the regex in sync.

## Visual system

Palette is **H&E stain** — the actual colors of a tumor biopsy under a microscope: hematoxylin blue-violet (`--hema`) and eosin pink-red (`--eosin`), with violet-biased neutrals. Subject-grounded rather than a generic dashboard skin.

Type: journal serif (Iowan/Palatino/Georgia) for headline and Research Angles — the argued prose; system sans for reading; monospace for the terminal layer (entity slugs, dates, tiers, counts). All are OS-native stacks — no webfonts, so no CDN dependency and no silent fallback.

Both light and dark themes defined at token level (`prefers-color-scheme` + `data-theme` override).

## The design thesis: epistemic status is the loudest thing on the page

This product's differentiator is that it publishes its own doubt, so the UI encodes it:
- **Left border stripe** on each watchlist entry = `thesis_impact` (teal confirms / amber challenges).
- **Source tier is colored inline** — a reader sees `primary` vs `aggregator` without clicking.
- **Critic catches are struck through and stamped `REJECTED · provenance_stale`** — rejections are a feature, displayed not hidden.
- **Thesis drift** renders as before/after with the old belief struck through.
- **Catches** is the one stat rendered in eosin — the number that should draw the eye.

## Verified

Rendered headless in Chrome against the live server: zero page errors, both themes, no horizontal body scroll, all 9 sections populate, and the collapse/expand interaction verified through load → expand-all → collapse-all → manual toggle. One real bug was caught this way and fixed: an em-dash rendered as mojibake until `<meta charset="utf-8">` was added (Python's http.server sends no charset for .html).

## Resolved in reaction (16 Jul 2026)

- **H&E palette stays** — the subject's own material; eosin red is reserved for the things that must draw the eye (critic catches, the headline's so-what, the catches stat).
- **Watchlist angles are collapsible** — `<details>` per entry, **open by default only for `priority: high`**, so a 16-entity issue scans in seconds while the high-priority arguments still read without a click. A rail button expands/collapses all; its label derives from live state.

## v2 changes (16 Jul, from live reaction)

- **Identity block**: original honeycomb-swarm mark (inline SVG, no external asset) + `ResearchSwarm` + `Competitive Intelligence Analysis` tagline. Omnigent's logo was *not* copied — it is their brand mark; this one is drawn for us and matches the name.
- **Tabs**: `Latest Intel` · `Pipelines` · `Modality Map` — the biotech renames of the AI-domain "AI Labs"/"Positioning Map". Only Latest Intel is built; the other two are honest stubs saying what they will hold.
- **Left rail deleted.** Section nav no longer squeezes the content sideways; the page is one centered column. `Expand all angles` moved to the top-right of the watchlist header, where it acts.
- **Masthead follows the dictated structure**: title → rule → summary (+ so-what) → rule → stats on one line → rule → TLDR.
- **"The cycle in bullets" renamed TLDR**; every section is partitioned by a 1px rule.
- **Section subtitles added**: Radar = "Players newly on our radar this cycle"; Themes = "Cross-cutting patterns from this cycle"; and equivalents elsewhere.
- **Issue picker restyled**: `appearance: none`, custom eosin chevron, themed background, hover/focus states — plus `color-scheme` set per theme so the *native popup* and scrollbars follow the theme instead of flashing light-on-dark.
- **Sample data expanded** to 7 watchlist entries, 3 radar, 3 themes, 3 frontier moves, 2 critic catches, 2 thesis drifts — enough to judge density.
- **`sample data` chip retained** (small, in the kicker) rather than the old dashed rail badge. **Deliberate**: the mock names real public companies doing invented things ("Merck acquires Verastem for $9B"). Unlabelled, a shared link reads as genuine M&A news. The eyesore went; the honesty stayed.

## v3 changes (16 Jul, from live reaction)

- **Dead right-hand space eliminated.** The real cause: body copy was capped at ~66ch inside a 60rem shell, so ~370px of every row was empty. Shell narrowed to 48rem and measures widened to ~74ch — measured dead space is now **20px**, down from ~370px.
- **Everything scaled up**: base 16px → 17px; statline .72rem → .84rem with 1.05rem bold figures; TLDR bullets .97rem → 1.08rem; angle prose 1rem → 1.08rem.
- **TLDR is plain bullets** — priority chips removed (priority belongs on entities, not on a scan list); eosin dot markers instead.
- **All Research Angles open by default** (was: high-priority only).
- **Entity headers made prominent**: name 1.28rem → 1.65rem on its own line, tags moved to a dedicated row below, chips enlarged with a *filled* eosin `HIGH` chip so priority reads at a glance instead of sitting flat.
- **Open threads now sit in a greyed box** with a label, separating "still developing" from the rejected-claims material above it.
- **Summaries enriched** — each watchlist entry now carries deal structure, financing, timeline and context rather than one line.

## Still open (for the spec)

- Rail holds nav + stats; a horizontal stats bar atop the main column is the untested alternative.
- ~~Issue picker needs an `issues/index.json` manifest contract.~~ Closed under #36 — the picker reads the manifest `publish.py` regenerates (id, status, surge, flags), newest-first, stubs included.
