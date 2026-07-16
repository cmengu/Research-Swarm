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

Rendered headless in Chrome: no page errors, both themes, no horizontal body scroll, all 9 sections populate from the sample.

## Open for reaction

- Is the H&E palette right, or too clinical//too pink?
- Rail is sticky nav + stats — or should stats be a horizontal bar across the top of the main column?
- Should the watchlist be collapsible (scan headlines, expand angles), or always fully expanded as now?
