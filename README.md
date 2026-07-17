# ResearchSwarm 🐝

A self-improving **per-program competitive detective** for oncology.

On a monthly per-program cadence — plus an automatic conference surge and a manual push — unattended: a manager aims a small set of **read-only research apertures** at one drug program's competitive board (one biology scan across its target and mechanism, one arena scan per indication, one cheap house sweep). It synthesizes their findings into a dated **program issue** in which every competitor carries a **read-through** — the typed relation that says *why* it is a competitor, and what it means for the program. A **cross-family adversarial critic** (Codex judging Claude's work) hunts gaps and bad provenance before anything publishes to a static web dashboard.

The pilot program is **HMBD-001**, Hummingbird Bioscience's anti-HER3 signalling antibody. Adding another program is one config file.

**Status:** planning. The full build spec is [`SPEC.md`](SPEC.md) (a nine-document index); the pre-map capture is [`CAPTURE.md`](CAPTURE.md) (historical). The Wayfinder map and tickets live on this repo's issue tracker.

## Design pillars

- **The program is the noun** — the reader is one drug's decision-owner; the digest is a detective on its competitors, not a market survey.
- **Typed competitors** — every competitor is a mechanism twin, target twin, setting rival, benchmark/SOC, or platform threat. The relation is the answer to "why is this a competitor," and it renders on the page.
- **The read-through or it doesn't publish** — every item states what it means for the program, or it is admitted as a capped, ranked blind spot, or dropped with a receipt. Nothing is silently omitted.
- **Self-maintaining, human-steered** — the competitor set evolves autonomously with drift visible in every issue; a human-set interest list is the one steering wheel, and the system proposes but never writes it.
- **Subscriptions, not API** — headless Claude Code (Claude sub) + Codex CLI (ChatGPT sub).
- **Fail visible** — critic rejections, failed runs, dead apertures, and known blind spots are published, never silently dropped.
