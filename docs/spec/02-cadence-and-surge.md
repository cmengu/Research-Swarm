# 2. Cadence, scheduling and surge

When a run happens. Covers the daily heartbeat, the baseline Mon+Thu cadence, conference surge mode, and the self-verifying conference calendar.

**Inputs:** `config/cadence.toml`, `config/calendar.toml`.
**Consumers:** `run.py` ([09](09-orchestrator.md)), the researcher prompt's surge block ([04](04-researchers.md)), the critic's `provenance_stale` check ([06](06-validator-and-critic.md)), the dashboard's surge badge ([08](08-publishing-and-dashboard.md)).

## The scheduler is a dumb daily heartbeat

**The OS scheduler fires `run.py` at 07:00 local, every day, forever, and is never rewritten.** `run.py` reads `config/cadence.toml`, asks "is today a run day?", and exits in milliseconds if not.

You cannot surge from a cron entry. The alternative — having an installer rewrite Task Scheduler, cron and launchd entries whenever a conference window opens — means per-OS scheduler code in three flavours, probably elevated privileges on Windows, and a silent failure mode on the one machine that is hardest to debug remotely: the rewrite fails, and you find out by missing a plenary.

Self-gating buys four things:

- **Cadence is a config fact, not a cron fact** — versioned, git-visible, reviewable in a diff.
- **OS-agnostic by construction** — one heartbeat registration per OS is the entire platform-specific surface.
- **Testable** — cadence is exercised by faking the date, not by waiting a week.
- **Cadence stays in the declarative-config layer**, which is the system's only intended control surface.

**A skipped day is a no-op, not a run:** no issue, no stub, no dashboard entry, no trace. Only a stage failure inside an actual run produces a failed stub.

## Baseline cadence

Mon + Thu, 07:00 local. Lives in `config/cadence.toml`:

```toml
[baseline]
days = ["mon", "thu"]
hour = 7
```

Flippable at will — it is a default, not an invariant. The coverage window of each run runs from the previous issue's date to today, so changing cadence changes window width automatically rather than leaving gaps.

## Surge mode

### Why surge exists

Agenda-setting emissions in oncology are **episodic and calendared**. Both 2026 repricing events in the underlying research — daraxonrasib (OS 13.2 vs 6.7 months, HR 0.40) and HARMONi-6 (HR 0.66) — landed at a **single ASCO plenary**. A flat Mon+Thu cadence reads the biggest 72 hours of the year at exactly the same rate as a dead August week, and then faces a firehose in one window.

So: keep the Mon+Thu baseline, and add a conference surge.

### Surge is one knob

A surge window sets **`cadence = "daily"`**. Same six beats, same manager, same critic, same rubric, same model tiers.

```toml
[surge]
enabled = true
cadence = "daily"

[surge.guard]
require_verified_dates = true
max_surge_days = 7          # a window claiming longer is a data error
```

Daily runs narrow each cycle's coverage window to about a day, which **bounds volume per run naturally** — the firehose gets sliced into daily servings instead of arriving as one 72-hour flood, which is exactly when the manager cuts hardest and dropped-story findings spike.

v1 deliberately exposes **one** knob. A surge-only plenary/late-breaker beat and a deeper backstop were both considered and **deferred**: they cost a new prompt and roughly triple subscription burn across a window, against the under-an-hour, modest-usage target — and a rate limit on ASCO Monday is the worst possible morning to discover one. Adding a knob later is a line in `cadence.toml`, not a code change.

### The critic's bar does not move — with one fix

The rubric is **identical** during surge: same blocking kinds, same principle, same retry budgets. A bar that relaxes when stakes peak is backwards.

It also needs no relaxing. Conference-day reporting is `tier: trade` (Endpoints, Fierce live coverage), and the rubric only blocks on **aggregator-only** support — so live trade coverage of a presented-but-unpublished readout already clears the bar with no primary press release in hand.

The one required fix is a reference-window change, not a bar change:

> **While a surge window is open, `provenance_stale` compares `published_at` against the conference window, not the run's `coverage_window`.**

Narrowing the coverage window to a day would otherwise break that check. During ASCO, a Tuesday run has a Tuesday-only window — so Sunday's plenary readout, still the most important story on the floor and entirely legitimate to carry, would trip `provenance_stale` as **blocking**, on the busiest morning of the year, with two retries to burn. Without this carve-out, surge would manufacture false halts precisely on the mornings it exists to cover.

