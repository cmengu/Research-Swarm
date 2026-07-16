# ResearchSwarm — build spec

**This is an index. Every rule lives in a stage document; this page only says where.**

ResearchSwarm is an unattended competitive-intelligence pipeline for **oncology-first biotech and pharma M&A**, written for an investor/BD-grade reader. Twice a week (and daily during oncology conference windows) it fans six read-only research agents across the news, has a manager synthesize one dated digest, puts that digest through a deterministic validator and an adversarial cross-family critic, and publishes it to a static dashboard. It maintains its own watchlist, its own worldview, and its own queue of expected catalysts, and every self-edit is a git diff.

Nothing here is built yet. This spec is the complete decision context — the output of a thirteen-ticket planning map — written so a builder can start without reading a single issue thread.

## Reading order

| # | Document | What it settles |
|---|---|---|
| 1 | [Overview and principles](docs/spec/01-overview.md) | What the system is, who reads it, the house rules every other document obeys |
| 2 | [Cadence, scheduling and surge](docs/spec/02-cadence-and-surge.md) | When a run happens; the daily heartbeat; conference surge; the self-verifying calendar |
| 3 | [State files and governance](docs/spec/03-state-and-governance.md) | The three self-maintained files, who may write them, and the `entity_id` spine |
| 4 | [Researchers](docs/spec/04-researchers.md) | The six beats, the shared prompt, and the `findings.json` contract |
| 5 | [The manager](docs/spec/05-manager.md) | Synthesis, the authorship rule, and what the manager alone may author |
| 6 | [Validator and critic](docs/spec/06-validator-and-critic.md) | The two gates, the degradation register, retries, and the rebuttal channel |
| 7 | [issue.json schema v1.0.0](docs/spec/07-issue-schema.md) | The complete field-level contract for a published issue |
| 8 | [Publishing and dashboard](docs/spec/08-publishing-and-dashboard.md) | The static dashboard, the issue manifest, and the reader-facing markers |
| 9 | [Orchestrator and repo layout](docs/spec/09-orchestrator.md) | `run.py`, the stage machine, config, retention, and what to build first |

Read 1 first. After that, 2–9 stand alone; each names its own inputs and outputs.

## The shortest possible summary

A daily heartbeat wakes `run.py`, which checks `config/cadence.toml` and exits in milliseconds unless today is a run day. On a run day it reads the three state files, renders one shared prompt against six beat definitions, and calls six read-only researchers in parallel. Each returns a `findings.json` of **facts only** on stdout; `run.py` is the sole writer that persists them. The manager reads all six plus the state files and authors one `issue.json` — it is the only component that interprets. A deterministic validator checks structure for free; if it passes, the Codex critic judges what the pipeline found and then lost. Blocking findings loop back to the manager (twice, with one rebuttal allowed); advisories publish visibly. The issue is written to `issues/`, committed, and rendered by a static dashboard.

The system's spine is the **`entity_id`**, its conscience is the **degradation register**, and its one forward-looking claim is the **catalyst queue**.

## Provenance

The decisions here were made across thirteen tickets on the [ResearchSwarm wayfinder map](https://github.com/cmengu/Research-Swarm/issues/1) between 16 June and 16 July 2026, and compiled by [#26](https://github.com/cmengu/Research-Swarm/issues/26) under the rulings in [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381). Ticket links appear in these documents **only** as provenance footnotes — every rule is stated here in full. If a document ever says "see #N" for a rule you need, that is a bug in the spec, not an instruction to go read the ticket.

The pre-map voice capture that started it all is preserved at [`CAPTURE.md`](CAPTURE.md). It is a historical record, superseded by this spec wherever the two disagree.

## Marked provisional

Three numbers and one set of stances were adopted as defaults rather than chosen by the owner. They are flagged **⚑ provisional** where they appear, and each names what would change it:

- Raw-findings retention: **24 runs** ([09](docs/spec/09-orchestrator.md#retention))
- Continuity lookback floor: **12 issues** ([06](docs/spec/06-validator-and-critic.md#the-lookback-floor))
- Stale-calendar counter `N`: **8 cycles** ([02](docs/spec/02-cadence-and-surge.md#staleness))
- Four of six thesis stances are `agent_draft_delegated` ([03](docs/spec/03-state-and-governance.md#stance-provenance))

None blocks a build. All are one-line config or content edits.
