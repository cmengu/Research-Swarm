# 1. Overview and principles

The system, its reader, and the house rules the other eight documents obey. Read this first; everything else assumes it.

## What ResearchSwarm is

A "glorified cron job" with an adversarial conscience. On a schedule, it:

1. wakes, and decides whether today is a run day;
2. fans **six read-only research agents** across oncology biotech and pharma M&A news, in parallel;
3. has **one manager** synthesize their raw facts into a single dated digest — the *issue*;
4. puts that issue through a **deterministic validator** and then an **adversarial critic from a different model family**;
5. publishes to a static dashboard, and commits everything to git.

Between runs it maintains three files about itself: what it watches, what it believes, and what it expects to happen. It edits those files without asking permission, and every edit is a reviewable diff.

## Who reads the output

**An investor/BD-grade reader.** This single decision sets the bar for everything else: every claim is cited, every M&A thesis is argued rather than asserted, valuations are noted where known, and the critic's bar is strict. There is no "busy week" exemption from citation discipline — the most-read issue of the year must not be the least-vetted.

The reader is assumed to be sophisticated, time-poor, and capable of judging arguments for themselves. That is why the system publishes its critic's objections rather than quietly resolving them, and why it records what it predicted rather than grading its own accuracy.

## Domain scope

**Oncology-first, biotech-wide backstop.** Five narrow beats go deep on oncology; a sixth catch-all beat sweeps biotech at large for the story nobody's query was shaped to find. Full beat definitions: [04 — Researchers](04-researchers.md).

## The house rules

Every design decision in this spec descends from these. When a later document seems surprising, it is usually one of these applied strictly.

### 1. A missing piece bends the output; it never kills the run

A flagged issue beats a missing issue. When something fails — a dead beat, an unseeded belief, an unreachable critic, a rotted calendar — the system publishes what it has and **marks the absence at the point where the reader would otherwise misread it**. The only thing that stops a publication is a digest that would mislead the reader about a fact.

