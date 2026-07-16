# ResearchSwarm 🐝

A self-improving competitive-intelligence engine for biotech.

Twice a week, unattended: a manager fans out parallel **read-only research agents** (one per beat — pharma M&A, oncology startup frontier, clinical & scientific developments, policy & regulation, incumbents & new entrants — plus a catch-all backstop). A manager synthesizes their findings into a dated **issue**; a **cross-family adversarial critic** (Codex judging Claude's work) hunts gaps and bad provenance before anything publishes to a local web dashboard.

**Status:** planning. All locked decisions and remaining fog live in [CAPTURE.md](CAPTURE.md); the Wayfinder map and tickets live on this repo's issue tracker.

## Design pillars

- **Investor/BD-grade output** — every claim cited, M&A theses argued.
- **Self-maintaining** — the watchlist and internal thesis evolve autonomously, with drift visible in every issue.
- **Subscriptions, not API** — headless Claude Code (Claude sub) + Codex CLI (ChatGPT sub).
- **OS-agnostic repo** — anyone clones and runs; only the scheduler registration is per-OS.
- **Fail visible** — critic rejections and failed runs are published, never silently dropped.
