# Dashboard prototype

Asset for the dashboard tickets [#8](https://github.com/cmengu/Research-Swarm/issues/8) (v1–v3 market-digest design) and [#61](https://github.com/cmengu/Research-Swarm/issues/61) (v4 per-program detective IA). It now renders the **v2.0.0 program-issue schema** ([#60](https://github.com/cmengu/Research-Swarm/issues/60)) — the inline data is a **verbatim copy** of [`docs/schema/sample-issue-hmbd-001-2026-07-18.json`](../docs/schema/sample-issue-hmbd-001-2026-07-18.json) (real public HMBD-001 facts; read-throughs illustrative).

Run it two ways, and they deliberately do different things:

- **Double-click `dashboard/index.html`** (`file://`) → **prototype mode**: the inlined sample, under a persistent banner saying so. No server, no sibling files, zero fetches.
- **Serve the repo root** (`python3 -m http.server` from the repo root, then open `/dashboard/`) → **production**: the page fetches published data.

**The page is self-contained either way** — one file, no framework, no build step, no external asset, no sibling file. The sample is *inlined* so it always travels, and *protocol-gated* so it can never masquerade as published data.

## Stack decision

**Single static HTML file, no framework, no build step.** Vanilla JS renders `issue.json` into the DOM. Rationale: the payload is one document per cycle, rendered once — there is no state to manage and no interactivity beyond a dropdown and anchors. A framework would add a build step to a repo whose whole point is "clone and run". GitHub-Pages-compatible by construction (static files, relative paths).

The data layer is **three hops** — registry → program manifest → issue. See "v5 changes" below.

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

## v4 changes (18 Jul 2026, the detective IA — ticket [#61](https://github.com/cmengu/Research-Swarm/issues/61))

Re-architects the IA from a market digest to a **per-program detective**, rendering the v2.0.0 schema. The design system (H&E palette, type, 48rem/74ch measure, epistemic-status visuals) is **unchanged and not reopened** — only the information architecture moved. The five-second test ("does a cold visitor know what this is for?") was the actual bug this map fixed; it drove every call below.

The seven questions #61 asked, and how the prototype answers each:

1. **Top-level noun → program.** A **program identity card** sits above the tabs: name · sponsor · one-line mechanism · modality/target/MOA/stage/indications. It is the five-second-test fix — a cold visitor reads "competitive dossier for the drug HMBD-001" instantly. Picker is now **two-level**: a program switcher + a per-program issue dropdown.
2. **The double-click → progressive-disclosure dossier**, not a modal. Each competitor card has a `<details>` dossier (available data · development & BD history · next catalyst · patents) and a native **double-click** handler that toggles it. A modal was rejected: it traps focus and blocks side-by-side comparison, and progressive disclosure is what's honestly reachable in a single static file. Patents render as "not tracked in v1" (source-set [#51](https://github.com/cmengu/Research-Swarm/issues/51) ruled them out) rather than a faked panel.
3. **Failed programs → not a tab.** Failure is per-indication and two-tier (competitor `failure` field, [#54](https://github.com/cmengu/Research-Swarm/issues/54)), so it renders **inline as a demoted/archived state** on the competitor card. HER3-DXd shows an `indication_tier` failure (EGFR-NSCLC BLA withdrawn) while the program-tier entity survives — a real two-tier example. A top-level "failed programs" tab would be the wrong shape.
4. **Treatment landscape → inside the program**, within each first-class indication block (arena + the `indication × line × biomarker` table), and also surfaced as a standing **Treatment Landscape** tab for the cross-issue slow-state view. Efficacy numbers show their primary-source tier inline.
5. **House view → subordinate but present.** Wrapped in a visually set-apart, lighter `#houseWrap` panel below the program content — not equal billing (which drags back to "too broad"), not a hidden footer (which buries the BD/threat signal the reader wants). Two lenses + themes + capped blind-spots.
6. **Epistemic-status visual thesis survives intact** — the left-border `thesis_bearing` stripe (now on read-throughs), inline source tiers, struck-through `REJECTED` critic catches, before/after thesis drift. Plus new epistemic chrome: the **relation badge** (the typed "why it's a competitor"), inline **degradation markers** at the point of absence, and the interest-list **rot** status in the footer.
7. **Five-second test — the program identity card is the answer.** The relation badge on every competitor makes "why is this here" legible without a click, which is the same complaint at the item level.

**Tabs re-cast:** `This Issue` (the dated program brief) · `Competitor Set` (the standing typed roster, grouped by relation tier — mechanism/target twin, setting rival, benchmark, platform threat; discontinued entries demoted-and-archived inline) · `Treatment Landscape` (per-indication SOC, slow state). All three are **real derived views** over the same data — no "not built yet" stubs.

**The read-through is the load-bearing new component**: every competitor, arena, house and discovery item renders its `read_through` block **always visible** (not behind a click) — the relation badge + the "what this means for HMBD-001" prose + the `established_by` provenance. That is the stakeholder's #1 ask ("why it's a competitor, on the page") made structural.

### v4 verification

Rendered headless in Chrome against the published file (`--dump-dom` + screenshot): the inline `ISSUE` is a byte-verbatim copy of the schema sample (asserted in CI-style check), JS parses, **20/20 content checks pass**, every render container populates (no renderer threw), and the program-identity/house-view layout confirmed visually in both themes. No horizontal body scroll.

## v5 changes (18 Jul 2026, the data layer — tickets [#81](https://github.com/cmengu/Research-Swarm/issues/81) + [#82](https://github.com/cmengu/Research-Swarm/issues/82))

Restores the data layer v4's IA rewrite dropped, per [spec/08](../docs/spec/08-publishing-and-dashboard.md). **The design system is untouched** — this ticket restored plumbing, it did not redesign anything.

**Three hops**: `issues/index.json` (the cross-program registry) → `issues/<program_id>/index.json` (that program's issue manifest) → `issues/<program_id>/<date>.json` (the issue). **First paint does not wait for the issue**: the registry alone renders the program switcher *and* the identity card — which is exactly what `sponsor` and `mechanism` are doing in the registry — and the issue body shows a skeleton until its fetch lands.

**The prototype/production split.** The page branches on `location.protocol`, because that is the one fact that distinguishes the two situations:

| | `file://` | `http(s)://` |
|---|---|---|
| Data | the inlined sample | fetched |
| Says so | persistent prototype banner + kicker chip | — |
| On failure | n/a (nothing is fetched) | error state, with the serve-over-HTTP hint |

**The inlined sample is never substituted for a failed fetch.** That is the whole lesson of the `_sample.js` incident, and it is not that mistake wearing a new hat: the original failure was an *external* asset that did not travel, producing an empty page that looked deliberate. Here the sample is inlined (so it always travels) and protocol-gated (so it cannot pose as published data). The failure mode that shipped an empty page was **silence** — and a fetched page that quietly falls back to a sample is silence too. Prototype mode announces itself; a failed fetch announces itself.

**Failure is graded by stage**, because the stages fail differently:

| Fails | Page shows |
|---|---|
| Registry | Whole-page error — nothing can be labelled, not even the switcher |
| Program manifest | Switcher renders; that program's issue list errors; **other programs stay selectable** |
| Issue | Identity card and switcher hold; the issue body errors, naming the issue id that failed |

**The pickers fetch at their real depths**: changing the *issue* is one hop; changing the *program* is two (its manifest, then its latest issue) — the registry is already in hand and is **not** re-fetched. Both are verified by fetch-count assertions.

**First-publish bootstrap**: a program with `latest_issue: null` still appears in the switcher (a program exists because it has a `.toml`, not because it has published), its identity card renders from the registry, and the issue body is an explicit empty state. Run #1 is not special-cased.

**Vocabulary homes restored** (#82) — `STATUS`, `FLAG_LABEL`, `FINDING`, `BANNER`, `DEGRADATION`, with v3's "one home, no bare literals elsewhere" discipline. The frontend owns its reader-facing wording and Python emits no vocabulary block; the duplication is deliberate, because it is wording, not truth. Two changes v2 earns:

- **`MARKER`'s regex is retired.** v3 conceded it was "HEURISTIC, not a contract" and a reworded marker silently degraded to prose. v2 carries `degradation.kind` and `run.status` as **typed** fields, so the chrome keys on the type and never greps prose.
- **An unknown kind renders visibly** — a neutral chip carrying the raw kind — rather than v3's render-nothing default. A marker the page does not recognise is exactly when the reader most needs to know something was raised.
- **`BEAT_SECTIONS`' successor is derived, not hand-listed**: an aperture's scope *is* the indication id, so which sections a dead aperture leaves thin follows from its scope rather than from a table that rots.

### v5 verification

No browser available, so verified statically and by execution under a minimal DOM shim:

- **Zero external asset references and zero sibling-file dependencies** (no `<link>`, no `<script src>`, no `@import`, no `url()`); `dashboard/` contains only `index.html` and this README.
- `node --check` on both extracted `<script>` blocks and on the concatenated program.
- **The `file://` path executes with `fetch` stubbed to throw** — it never fires. Banner shown, identity card painted, all 13 render containers populated, both pickers built.
- **All five `http://` scenarios asserted**: happy, dead registry, dead manifest, dead issue, first-publish. Every failure scenario additionally asserts the **inlined sample did not leak** into the page.
- Fetch-count assertions for picker depth (1 hop / 2 hops, registry not re-fetched), plus the unknown-degradation chip, the failed-run stub, and `labelFor`'s status/flag/surge wording.

## Still open (for the spec)

- Rail holds nav + stats; a horizontal stats bar atop the main column is the untested alternative.
- ~~Issue picker needs a per-program `issues/<program_id>/index.json` manifest contract.~~ **Resolved in v5** — that contract plus the cross-program registry are specified in [spec/08](../docs/spec/08-publishing-and-dashboard.md) and consumed by the page.
- The interest editor is a **separate local runtime surface** ([#55](https://github.com/cmengu/Research-Swarm/issues/55)), deliberately not part of this static digest — the digest stays read-only.
- Multi-program packaging (one digest, N programs) is deferred to [#59](https://github.com/cmengu/Research-Swarm/issues/59); the program switcher is stubbed for the single pilot program.