This rule has a formal enforcement mechanism: the [degradation register](06-validator-and-critic.md#the-degradation-register). A degradation that isn't in the register doesn't exist, and an unexplained empty section blocks.

### 2. Facts are machine-authored; interpretation is human-seeded and thesis-gated

Researchers report facts. The manager authors interpretation. A human seeds the worldview. The loop may accumulate evidence against a belief and revise it — but it may never invent a belief that no human wrote.

This is applied **field by field**, not document by document. In the catalyst queue, `catalyst` and `expected_window` are machine-authored while `what_it_would_prove` is thesis-gated. In `findings.json`, there is no field in which a researcher *could* express a stance. See [03 — State files](03-state-and-governance.md) and [05 — The manager](05-manager.md).

### 3. Determinism before judgment

Structural checks are decidable by a script with perfect accuracy, for free, in milliseconds. Judgment is not. Asking a model to count fields pays a probabilistic system to do a deterministic job — and it will miss an empty section *inconsistently*, which is worse than not checking at all.

So every gate in the system runs its free deterministic check first, and spends model budget only on what actually needs judgment. This appears three times: at the researcher seam ([04](04-researchers.md#transport)), at the validator ([06](06-validator-and-critic.md#stage-1--the-validator)), and in the receipt rule that decides whether a critic finding is even actionable ([06](06-validator-and-critic.md#the-receipt-rule)).

### 4. Self-maintenance, with a paper trail

The watchlist promotes entities, the thesis evolves, the queue re-cuts monthly — all with **no human approval step**. The biotech universe is small and slow-growing; an approval queue would rot. What replaces approval is **visibility**: every self-edit is a git commit with a reason, appended to that file's own log.

### 5. Subscriptions, not APIs

Workers run on a Claude subscription via headless Claude Code (`claude -p`); the critic runs on a ChatGPT subscription via Codex CLI (`codex exec --json`). This is a cost decision that constrains the architecture: it rules out per-token thinking about budget, and it makes model calls a scarce resource to be spent deliberately (hence rule 3). API billing is a fallback only if subscriptions cannot work, and then with cost caps.

### 6. Read-only is a hard wall

Researchers are constrained by Claude Code permission flags, not by prompt instructions. A researcher **cannot** write a file even if a prompt injection convinces it to try. This forces the transport design in [04](04-researchers.md#transport): findings come back on stdout, and `run.py` is the sole writer.

### 7. Cross-family adversarial review

The critic is a different model family from the workers by design. A Claude critic auditing Claude workers shares their blind spots. When the two families disagree irreconcilably, **both sides are published** — a genuine dispute between two families is information the reader should have, not something either side settles silently.

## The run, end to end

```
  daily heartbeat (OS scheduler, 07:00 local, never rewritten)
        │
        ▼
  run.py — is today a run day?  ──no──►  exit (no issue, no stub, no trace)
        │ yes
        ▼
  read state: watchlist.json, thesis.json, catalyst-queue.json
  verify conference calendar against primary sources
        │
        ▼
  6 researchers in parallel (read-only, sonnet)  ──►  findings.json each, on stdout
        │                                              run.py validates + persists
        ▼
  manager (opus) reads 6 findings + 3 state files  ──►  issue.json draft
        │
        ▼
  ┌──────────────────────┐  fail   ┌──────────────────────────┐
  │ validator            ├────────►│ back to manager (2 max)  │
  │ deterministic, free  │         │ exhausted → failed stub  │
  └──────────┬───────────┘         └──────────────────────────┘
             │ structurally valid
             ▼
  ┌──────────────────────┐ blocked ┌──────────────────────────┐
  │ codex critic         ├────────►│ fix or rebut (2 max)     │
  │ judgment only        │         │ exhausted → banner       │
  └──────────┬───────────┘         └──────────────────────────┘
             │ pass / pass_with_advisories
             ▼
  write issues/<date>.json + update index  ──►  git commit  ──►  dashboard renders
```

Two retry budgets, **two each, separate**. They fail for unrelated reasons, and a trivial JSON slip must never starve the critic of the budget it needed for substance. Worst case is bounded at four manager calls.

## What runs where

| | |
|---|---|
| **Target machine** | A Windows PC, always on. Verified: Codex CLI runs natively on Windows (no WSL) with auto-refreshing subscription auth; a twice-weekly cadence keeps tokens fresh. |
| **Repo** | OS-agnostic. Anyone can clone and run; the only platform-specific surface is one heartbeat registration per OS. |
| **Publishing** | localhost v1, reachable from LAN devices via the PC's IP. Every issue is committed, so a GitHub Pages flip is a later ten-minute change (caveat: Pages is public web unless paid). |
| **Storage** | JSON files. One `issue.json` per cycle in `issues/`. The schema is shaped so a later SQLite swap is mechanical — one row per entity per issue. |
| **Models** | Researchers: `claude-sonnet-5`. Manager: `claude-opus-4-8`. Critic: Codex (`gpt-5.6-codex`). All configurable; these are defaults. |
| **Budget target** | A full run under roughly an hour, at modest subscription usage: six researchers, one manager, up to four manager calls worst case, plus critic passes. |

## Phase 2 — deliberately parked

Not fog, not out of scope: decided to be later.

- **Logged-in browser access** (Claude in Chrome, or Playwright MCP with a persistent profile) to reach paywalled primaries. It exists and works, but it is brittle and opens a prompt-injection surface on an unattended run. v1 cites paywalled facts via free secondary coverage and flags them for manual assessment, accepting about a week of latency.
- **Human steering** of watchlist and thesis via prompt-nudging.
- **SQLite migration** — the schema is already shaped for it.
- **GitHub Pages flip.**
- **Failure push-notifications** — v1 has no alerting infrastructure; a failed run publishes a visible stub and the next run widens its window.

## Out of scope

Beyond this effort entirely: multi-user or team features, auth, hosting for others, and non-biotech domain instances. The format generalizes, but this build is the biotech one.

---

*Provenance: the pre-map capture ([`CAPTURE.md`](../../CAPTURE.md), 16 Jul 2026) and wayfinder tickets [#2](https://github.com/cmengu/Research-Swarm/issues/2) (Windows/Codex verification) and [#24](https://github.com/cmengu/Research-Swarm/issues/24) (compilation rulings).*
