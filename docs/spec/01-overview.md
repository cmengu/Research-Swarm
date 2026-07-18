# 1. Overview and principles

The system, its reader, and the house rules the other eight documents obey. Read this first; everything else assumes it.

## What ResearchSwarm is

A "glorified cron job" with an adversarial conscience, pointed at **one drug program at a time**. On each program's schedule, it:

1. wakes, and decides whether any program is due today;
2. aims a small set of read-only research **apertures** at that program's competitive board, in parallel — one indication-blind biology scan, one arena scan per indication, one cheap house sweep;
3. has **one manager** synthesize their raw facts into a single dated **program issue** — with a subordinate house view — in which every competitor carries a **read-through**;
4. puts that issue through a **deterministic validator** and then an **adversarial critic from a different model family**;
5. publishes to a static dashboard, and commits everything to git.

Between runs it maintains its memory: a shared layer of competitor facts, the per-program relation edges that type each competitor, a human-set interest list, a worldview, and a queue of expected catalysts. It edits most of these without asking permission, and every edit is a reviewable diff.

**The re-rooting in one line:** the predecessor published because something *happened*; this system publishes because something happened *to a specific program*. The pipeline survived the pivot; the product it emits changed ([provenance](#the-pivot-that-produced-this)).

## Who reads the output

**The program's decision-owner.** For the pilot, that is the team steering HMBD-001 — not a generalist investor. This single decision sets the bar for everything else: the reader owns **one decision** (how to position, prioritize, and de-risk their program) and is served **two evidence streams** — the competitive stream (the typed competitors) and the value stream (the house view's partnership/BD and threat/financing lenses). Every claim is cited, every read-through is argued rather than asserted, and the critic's bar is strict. There is no "busy week" exemption from citation discipline.

The reader is sophisticated, time-poor, and capable of judging arguments for themselves. That is why the system publishes its critic's objections rather than quietly resolving them, why it records what it predicted rather than grading its own accuracy, and why it names what it *cannot* see rather than presenting a partial board as complete.

## Domain scope

**Oncology, one program deep.** The apertures go deep on a single program's biology and indications; a cheap house sweep watches the wider oncology board for partnership, threat, financing and the blind spots the narrow scans miss. The pilot is HMBD-001 (anti-HER3); the format generalizes to any oncology program, and adding one is a config file ([09](09-orchestrator.md#scaling-to-many-programs)). Full aperture definitions: [04 — Researchers](04-researchers.md).

## The house rules

Every design decision in this spec descends from these. When a later document seems surprising, it is usually one of these applied strictly. **These rules survived the pivot unchanged — they are the machinery's philosophy, not the product's shape.**

### 1. A missing piece bends the output; it never kills the run

A flagged issue beats a missing issue. When something fails — a dead aperture, an unseeded belief, an unreachable critic, a rotted calendar, a competitor the system cannot yet place — it publishes what it has and **marks the absence at the point where the reader would otherwise misread it**. The only thing that stops a publication is a digest that would mislead the reader about a fact.

This rule has a formal enforcement mechanism: the [degradation register](06-validator-and-critic.md#the-degradation-register). A degradation that isn't in the register doesn't exist, and an unexplained empty section blocks. Its newest expression is the **admission rule**: an item publishes with a read-through, or as a capped ranked blind spot, or dropped with a receipt — never silently absent ([06](06-validator-and-critic.md#the-admission-rule)).

### 2. Facts are machine-authored; interpretation is human-seeded and thesis-gated

Researchers report facts. The manager authors interpretation — including every **read-through**. A human seeds the worldview and the interest list. The loop may accumulate evidence against a belief and revise it, and may **propose** competitors, promotions, and interests — but it may never invent a belief no human wrote, and it never writes the interest list or a program's aperture itself.

This is applied **field by field**, not document by document. On a competitor record, `summary` is machine-authored fact while `read_through` is the manager's interpretation. In `findings.json`, there is no field in which a researcher *could* express a stance. See [03 — State and config](03-state-and-governance.md) and [05 — The manager](05-manager.md).

### 3. Determinism before judgment

Structural checks are decidable by a script with perfect accuracy, for free, in milliseconds. Judgment is not. Asking a model to count fields pays a probabilistic system to do a deterministic job — and it will miss an empty section *inconsistently*, which is worse than not checking at all.

So every gate runs its free deterministic check first, and spends model budget only on what needs judgment. This appears four times: at the researcher seam ([04](04-researchers.md#transport)), at the validator ([06](06-validator-and-critic.md#stage-1--the-validator)), in the receipt rule ([06](06-validator-and-critic.md#the-receipt-rule)), and now in the admission rule — the *presence* of a read-through is a deterministic validator check; only its *quality* is the critic's ([06](06-validator-and-critic.md#the-admission-rule)).

### 4. Self-maintenance, with a paper trail

The competitor set promotes and types entities, the thesis evolves, the queue re-cuts — all with **no human approval step**. What replaces approval is **visibility**: every self-edit is a git commit with a reason, appended to that file's own log. The one carve-out is the **steering wheel**: the interest list is human-owned. The system may *propose* refine/prune/add as findings, but the human confirms and owns every write ([03](03-state-and-governance.md#the-interest-list)).

### 5. Subscriptions, not APIs

Workers run on a Claude subscription via headless Claude Code (`claude -p`); the critic runs on a ChatGPT subscription via Codex CLI (`codex exec --json`). This constrains the architecture: it makes model calls a scarce resource to spend deliberately (hence rule 3), which is also why cost scales with apertures, not programs ([09](09-orchestrator.md#scaling-to-many-programs)). API billing is a fallback only, with cost caps.

### 6. Read-only is a hard wall

Researchers are constrained by Claude Code permission flags, not by prompt instructions. A researcher **cannot** write a file even if a prompt injection convinces it to try. This forces the transport design in [04](04-researchers.md#transport): findings come back on stdout, and `run.py` is the sole writer.

### 7. Cross-family adversarial review

The critic is a different model family from the workers by design. A Claude critic auditing Claude workers shares their blind spots. When the two families disagree irreconcilably, **both sides are published** — a genuine dispute is information the reader should have, not something either side settles silently.

## The run, end to end

```
  daily heartbeat (OS scheduler, 07:00 local, never rewritten)
        │
        ▼
  run.py — is any program due today?  ──no──►  exit (no issue, no stub, no trace)
        │ yes, for program P
        ▼
  read state: entities/, programs/P/, interests.toml, thesis.json, catalyst-queue.json
  verify conference calendar against primary sources; poll the registry watch
        │
        ▼
  1 biology + N arena + 1 house = apertures in parallel (read-only, sonnet)
        │                                   ──►  findings.json each, on stdout
        ▼                                        run.py validates + persists
  manager (opus) reads findings + state  ──►  issue.json draft (every item read-through'd)
        │
        ▼
  ┌──────────────────────┐  fail   ┌──────────────────────────┐
  │ validator            ├────────►│ back to manager (2 max)  │
  │ deterministic, free  │         │ exhausted → failed stub  │
  │ + admission check    │         └──────────────────────────┘
  └──────────┬───────────┘
             │ structurally valid
             ▼
  ┌──────────────────────┐ blocked ┌──────────────────────────┐
  │ codex critic         ├────────►│ fix or rebut (2 max)     │
  │ judgment only        │         │ exhausted → banner       │
  └──────────┬───────────┘         └──────────────────────────┘
             │ pass / pass_with_advisories
             ▼
  write issues/P/<date>.json + update index  ──►  git commit  ──►  dashboard renders
```

Two retry budgets, **two each, separate**. A trivial JSON slip must never starve the critic of the budget it needed for substance. Worst case is bounded at four manager calls.

## What runs where

| | |
|---|---|
| **Target machine** | A Windows PC, always on. Verified: Codex CLI runs natively on Windows (no WSL) with auto-refreshing subscription auth. |
| **Repo** | OS-agnostic. Anyone can clone and run; the only platform-specific surface is one heartbeat registration per OS. |
| **Publishing** | localhost v1, reachable from LAN devices via the PC's IP. Every issue is committed, so a GitHub Pages flip is a later ten-minute change (caveat: Pages is public web unless paid). |
| **Storage** | JSON files. One `issues/<program_id>/<date>.json` per cycle; a shared `state/entities/` fact layer plus per-program `state/programs/<id>/`. Shaped so a later SQLite swap is mechanical — un-parked only when cross-entity query becomes the hot path ([09](09-orchestrator.md#scaling-to-many-programs)). |
| **Models** | Researchers: `claude-sonnet-5`. Manager: `claude-opus-4-8`. Critic: Codex (`gpt-5-codex`). All configurable; these are defaults. |
| **Budget target** | Cost scales with distinct apertures, not program count: `FIXED + Σ arena scans`. A full run under roughly an hour at modest subscription usage. |

## Phase 2 — deliberately parked

Not fog, not out of scope: decided to be later.

- **Logged-in browser access** (Claude in Chrome, or Playwright MCP with a persistent profile) to reach paywalled primaries and language-gated China registries. Brittle, and opens a prompt-injection surface on an unattended run. v1 cites paywalled facts via free secondary coverage, flags them, and carries China-first assets as a named blind spot ([04](04-researchers.md#the-registry-watch-and-the-feed-set)).
- **The interest editor surface** — a separate local runtime tool; build-time architecture deferred to execution ([03](03-state-and-governance.md#the-interest-list)).
- **Human steering** of the thesis via prompt-nudging (the interest list is already the v1 steering wheel).
- **SQLite migration** — the schema is already shaped for it; un-parks on a named trigger ([09](09-orchestrator.md#scaling-to-many-programs)).
- **Patents as a scan feed** — record not signal, ~18-month latency; manual low-cadence enrichment only ([04](04-researchers.md#the-registry-watch-and-the-feed-set)).
- **Multi-program packaging, GitHub Pages, failure push-notifications.**

## Out of scope

Beyond this effort entirely: multi-user or team features, auth, hosting for others, non-oncology and non-Hummingbird instances, and the system *autonomously setting* its own interests. The format generalizes, but this build is the oncology one.

## The pivot that produced this

This spec re-roots the v1 build spec (map [#1](https://github.com/cmengu/Research-Swarm/issues/1)) onto the surviving pipeline, per the [per-program detective map (#49)](https://github.com/cmengu/Research-Swarm/issues/49). What the pivot **did not touch** is inherited from map #1 and survives here: subscriptions not API; read-only researchers with `run.py` the sole writer; the authorship rule; the two gates; the degradation register and its three-part admission test; fail-visible over fail-silent; `entity_id` as the spine; the static single-file dashboard.

---

*Provenance: the pivot map [#49](https://github.com/cmengu/Research-Swarm/issues/49) and its resolved children (#50–#61), re-rooting the pre-map capture ([`CAPTURE.md`](../../CAPTURE.md)) and v1 spec ([#24](https://github.com/cmengu/Research-Swarm/issues/24)). Windows/Codex verification: [#2](https://github.com/cmengu/Research-Swarm/issues/2).*