The same carve-out is handed to researchers in their prompt, so they don't self-censor in-window stories. See [04 — Researchers](04-researchers.md#what-a-researcher-is-told).

### Surge is marked in the issue

The dashboard dropdown is a flat list of dated issues. During ASCO week, five issues appear where a reader expects two, each with a one-day coverage window, and nothing explains why — it reads as a malfunction. So `run` carries:

```jsonc
"surge": {              // absent entirely on a baseline run
  "window": "ASCO 2026",  // the window's `name` from calendar.toml
  "day": 2,               // 1-indexed day within the window
  "of": 5
}
```

Absent, not null, on baseline runs. The dashboard renders a badge (`ASCO 2026 · day 2 of 5`) and groups the window's issues under the conference name; the issue manifest carries `surge.window` so grouping doesn't require opening every issue ([08](08-publishing-and-dashboard.md#the-issue-manifest)).

## The conference calendar

### It self-maintains from primary sources

Conference dates move every year, so a hand-maintained calendar rots on an annual task nobody remembers. Societies publish their own dates, which makes these **primary-source facts, not judgment** — exactly what the loop is already trusted to fetch.

So: **seeded once by a human, re-verified by the loop each cycle** against each window's `source`, written as a visible git diff with the source cited. No approval step; drift is auditable.

> **The loop must never write a date it did not read from `source`.**

### It fails toward surging

The cost is **asymmetric**: surging on a wrong week costs a few runs and some subscription quota; missing the ASCO plenary costs the year's biggest story — the thing the feature exists for. Where the two conflict, bias to surge.

The one exception is `require_verified_dates`: an **unverified** window surges nothing. Failing toward surging means tolerating a wasted run, not firing on a hallucinated date. A guessed date is as likely to surge on the wrong week as the right one, and it would surge while *claiming* verification. An honest gap beats a confident guess.

### Staleness

A stale calendar is **the only failure in this system that would otherwise be silent.** Every other missing piece announces itself: an unseeded thesis prints its marker, a dead critic publishes under a banner, an unaccounted watchlist entity blocks validation. A rotted calendar just… doesn't surge. A perfectly normal Monday digest ships while ASCO reprices two companies, and nothing says a word.

That breaks house rule 1 ([01](01-overview.md#1-a-missing-piece-bends-the-output-it-never-kills-the-run)). So staleness is a **declared degradation**, registered as `calendar_stale` in [the degradation register](06-validator-and-critic.md#the-degradation-register) — the register is its single home; this document explains the entry but does not declare it.

| Condition | Behaviour |
|---|---|
| `valid_through` has passed | Every issue carries `conference calendar stale — surge disabled`; critic files `calendar_stale` (advisory). |
| A window's dates are unverified | That window surges nothing; the marker names it. |
| No window verified in **N = 8** cycles ⚑ | Same marker; the loop's own verification step is failing. |

**⚑ N = 8 is provisional.** About four weeks at baseline cadence: long enough that a fortnight of quiet society pages doesn't false-alarm, short enough to catch a rotted verification step before the next major window opens. Counted per run, independent of surge cadence. Recalibrate once real cadence data exists — this is the number most likely to be wrong, and it is one line in `cadence.toml`.

### Seeded state

All six windows — `jpm`, `aacr`, `asco`, `wclc`, `esmo`, `ash` — ship with `starts`, `ends` and `verified_at` **empty**. Exact dates were deliberately **not invented**; `typical_window` carries the reliable month-level pattern and tells the verifier when to look. Until a run resolves them against `source`, no window surges and the stale marker explains why.

This is not an oversight to fix before launch. It is the fails-toward-honesty rule applied to the seed itself: the system's first act is to go read the dates from the societies, and the marker explains the gap until it does.

## Open, deferred by decision

Neither blocks a build; both want real cadence data first.

- Whether a surge window should also widen the **baseline** coverage window of the first post-window run, so a Friday run after ASCO doesn't re-report the whole week.
- Whether `jpm` deserves a different knob set — it's a deal-announcement venue, not a data venue, so its failure mode is a rumour storm rather than a readout firehose.

---

*Provenance: ticket [#18](https://github.com/cmengu/Research-Swarm/issues/18); qualifies capture decisions #12 and #17. The `N` value is a ⚑ default adopted under [#24](https://github.com/cmengu/Research-Swarm/issues/24#issuecomment-4991566381).*
