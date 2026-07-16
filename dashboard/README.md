# Dashboard prototype

Asset for ticket [#8](https://github.com/cmengu/Research-Swarm/issues/8). **All content fabricated** — this renders the sample from the schema ticket.

Run it: `cd dashboard && python3 -m http.server 8899` → http://localhost:8899

## Stack decision

**Single static HTML file, no framework, no build step.** Vanilla JS renders `issue.json` into the DOM. Rationale: the payload is one document per cycle, rendered once — there is no state to manage and no interactivity beyond a dropdown and anchors. A framework would add a build step to a repo whose whole point is "clone and run". GitHub-Pages-compatible by construction (static files, relative paths).

Production swaps `<script src="_sample.js">` for `fetch('../issues/<id>.json')` and populates the picker from an `issues/index.json` manifest.

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

## Still open (for the spec)

- Rail holds nav + stats; a horizontal stats bar atop the main column is the untested alternative.
- Issue picker needs an `issues/index.json` manifest contract.
