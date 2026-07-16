# Conference surge mode + calendar config (v0.1.0)

Decision asset for ticket [#18](https://github.com/cmengu/Research-Swarm/issues/18). Assets: [`config/cadence.toml`](../config/cadence.toml), [`config/calendar.toml`](../config/calendar.toml).

## The finding this answers

Agenda-setting emissions in oncology are **episodic and calendared**. Both 2026 repricing events in the [bellwether research](research/oncology-bellwethers-2026.md) — daraxonrasib (OS 13.2 vs 6.7 months, HR 0.40) and HARMONi-6 (HR 0.66) — landed at a **single ASCO plenary**. A flat Mon+Thu cadence reads the biggest 72 hours of the year at exactly the same rate as a dead August week, then faces a firehose in one window.

User adopted: keep the Mon+Thu baseline, add a conference surge.

## 1. The scheduler is a dumb daily heartbeat

**The OS scheduler fires `run.py` at 07:00 local, every day, forever, and is never rewritten.** `run.py` reads `config/cadence.toml`, asks "is today a run day?", and exits in milliseconds if not.

You cannot surge from a cron entry. The alternative — having `schedule-install` rewrite Task Scheduler / cron / launchd entries when a window opens — means per-OS scheduler code in three flavours, probably elevated privileges on Windows, and a silent failure mode on the one machine that is hardest to debug remotely: the rewrite fails, and you learn about it by missing a plenary.

Self-gating buys four things:

- **Mon+Thu becomes a config fact, not a cron fact** — versioned, git-visible, reviewable in a diff.
- **OS-agnostic by construction** — one heartbeat registration per OS is the entire platform-specific surface, which is what CAPTURE #10 wanted.
- **Testable** — cadence is exercised by faking the date, not by waiting a week.
- **Cadence stays in the declarative-config layer**, which is the system's only intended control surface.

Qualifies **CAPTURE #12** (the scheduler no longer owns the recipe's timing) and **CAPTURE #17** (Mon+Thu 07:00 moves from cron into `cadence.toml`).

A skipped day is a **no-op, not a run**: no issue, no stub, no dashboard entry. Only a stage failure inside an actual run produces a failed stub (CAPTURE #16).

## 2. The calendar self-maintains from primary sources

Conference dates move every year, so a hand-maintained calendar rots on an annual task nobody remembers. Societies publish their own dates, which makes these **primary-source facts, not judgment** — exactly what the loop is already trusted to fetch.

So: **seeded once by a human, re-verified by the loop each cycle** against each window's `source`, written as a visible git diff with the source cited. Per CAPTURE #6 — self-maintaining, no approval step, drift auditable. The loop must never write a date it did not read from `source`.

### Staleness is a declared degradation

A stale calendar is **the only failure in this system that would otherwise be silent.** Every other missing piece announces itself — an unseeded thesis prints `No thesis seeded — facts only`, a dead critic publishes `published_uncritiqued` under a banner, an unaccounted watchlist entity blocks validation. A rotted calendar just… doesn't surge. A perfectly normal Monday digest ships while ASCO reprices two companies, and nothing says a word.

That breaks the house rule from #5 — *a missing piece bends the output and marks the absence.* So staleness is a **declared degradation**, registered in [the register](critic-rubric.md#the-register) in the critic rubric (v0.3.0, #23) — the rubric is the registry, and this document only explains the entry, it does not declare it. The behaviour the register binds:

| Condition | Behaviour |
|---|---|
| `valid_through` has passed | Every issue carries `conference calendar stale — surge disabled`; critic files `calendar_stale` (advisory). |
| A window's dates are unverified | That window surges nothing; the marker names it. |
| No window verified in N cycles | Same marker; the loop's verification step is itself failing. |

### The calendar fails toward surging

The cost is **asymmetric**: surging on a wrong week costs a few runs and some subscription quota; missing the ASCO plenary costs the year's biggest story — the thing the feature exists for. So where the two conflict, bias to surge.

The one exception is `require_verified_dates` (cadence.toml): an **unverified** window surges nothing. Failing toward surging means tolerating a wasted run, not firing on a hallucinated date — a guessed date is as likely to surge on the wrong week as the right one, and it would surge while *claiming* verification. An honest gap beats a confident guess.

**Seeded state:** all six windows (`jpm`, `aacr`, `asco`, `wclc`, `esmo`, `ash`) ship with `starts`/`ends` empty and `verified_at` empty. Exact dates were deliberately **not invented**; `typical_window` carries the reliable month-level pattern and tells the verifier when to look. Until a run resolves them against `source`, no window surges and the stale marker explains why.

## 3. Surge is a per-window config knob

A surge window sets **`cadence = "daily"`**. Same six beats, same manager, same critic, same rubric.

Daily runs narrow each cycle's `coverage_window` to ~1 day, which **bounds volume per run naturally** — the firehose gets sliced into daily servings instead of arriving as one 72-hour flood, which is when the manager cuts hardest and `dropped_story` findings spike.

v1 deliberately exposes **one** knob. A surge-only plenary/late-breaker beat and a deeper backstop were both considered and deferred: they cost a new prompt in #6 and roughly triple subscription burn across a window, against CAPTURE #17's "under ~1 hour, modest usage" target — and a rate limit on ASCO Monday is the worst possible morning to discover one. Adding a knob later is a line in `cadence.toml`, not a code change.

## 4. The critic's bar does not move — with one fix

The rubric is **identical** during surge: same blocking kinds, same principle, same retry budgets. A bar that relaxes when stakes peak is backwards — the most-read issue of the year would be the least-vetted, and CAPTURE #2's "every claim cited, investor/BD-grade" has no busy-week exemption. It also needs no relaxing: conference-day reporting is `tier: trade` (Endpoints, Fierce live coverage), and the rubric only blocks on **aggregator-only** support, so live trade coverage of a presented-but-unpublished readout already clears the bar with no primary PR in hand.

### Rubric amendment: `provenance_stale` reference window

**Narrowing `coverage_window` to a day breaks `provenance_stale`.** That check fires when a claim presented as new rests on a source published outside the coverage window. During ASCO, a Tuesday run has a Tuesday-only window — so Sunday's plenary readout, still the most important story on the floor and entirely legitimate to carry, would trip `provenance_stale` as **blocking**, on the busiest morning of the year, with two retries to burn.

> **Amendment to [`docs/critic-rubric.md`](critic-rubric.md#blocking-findings):** while a surge window is open, `provenance_stale` compares `published_at` against the **conference window**, not the run's `coverage_window`.

Without this, surge would manufacture false halts precisely on the mornings it exists to cover.

## 5. Surge issues are marked in the UI

The dashboard dropdown is a flat list of dated issues. During ASCO week, five issues appear where a reader expects two, each with a one-day coverage window, and nothing explains why — it reads as a malfunction.

So `run` gains a small block:

```jsonc
run: {
  run_id, status, critic_verdict, critic_retries, models,
  surge: {                  // absent on a baseline run
    window: "ASCO 2026",    // window `name` from calendar.toml
    day: 2,                 // 1-indexed day within the window
    of: 5
  }
}
```

Dashboard (#8): a badge reading **`ASCO 2026 · day 2 of 5`** on the issue, and the dropdown groups the window's issues under the conference name rather than scattering them. It also makes the narrow coverage window self-explanatory, and it is queryable later — "show me every ASCO-week issue".

## Deltas this creates

**Schema (rides with v0.1.1 / spec compilation, #3 is closed):**

- `run.surge = {window, day, of}` — absent on baseline runs.
- `critic_report.advisory_findings[].kind` gains `calendar_stale`.

**Critic rubric (#7):** the `provenance_stale` surge amendment above still rides with spec compilation. ~~`calendar_stale` added to the advisory table; stale calendar added to declared degradations~~ — **landed**: rubric v0.3.0 (#23) lifted both into [the register](critic-rubric.md#the-register).

**Dashboard (#8):** surge badge; dropdown grouping. `issues/index.json` (already in the map's fog) should carry `surge.window` so the dropdown can group without opening every issue.

**Orchestrator:** `run.py` self-gates on `cadence.toml`; a calendar-verification step writes `calendar.toml` diffs from primary sources each cycle.

## Carried to the spec

- `N` for "no window verified in N cycles" — needs a real cadence to calibrate against; a guess now is a number nobody trusts later.
- Whether a surge window should also widen the *baseline* coverage window of the first post-window run, so a Friday run after ASCO doesn't re-report the whole week.
- Whether `jpm` deserves a different knob set — it's a deal-announcement venue, not a data venue, so the failure mode there is a rumour storm rather than a readout firehose.
