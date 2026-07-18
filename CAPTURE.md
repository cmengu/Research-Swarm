# ResearchSwarm — Capture Doc (16 Jul 2026)

> **⛔ HISTORICAL — superseded by [`SPEC.md`](SPEC.md) and `docs/spec/`.** This is the pre-map voice capture, kept for its fuller reasoning. It describes the **v1 market-wide digest** (twice-weekly beats, a flat watchlist, an investor reader). The system was since **re-rooted into a per-program competitive detective** by the pivot map ([#49](https://github.com/cmengu/Research-Swarm/issues/49)) and its second capture, the pivot grilling ([PR #48](https://github.com/cmengu/Research-Swarm/pull/48) — also historical). **Where this document and the spec disagree, the spec wins.** Read it for the origin story, not for current rules.

Raw capture of the initial voice brain-dump, before grooming. Nothing here is final; the Wayfinder map superseded this.

## One-liner

A "glorified cron job": a competitive-intelligence engine. A manager spawns parallel read-only research agents, each on a focus area; an adversarial critic (cross-family — e.g. Codex when workers are Claude) hunts gaps before anything is published to a web dashboard.

## Pipeline (as dictated)

1. **Trigger** — twice weekly (e.g. Mon + Thu), fully automatic, no human in the loop, same full suite every run.
2. **Research fan-out** — one agent per focus area, parallel, read-only. Default model: Sonnet 5 (model must be configurable). Plus a **round-up backstop**: a catch-all sweep of the week's big stories so nothing slips through narrow queries.
3. **Synthesize** — manager (default Opus 4.8, configurable) merges all findings into one tiered digest. Only the manager writes.
4. **Completeness critique** — fresh adversarial agent (ideally Codex, via ChatGPT subscription not API; ditto Claude subscription for the workers) hunts what the workers missed. Must pass before publish; on failure, loop back to synthesis → critique.
5. **Publish** — web dashboard, hosted on localhost for now. Format TBD (to be discussed).

## Domain / focus areas (draft)

Biotech startup space + big pharma, specifically:
- Big pharma **M&A opportunities**
- Startups: **what's at the frontier**
- Labs to track
- Policy & regulation
- Biotech / **oncology** developments
- Frontier incumbents + new product releases
- New entrants

## Constraints & preferences stated

- Use Claude + Codex **subscriptions**, not API billing, where possible.
- Models configurable; defaults = Sonnet 5 (workers), Opus 4.8 (manager).
- Folder name: `research-swarm` (swarm of bees).
- Localhost dashboard first; publishing format to be discussed later.
- Process: groom non-technical implementation first, then technical — before creating any GitHub repo/issues.

## Open questions (pre-grilling)

- Who is the audience/consumer of the digest, and what decision does it feed?
- Dashboard format & retention (history? diffs between runs?)
- What sources are the researchers allowed to read (web search only? paid databases? RSS?)
- What does "pass" mean for the critic — objective checklist or judgment call?
- Where does it run (this Mac? always-on? cloud?)

---

## Digest / issue format (dictated 16 Jul, AI examples → adapt to biotech)

Each run publishes a dated **issue** (e.g. `2026-07-16`); dashboard has a dropdown to browse past issues.

Page structure, top to bottom:
1. **TLDR headline** — biggest story of the cycle + short description.
2. **Stats bar** — counts: tracked-company updates, new entrants, frontier items, # sources, coverage window (prev issue date → this issue date).
3. **TLDR bullets** — one per main topic this cycle.
4. **Tracked watchlist** — per tracked company/lab:
   - priority tag; category tag (biotech: trial readout / deal-M&A / funding / regulatory / people / platform-tech)
   - brief sourced summary; developing vs concluded status; source breakdown
   - **Research Angle** — our opinionated take, argued against an internal focus-area thesis doc (name: "Research Angle", not Hummingbird)
5. **Quiet this cycle** — tracked entities with no news; includes **critic catches** (stories rejected for bad provenance — e.g. a claimed raise tracing to a months-old article — published as rejections) and **open threads** (developing, no update).
6. **New on our radar** — newly surfaced entities: priority, topic (funding/release/etc.), what they do, **why we care** (tied to thesis).
7. **Themes & signals** — cross-cutting patterns; what the industry is consolidating on.
8. **Elsewhere on the frontier** — big incumbents' moves (Pfizer/Roche/AZ-scale, China licensing wave, etc.), sufficiently detailed per move.
9. **Sources & method** — e.g. Endpoints News, Fierce Biotech, STAT, BioPharma Dive, Reuters, FDA/EMA, company PRs.

Implications noted:
- Needs an **internal thesis doc** (standing focus-area point of view) that Research Angles and "why we care" are argued against.
- Critic rejections are **published** in the issue, not silently dropped.

---

## Decisions locked during grilling (16 Jul 2026)

1. **Destination (Wayfinder)** — planning only: all non-technical + technical decisions resolved and written down as context. No spec, no issues, no code yet; user runs tickets → spec later.
2. **Audience** — investor/BD-grade output: every claim cited, M&A theses argued, valuations noted. Strict critic bar.
3. **Domain scope** — oncology-first deep agents; biotech-wide catch-all backstop.
4. **Agent roster (v1)** — 5 beats + backstop: (1) Pharma M&A & dealmaking incl. licensing, (2) Oncology startup frontier, (3) Clinical & scientific developments, (4) Policy & regulation, (5) Incumbent moves & new entrants; + catch-all weekly sweep.
5. **Digest format** — dated issues, see "Digest / issue format" section above.
6. **Watchlist** — fully self-maintaining: we seed ~10–20 entities; system auto-promotes radar entities into its own schema, no approval step. Human steering via prompts = phase 2. Biotech universe is small, growth will be slow.
7. **Thesis doc** — seeded once by us, self-evolving each cycle; every revision logged visibly in the issue ("thesis updates this cycle") so drift is watchable.
8. **Critic gate** — structured verdict: blocking (bad provenance, uncited claims, missed must-cover, empty section) vs advisory. Blocking → loop to synthesis, max 2 retries; still failing → publish with "unresolved critic findings" banner. Flagged issue > missing issue.
9. **Sources** — v1 = free public web, trust-tiered: primary (FDA/EMA/ClinicalTrials.gov, SEC, company PRs, PubMed/bioRxiv/medRxiv) > trade press (Endpoints, Fierce, STAT free, BioPharma Dive, Reuters) > aggregators. Paywalled facts (STAT+, Endpoints premium, PitchBook, Evaluate) cited via secondary coverage + link + best-effort assessment, flagged "primary paywalled — assess manually"; accept ~1-week latency. **Phase 2 ticket: logged-in browser access (Claude in Chrome / Playwright MCP with persistent profile) — exists but brittle + prompt-injection risk for unattended runs.**

## Technical decisions locked (16 Jul 2026, continued)

10. **Runtime** — target machine is a **Windows PC** (not this Mac), but the repo is OS-agnostic: anyone can clone and run. Subscription-first: headless Claude Code (`claude -p`) on the Claude subscription; Codex CLI critic on the ChatGPT subscription (**Windows support likely via WSL — verify = research ticket**). API fallback only if subscriptions can't work, with cost caps.
11. **Storage** — JSON files for v1: one structured `issue.json` per cycle in `issues/`; schema designed so a later SQLite swap is mechanical. Front-end format can be encoded as a project skill so future agents render consistently.
12. **Trigger + orchestration** — OS-native scheduler fires (Windows Task Scheduler; repo ships a `schedule-install` helper with cron/launchd equivalents) → one thin OS-agnostic Python orchestrator (`run.py`) follows the recipe: parallel read-only researcher calls → manager synthesis → Codex critique → max-2 retry loop → write issue.json. Per-stage logs. Models/beats swap via config file.
13. **Publish reach** — localhost v1 (LAN devices via the PC's IP); every run commits issue.json to the repo, so GitHub Pages is a later 10-minute flip (caveat: Pages = public web unless paid private setup).
14. **Read-only enforcement** — researchers constrained by Claude Code permission flags (hard wall), not prompt instructions.
15. **Cross-run state** — watchlist, thesis doc, open threads, last-issue date = version-controlled state files in the repo; every self-update is a visible git diff.
16. **Failure handling** — failed run publishes a stub issue ("run failed at stage N — see log") visible in the dropdown; next successful run widens its coverage window to include missed days. No alerting infra in v1.
17. **Defaults (flippable)** — runs Mon + Thu 07:00 local; full run targets < ~1 hour and modest subscription usage (6 researchers + manager + critic loops).

## Not yet specified (fog — for future tickets)

- **issue.json schema** — the exact field-level contract (first spec-phase task; digest format above is the blueprint).
- **Seed watchlist** — the actual initial ~10–20 biotech/pharma entities (curation session).
- **Thesis v1** — the actual seeded point-of-view document (co-writing session).
- **Codex-on-Windows verification** — research ticket: native vs WSL, headless invocation, subscription auth persistence for unattended runs.
- **Researcher prompt templates** — per-beat prompts, trust-tier citation rules, output contract.
- **Critic rubric** — the concrete blocking-findings checklist Codex judges against.
- **Dashboard build** — static app implementation, visual design (dataviz/artifact-design skills apply).
- **Phase 2 parked items** — logged-in browser access (Claude in Chrome / Playwright MCP profile); human steering of watchlist/thesis via prompt-nudging; SQLite migration; GitHub Pages flip; failure push-notifications.

## Out of scope (this effort)

- Multi-user/team features, auth, hosting-for-others.
- Any non-biotech domain instance (the format generalizes, but this map builds the biotech one).
