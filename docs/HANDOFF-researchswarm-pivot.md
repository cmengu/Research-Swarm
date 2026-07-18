# Handoff — ResearchSwarm per-program detective pivot (18 Jul 2026)

For a fresh agent picking up ResearchSwarm. Read this, then `SPEC.md`, then work. Everything below is **shipped as draft PRs, not yet merged** — `main` is still the v1 market-digest spec until the chain merges.

## The one-line pivot

The old system published because something *happened* (a market-wide oncology/M&A digest, twice-weekly beats). It was **re-rooted into a per-program competitive detective**: the reader is one drug's decision-owner, competitors are **typed** by their relation to the program, and every competitor carries a **read-through** (why it's a competitor + what it means for the program). Pilot program: **HMBD-001** (Hummingbird Bioscience anti-HER3 IgG1 signalling antibody).

Pivot map = [#49](https://github.com/cmengu/Research-Swarm/issues/49); children #50–#61 all resolved. Their decisions are the authority — **do not re-litigate them.**

## What shipped this session (stacked draft PRs — MERGE IN ORDER)

| PR | Branch | Ticket | Deliverable |
|----|--------|--------|-------------|
| **#64** | (source-set) | #51 | `docs/research/program-detective-source-set-2026.md` — the credible feed set (registry-diff, China blind spot). Already merged? verify. |
| **#65** | `build/60-issue-schema-v2` | #60 | **issue.json v2.0.0** — `docs/spec/07-issue-schema.md` rewritten + hand-built real-facts sample `docs/schema/sample-issue-hmbd-001-2026-07-18.json`. |
| **#66** | `build/61-dashboard-ia` | #61 | **Dashboard v4 detective IA** — `dashboard/index.html` renders the v2 sample. |
| **#67** | `build/62-compile-spec` | #62 | **Spec compilation** — all 9 `docs/spec/` docs + `SPEC.md`/`README.md` revised, `CAPTURE.md` marked historical, new `scripts/check-spec-links.py`. |

Each PR is stacked on the previous (base = the prior branch), so **merge #64 → #65 → #66 → #67**. Each PR body has the full rationale. Diffs are scoped: #67 touches only spec/capture/tooling, #66 only the two dashboard files, #65 only schema doc + sample.

## The v2 model (the vocabulary you must use)

- **Program** — one detective per drug. Config: `config/programs/<id>.toml` (identity, `moa`, indications, per-program cadence, `seed_competitors`). Human-owned; the system proposes but never writes it. **Adding a program = one config file.**
- **Typed competitor** — the relation *is* the answer to "why is this a competitor," rendered on the page. Five relations, two tiers: `mechanism_twin` / `target_twin` (program-level biology), `setting_rival` / `benchmark_soc` (indication-level arena), `platform_threat` (**house-level, company-unit** — leaves the program instance). `moa` separates a mechanism twin (same target AND MOA) from a target twin (same target, different MOA — e.g. HER3-DXd is a *target* twin, so an ADC's win validates HER3 expression, NOT HMBD-001's signalling thesis).
- **Read-through** — the load-bearing new object. Structured field on every competitor/arena/house/discovery item: `{relation|lens, thesis_bearing, text, established_by}`. **Presence is a deterministic validator check** (`missing_read_through`, blocking); **quality is a critic advisory** (`weak_read_through`). This is the admission rule.
- **Admission rule (ternary receipt)** — every scanned item lands in exactly one place, nothing silently omitted: (1) admitted with a read-through, (2) a **capped ranked blind spot** (`house_view.blind_spots`, N=5, overflow never silent), or (3) **dropped with a receipt** (`quiet_this_cycle.dropped_with_receipt`).
- **Apertures replace beats** — `1 + N + 1`: one indication-blind **biology scan** (target+moa), one **arena scan per indication**, one cheap **house sweep**. Cost = `FIXED + N × arena scan`, scales with apertures not programs.
- **House view** — replaces `elsewhere_on_frontier` + `themes_and_signals`. Subordinate section, two **lenses** (partnership/BD, threat/financing) + surviving themes + blind spots. Not equal billing, not a footer.
- **Interest list** — `config/interests.toml`, the **one steering wheel**: `tier` (strong/watching) + free-text `note`. Steers admission+steering+sort, never cadence, never scan depth. **Human-owned via a separate editor** (not the static digest); system proposes refine/prune/add, human confirms. Rot = fail-visible after 6 months.
- **State split** — shared facts in `state/entities/<entity_id>.json` (program-agnostic, materialized index over the issue archive, corrections append), per-program relation edges in `state/programs/<id>/edges.json` (`(program_id × entity_id) → relation + read_through`). This is why cost scales with apertures.
- **Registry-diff** — a first-class **input class** (not a source URL): ClinicalTrials.gov v2 `lastUpdatePostDate` polled for tracked NCTs surfaces a competitor's move weeks before news. China-first assets (SDP0505, ivonescimab) are the named blind spot — CDE/chictr, language-gated, no clean free feed.
- **Failure** — two-tier, archival, never deletion: `program_tier` (whole entity) vs `indication_tier` (one setting archives, entity survives). Rendered inline as a demoted state, **not a "failed programs" tab**.

## What's next (the actual build work)

Everything above is **spec + prototype**. The pipeline itself (`researchswarm/`, `run.py`) is built for v1 and, per [#52](https://github.com/cmengu/Research-Swarm/issues/52), re-roots with **zero code changes** — program identity lives only in `state/` data and prompts. So the remaining work is a **state-shape + prompt-framing change**, resumed as `build` tickets. The build order is in `docs/spec/09-orchestrator.md#build-order`:

1. Program config + the `state/` split (entities/ + programs/<id>/); extend the cross-file join check.
2. Re-frame `prompts/researcher.md` — apertures, registry watch, competitor+interest coverage duty.
3. Re-frame `prompts/manager.md` — author read-throughs, type competitors, house view, admission rule.
4. Extend the validator — 4 new blocking checks (`missing_read_through`, `untyped_competitor`, `blind_spot_overflow`, `landscape_number_unsourced`) + new degradation rows.
5. Extend the critic — `weak_read_through`, `relation_miscast`.
6. Publish per-program (`issues/<program_id>/`), edge/entity state edits.
7. Wire the dashboard v4 to real issues.
8. Cadence (per-program dial) + registry poll + surge.
9. **Add program #2 = drop one config file, edit nothing else** (the modularity test).

New build tickets for this don't exist yet — they'd be created via the map's ticket flow after the spec merges.

## Deferred by decision — DO NOT quietly rule these

Named open by the map; carry forward, don't invent answers:
- **The thesis under a program roof** — does a program carry its own angle? v1 does the minimum that forecloses neither (read-throughs feed the existing drift engine via `thesis_bearing`).
- **The interest editor surface** — a separate local runtime tool; build-time architecture deferred.
- **The seeded 22-entity watchlist migration** into the per-program competitor model — a curation session, not a compilation ruling.
- **Multi-program packaging** (one digest, N programs) — deferred to #59.

## Provisional defaults (⚑ — flip freely, each is one line)

Monthly per-program cadence · retention 24 runs · lookback floor 12 issues · stale-calendar N=8 · cold-start 7 days · interest rot 6 months · blind-spot cap N=5 · 4/6 thesis stances `agent_draft_delegated`.

## Verification tools (all pass right now)

- **`python3 scripts/check-spec-links.py .`** — resolves all 150 intra-repo links/anchors. Run after any spec edit. (Slug rule: GitHub-style; underscores are KEPT in anchors, em-dash headings produce double-hyphens.)
- **Dashboard render:** `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --dump-dom file://$(pwd)/dashboard/index.html` — the inline `ISSUE` must stay a byte-verbatim copy of the schema sample.
- **Schema sample:** `python3 -c "import json; json.load(open('docs/schema/sample-issue-hmbd-001-2026-07-18.json'))"`.

## Gotchas / context

- **Working in a worktree** at `.claude/worktrees/wf-51-source-set` — the stacked branches live here. `main` is the pre-pivot v1 spec.
- **Real facts, verified in-repo:** the sample + dashboard use real public facts grounded in `docs/research/program-detective-source-set-2026.md` (HER3-DXd BLA withdrawn May 2025; SDP0505 HER3×c-Met bispecific ADC; ivonescimab HARMONi-6 OS HR 0.66 at ASCO 2026; ESMO title-drop 17 Jul 2026). Read-throughs are illustrative; facts are real. The "Merck entanglement" is two companies: **Merck & Co./MSD** owns HER3-DXd (target-twin rival via the Daiichi license); **Merck KGaA** markets cetuximab (HMBD-001's Phase 1b combo agent). Keep them distinct.
- **v1 live-run lessons (still true):** critic model id is **`gpt-5-codex`** (NOT `gpt-5.6-codex` — that was hallucinated at v1 planning and lingers in some v1 doc text; the pivot docs kept the placeholder, fix on build); `codex exec --output-schema` uses OpenAI strict mode (every object `additionalProperties:false`, all props required, optionals nullable); `runs/` is gitignored working papers — keep OUT of commits; tests run as `uv run python -m pytest`.
- **Design system is LOCKED** — H&E palette (hematoxylin #4A3F8F + eosin #C8375D), journal serif/sans/mono, 48rem/74ch, epistemic-status-as-visual-thesis. The pivot changed the dashboard's IA only, never the design system.

## First moves for the next agent

1. `gh pr view 67` (and 65, 66) — read the shipped work.
2. Confirm merge order #64→#65→#66→#67; merge if the owner approves.
3. Once merged, `main` = the pivot spec. Then generate build tickets from `docs/spec/09-orchestrator.md#build-order`.
