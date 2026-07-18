# ResearchSwarm — build spec

**This is an index. Every rule lives in a stage document; this page only says where.**

ResearchSwarm is an unattended **per-program competitive detective** for **oncology**, written for the decision-owner of a specific drug program. On a monthly per-program cadence — plus an automatic conference surge and a manual push — it aims a small set of read-only research **apertures** at one program's competitive board, has a manager synthesize one dated **program issue** (with a subordinate house view), puts that issue through a deterministic validator and an adversarial cross-family critic, and publishes it to a static dashboard. It maintains a shared competitor-fact layer, per-program relation edges, a human-set interest list, its own worldview, and a queue of expected catalysts — and every self-edit is a git diff.

The pilot program is **HMBD-001** — Hummingbird Bioscience's anti-HER3 IgG1 signalling antibody. Adding a second program is one config file.

Nothing here is built beyond the v1 pipeline that this spec re-roots. This spec is the complete decision context — the output of a thirteen-ticket pivot map — written so a builder can start without reading a single issue thread.

## The re-rooting in one line

The old system published because something *happened*; this one publishes because something happened *to a specific drug program*. The pipeline survives; the product it emits changed.

## Reading order

| # | Document | What it settles |
|---|---|---|
| 1 | [Overview and principles](docs/spec/01-overview.md) | What the system is, who reads it, the house rules every other document obeys |
| 2 | [Cadence, scheduling and surge](docs/spec/02-cadence-and-surge.md) | The three cadence triggers — monthly per-program, conference surge, manual push; the self-verifying calendar |
| 3 | [State, config and governance](docs/spec/03-state-and-governance.md) | The programs, the shared competitor layer, the interest list, the thesis, the queue, and who may write them |
| 4 | [Researchers](docs/spec/04-researchers.md) | The apertures that replaced the beats, the shared prompt, registry-diff, and the `findings.json` contract |
| 5 | [The manager](docs/spec/05-manager.md) | Synthesis, the authorship rule, the read-through, and what the manager alone may author |
| 6 | [Validator and critic](docs/spec/06-validator-and-critic.md) | The two gates, the admission rule, the degradation register, retries, and the rebuttal channel |
| 7 | [issue.json schema v2.0.0](docs/spec/07-issue-schema.md) | The complete field-level contract for a published program issue |
| 8 | [Publishing and dashboard](docs/spec/08-publishing-and-dashboard.md) | The static detective dashboard, the issue manifest, and the reader-facing markers |
| 9 | [Orchestrator and repo layout](docs/spec/09-orchestrator.md) | `run.py`, the stage machine, config, retention, and what to build first |

Read 1 first. After that, 2–9 stand alone; each names its own inputs and outputs.

## The shortest possible summary

A daily heartbeat wakes `run.py`, which checks each program's cadence in `config/programs/<id>.toml` and exits in milliseconds unless a program is due today. For a due program it reads the shared state, renders one shared prompt across that program's **apertures** — one indication-blind **biology scan** (target + MOA, carrying mechanism and target twins), one **arena scan per indication** (setting rivals + standard of care), and one cheap **house sweep** — and calls `1 + N + 1` read-only researchers in parallel. Each returns a `findings.json` of **facts only** on stdout; `run.py` is the sole writer that persists them. The manager reads them plus the state files and authors one `issue.json` — it is the only component that interprets, and every published competitor carries a **read-through**: the typed relation that answers "why is this a competitor" plus the prose of what it means for the program. A deterministic validator checks structure for free — including that every item carries a read-through or is an admitted blind spot; if it passes, the Codex critic judges what the pipeline found and then lost. Blocking findings loop back to the manager (twice, with one rebuttal allowed); advisories publish visibly. The issue is written to `issues/<program_id>/`, committed, and rendered by a static dashboard.

The system's spine is the **`entity_id`**, its conscience is the **degradation register**, its answer to "why is this a competitor" is the **typed relation**, and its one forward-looking claim is the **catalyst queue**.

## Cost scales with apertures, not programs

A second squamous-NSCLC program reuses the arena scan and treatment landscape it shares — it adds only its own biology scan and its own per-program read-throughs. The house sweep is fixed O(1). Cost is `FIXED + Σ distinct apertures`, not `× program count` ([09](docs/spec/09-orchestrator.md#scaling-to-many-programs)).

## Provenance

The decisions here were made across the [per-program detective map (#49)](https://github.com/cmengu/Research-Swarm/issues/49) and its resolved children (#50–#61) between 17 and 18 July 2026, re-rooting the v1 build spec (map [#1](https://github.com/cmengu/Research-Swarm/issues/1), compiled by [#24](https://github.com/cmengu/Research-Swarm/issues/24)/[#26](https://github.com/cmengu/Research-Swarm/issues/26)) onto the surviving pipeline. Ticket links appear in these documents **only** as provenance footnotes — every rule is stated here in full. If a document ever says "see #N" for a rule you need, that is a bug in the spec, not an instruction to go read the ticket.

Two capture documents are historical: the pre-map voice capture [`CAPTURE.md`](CAPTURE.md) and the pivot grilling ([PR #48](https://github.com/cmengu/Research-Swarm/pull/48)). The spec wins wherever they disagree; they are kept for their fuller reasoning, not deleted.

## Marked provisional

Defaults adopted rather than owner-chosen are flagged **⚑ provisional** where they appear, each naming what would change it:

- Raw-findings retention: **24 runs** ([09](docs/spec/09-orchestrator.md#retention))
- Continuity lookback floor: **12 issues** ([06](docs/spec/06-validator-and-critic.md#the-lookback-floor))
- Stale-calendar counter `N`: **8 cycles** ([02](docs/spec/02-cadence-and-surge.md#staleness))
- Cold-start lookback: **7 days** (`config/programs/<id>.toml`) — how far back a program's run #1 reaches when there is no previous issue to join to. Applies once per program.
- Per-program baseline cadence: **monthly** ([02](docs/spec/02-cadence-and-surge.md#baseline-cadence-the-per-program-dial))
- Interest-list rot horizon: **6 months** ([03](docs/spec/03-state-and-governance.md#the-interest-list))
- House blind-spot cap: **N = 5** ([06](docs/spec/06-validator-and-critic.md#the-admission-rule))
- Four of six thesis stances are `agent_draft_delegated` ([03](docs/spec/03-state-and-governance.md#stance-provenance))

None blocks a build. All are one-line config or content edits.

## Compilation gaps — deferred by decision, not ruled here

Named open by the map and left open by this spec, never quietly resolved:

- **The thesis under a program roof.** Whether a program carries its own angle, whether the house thesis survives as-is, and how the propagation contract behaves with two layers — parked by [#49](https://github.com/cmengu/Research-Swarm/issues/49). v1 does the minimum that forecloses neither: `read_through.thesis_bearing` feeds the existing drift engine, and `thesis_updates` renders as before ([05](docs/spec/05-manager.md#thesis-gating-under-a-program-roof)).
- **The interest editor surface.** A separate local runtime tool (not part of the static digest); its build-time architecture is deferred to execution ([03](docs/spec/03-state-and-governance.md#the-interest-list)).
- **Migration of the seeded 22-entity watchlist** into the per-program competitor model ([03](docs/spec/03-state-and-governance.md#migrating-the-seeded-roster)).
- **Multi-program packaging** — one digest spanning N programs — deferred to [#59](https://github.com/cmengu/Research-Swarm/issues/59); v1 publishes one issue per program.
