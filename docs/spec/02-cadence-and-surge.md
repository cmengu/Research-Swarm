# 2. Cadence, scheduling and surge

When a run happens. The pivot replaced the single global Mon+Thu cadence with **three triggers** — a monthly per-program dial, an automatic conference surge, and a manual push — but the dumb-heartbeat scheduler, the surge mechanism and the self-verifying calendar survived.

**Inputs:** `config/programs/<id>.toml` (per-program cadence), `config/calendar.toml`.
**Consumers:** `run.py` ([09](09-orchestrator.md)), the researcher prompt's surge block ([04](04-researchers.md)), the critic's `provenance_stale` check ([06](06-validator-and-critic.md)), the dashboard's surge badge ([08](08-publishing-and-dashboard.md)).

## The scheduler is a dumb daily heartbeat (unchanged)

**The OS scheduler fires `run.py` at 07:00 local, every day, forever, and is never rewritten.** `run.py` reads each program's cadence, asks "is any program due today?", and exits in milliseconds if not.

You cannot surge from a cron entry. The alternative — an installer rewriting Task Scheduler, cron and launchd entries whenever a window opens — means per-OS scheduler code in three flavours, elevated privileges on Windows, and a silent failure mode on the machine hardest to debug remotely. Self-gating keeps cadence a **config fact, not a cron fact** — versioned, git-visible, OS-agnostic, and testable by faking the date.

**A skipped day is a no-op, not a run:** no issue, no stub, no dashboard entry, no trace. Only a stage failure inside an actual run produces a failed stub.

## The three triggers

The knob turns cadence; it never turns scan depth (the house sweep runs cheaply every run) and the interest list never turns cadence — the two are orthogonal ([#55](https://github.com/cmengu/Research-Swarm/issues/55)).

| Trigger | Owner | What it does |
|---|---|---|
| **Baseline** | per-program dial | Each program runs on its own cadence (default monthly). SOC and competitive posture move in months, not days. |
| **Conference surge** | the calendar | Any program with a competitor in an in-window conference goes daily for that window — automatic. |
| **Manual push** | the human | A one-off run for a named program, out of cadence, when something breaks between scheduled runs. |

### Baseline cadence: the per-program dial

Cadence lives **per program**, in `config/programs/<id>.toml`:

```toml
[cadence]
baseline = "monthly"        # ⚑ default; per-program, flippable
```

**⚑ Monthly is the default, not an invariant.** A per-program detective watches a board that moves in months — a new SOC, a phase transition, a deal. Reading it Mon+Thu would mostly re-report an unchanged board; the registry watch ([04](04-researchers.md#the-registry-watch-and-the-feed-set)) already catches the between-cycle deltas that used to justify a twice-weekly beat. The coverage window of each run runs from the program's previous issue to today, so changing a program's dial changes its window width automatically rather than leaving gaps. Recalibrate per program once real cadence data exists.

### Manual push

A third trigger the map added ([#49](https://github.com/cmengu/Research-Swarm/issues/49)): the human can fire a single out-of-cadence run for a named program (`run.py --program hmbd-001 --push`). It produces a normal, dated program issue — same apertures, same gates, same rubric — and the next scheduled run's window joins to it like any other issue. It exists because a monthly dial is too slow to react to a break the human already knows about (a competitor's surprise readout the day after a scheduled run).

## Surge mode

### Why surge exists

Agenda-setting emissions in oncology are **episodic and calendared**. Both 2026 repricing events relevant to the pilot landed at conferences — ivonescimab's HARMONi-6 (HR 0.66) at the **ASCO 2026 plenary**; HER3-DXd cohorts surface first as **embargoed abstract titles**. A flat cadence reads the biggest 72 hours of the year at the same rate as a dead August week. So: keep the per-program baseline, and add a conference surge.

### Surge is one knob

A surge window sets **`cadence = "daily"`** for any program with a competitor in that window. Same apertures, same manager, same critic, same rubric, same model tiers.

```toml
[surge]
enabled = true
cadence = "daily"

[surge.guard]
require_verified_dates = true
max_surge_days = 7          # a window claiming longer is a data error
```

Daily runs narrow each cycle's coverage window to about a day, which **bounds volume per run naturally**. A surge-only aperture and a deeper sweep were both considered and **deferred**: they cost a new prompt and roughly triple subscription burn across a window, against the under-an-hour target — and a rate limit on ASCO Monday is the worst possible morning to discover one.

### The embargo calendar drives the pre-arm

The abstract embargo schedule is the surge's timing spine ([source set #51](https://github.com/cmengu/Research-Swarm/issues/51)): AACR mid-March, **ASCO title-drop 21 Apr / text 21 May / LBAs late May–June**, ESMO title-drop mid-July / congress in autumn. A competitor's pivotal readout is far more likely to *first* appear as an embargoed abstract than as a press release, so a program pre-arms around these dates. A title-drop is a low-confidence signal (titles only, text embargoed) — the detective surfaces it as `watching`, not as an established readout.

### The critic's bar does not move — with one fix

The rubric is **identical** during surge. The one required fix is a reference-window change:

> **While a surge window is open, `provenance_stale` compares `published_at` against the conference window, not the run's `coverage_window`.**

Narrowing the coverage window to a day would otherwise trip a legitimate Sunday-plenary story as `provenance_stale` on a Tuesday run. The same carve-out is handed to researchers ([04](04-researchers.md#sourcing-rules--non-negotiable)) so they don't self-censor in-window stories.

### Surge is marked in the issue

```jsonc
"surge": {              // absent entirely on a baseline run
  "window": "ASCO 2026",  // the window's name from calendar.toml
  "day": 2,
  "of": 5
}
```

Absent, not null, on baseline runs. The dashboard renders a badge and groups the window's issues under the conference name; the manifest carries `surge.window` so grouping doesn't require opening every issue ([08](08-publishing-and-dashboard.md#the-issue-manifest)).

## The conference calendar (unchanged)

### It self-maintains from primary sources

Societies publish their own dates, making these **primary-source facts, not judgment**. So: seeded once by a human, re-verified by the loop each cycle against each window's `source`, written as a visible git diff with the source cited.

> **The loop must never write a date it did not read from `source`.**

### It fails toward surging

The cost is asymmetric: surging on a wrong week costs a few runs; missing the ASCO plenary costs the year's biggest story. Where the two conflict, bias to surge. The one exception is `require_verified_dates`: an **unverified** window surges nothing — an honest gap beats a confident guess.

### Staleness

A stale calendar is **the only failure that would otherwise be silent** — every other missing piece announces itself. That breaks house rule 1 ([01](01-overview.md#1-a-missing-piece-bends-the-output-it-never-kills-the-run)), so staleness is a **declared degradation**, `calendar_stale` in [the register](06-validator-and-critic.md#the-degradation-register).

| Condition | Behaviour |
|---|---|
| `valid_through` has passed | Every issue carries `conference calendar stale — surge disabled`; critic files `calendar_stale` (advisory). |
| A window's dates are unverified | That window surges nothing; the marker names it. |
| No window verified in **N = 8** cycles ⚑ | Same marker; the loop's own verification step is failing. |

**⚑ N = 8 is provisional.** Counted per run, independent of surge cadence. The number most likely to be wrong; one line in config.

### Seeded state

All windows — `jpm`, `aacr`, `asco`, `wclc`, `esmo`, `ash` — ship with `starts`, `ends` and `verified_at` **empty**; `typical_window` carries the month-level pattern and tells the verifier when to look. Until a run resolves them against `source`, no window surges and the marker explains why. This is the fails-toward-honesty rule applied to the seed itself.

## Open, deferred by decision

Neither blocks a build; both want real cadence data first.

- Whether a surge window should widen the **baseline** coverage window of the first post-window run, so the next monthly run doesn't re-report the whole conference.
- Whether `jpm` deserves a different knob set — a deal-announcement venue, not a data venue.

---

*Provenance: the three-trigger cadence from pivot map [#49](https://github.com/cmengu/Research-Swarm/issues/49) decision 5; the surge mechanism, calendar and staleness inherited from v1 [#18](https://github.com/cmengu/Research-Swarm/issues/18); the embargo calendar from source set [#51](https://github.com/cmengu/Research-Swarm/issues/51). The `N` value is a ⚑ default under [#24](https://github.com/cmengu/Research-Swarm/issues/24).*
