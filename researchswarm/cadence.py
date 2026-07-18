"""When a run happens.

The OS scheduler is a dumb daily heartbeat: it fires run.py at 07:00 local,
every day, forever, and is never rewritten. This module is the only thing that
decides whether today is a run day.

Self-gating is what keeps cadence declarative, versioned, OS-agnostic, and
testable by faking the date. The alternative — rewriting Task Scheduler / cron
/ launchd entries when a surge window opens — means per-OS code, elevated
privileges on Windows, and a silent failure mode on the one machine that is
hardest to debug remotely.

Spec: docs/spec/02-cadence-and-surge.md
"""

from __future__ import annotations

import tomllib
from calendar import monthrange  # stdlib, NOT researchswarm.calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, see the v2 section
    from researchswarm.calendar import SurgeState
    from researchswarm.programs import Program

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True)
class Surge:
    """The [surge] + [surge.guard] knobs, parsed once surge grew its consumer.

    `cadence` is what a verified window switches the loop to (`daily`); the guards
    are the fails-toward-surging-but-not-toward-a-guess rules (spec/02):
      - `require_verified_dates` — an unverified window surges nothing;
      - `max_surge_days` — a window claiming a longer span is a data error, not a
        surge (the guard against a hallucinated end date);
      - `stale_after_cycles` — N: no window verified in this many runs and the
        calendar is declared stale. Config, never hardcoded, because it is the
        number most likely to be recalibrated once real cadence data exists.
    """

    enabled: bool
    cadence: str
    require_verified_dates: bool
    max_surge_days: int
    stale_after_cycles: int


@dataclass(frozen=True)
class Cadence:
    """What this stage of the system reads from cadence.toml.

    `surge` is the parsed [surge] block (None only if the file omits it entirely).
    It is parsed here now that surge mode is its consumer — a window switches
    `days` to daily and the guards gate whether a window may surge at all.
    """

    days: list[str]
    hour: int
    cold_start_lookback_days: int = 7
    surge: Surge | None = None


def is_run_day(cadence: Cadence, today: date) -> bool:
    """Is today a run day? A skipped day is a no-op, not a run.

    Baseline days only — the surge carve-out (a verified conference window makes
    every day a run day) is resolved from calendar.toml by run.py's gate, not
    here, because it needs the calendar this module deliberately does not read.
    """
    return DAY_NAMES[today.weekday()] in {d.lower() for d in cadence.days}


def load_cadence(path: Path) -> Cadence:
    """Load and validate config/cadence.toml.

    Validation is strict on purpose. A typo'd day name that silently never runs
    is precisely the kind of quiet failure this system exists to refuse, and it
    would be invisible until someone noticed the digests had stopped.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"cadence config not found: {path}")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    baseline = raw.get("baseline", {})
    days = [str(d).lower() for d in baseline.get("days", [])]
    for day in days:
        if day not in DAY_NAMES:
            raise ValueError(
                f"{path}: unknown day {day!r} in [baseline].days — expected one of {DAY_NAMES}"
            )

    hour = baseline.get("hour", 7)
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError(f"{path}: [baseline].hour must be an integer 0-23, got {hour!r}")

    return Cadence(
        days=days,
        hour=hour,
        cold_start_lookback_days=baseline.get("cold_start_lookback_days", 7),
        surge=_load_surge(path, raw),
    )


def _load_surge(path: Path, raw: dict) -> Surge | None:
    """Parse [surge] + [surge.guard], strict like the rest of the loader.

    Returns None only when the file omits [surge] entirely — a system that never
    wants surge. A present-but-malformed knob fails loudly here rather than
    silently disabling surge: a max_surge_days that is not a positive int, or a
    stale_after_cycles that is not, would otherwise let the biggest 72 hours of
    the year pass at baseline cadence with nothing to show the operator why.
    """
    surge = raw.get("surge")
    if surge is None:
        return None
    guard = surge.get("guard", {})
    max_days = guard.get("max_surge_days")
    if not isinstance(max_days, int) or max_days < 1:
        raise ValueError(
            f"{path}: [surge.guard].max_surge_days must be a positive integer, got {max_days!r}"
        )
    stale_after = guard.get("stale_after_cycles")
    if not isinstance(stale_after, int) or stale_after < 1:
        raise ValueError(
            f"{path}: [surge.guard].stale_after_cycles must be a positive integer, "
            f"got {stale_after!r}"
        )
    return Surge(
        enabled=bool(surge.get("enabled", False)),
        cadence=str(surge.get("cadence", "daily")),
        require_verified_dates=bool(guard.get("require_verified_dates", True)),
        max_surge_days=max_days,
        stale_after_cycles=stale_after,
    )


# ---------------------------------------------------------------------------
# v2 — the per-program cadence dial
#
# The pivot replaced v1's single global Mon+Thu list with THREE triggers
# (spec/02 "The three triggers"): a per-program baseline dial, an automatic
# conference surge, and a manual push. Everything below is the v2 path; the v1
# functions above are untouched and are deleted last, as their own ticket.
#
# The shape of the question changed, not just its inputs. v1 asked "is today in
# the day-of-week list?" — a property of the calendar alone, so a pure function
# of `today`. v2 asks "has enough time elapsed since THIS program's last issue?"
# — a property of the program's own history, because spec/02 pins the coverage
# window of each run to run "from the program's previous issue to today". Making
# elapsed-time the decision is what makes that true automatically: turning a
# program's dial changes its window width instead of leaving a gap between the
# last issue and the first run under the new dial.
# ---------------------------------------------------------------------------

# ⚑ The per-program baseline default (spec/02 "Baseline cadence: the per-program
# dial": `baseline = "monthly"  # ⚑ default; per-program, flippable`). Monthly is
# a stated default, not an invariant — SOC and competitive posture move in months,
# and the registry watch (spec/04) already catches the between-cycle deltas that
# used to justify v1's twice-weekly beat. The parse of this value already lives in
# programs.load_program (`Program.cadence_baseline`); it is NOT reparsed here.
DEFAULT_BASELINE_V2 = "monthly"

# The dial's vocabulary, in months. Only the two values spec/02 actually states
# are accepted: `monthly` (the baseline default) and `daily` (what a surge window
# switches a program to, spec/02 "Surge is one knob"). Anything else — `weekly`,
# `quarterly` — is a value the spec never names, and a dial that silently accepts
# an invented word would be exactly the quiet failure v1's day-name validation
# refuses. Adding one is a config decision plus a line here.
BASELINE_MONTHS_V2 = {"monthly": 1}
DAILY_V2 = "daily"


@dataclass(frozen=True)
class CadenceDecisionV2:
    """Why a program does or does not run today — the answer plus its reason.

    A bare bool would be enough for the gate, but not for the operator. Spec/02
    makes a skipped day "a no-op, not a run": no issue, no stub, no dashboard
    entry, no trace. That silence is deliberate, and it is also the reason the
    decision has to be able to explain itself somewhere — the orchestrator logs
    `reason` on the way to exiting in milliseconds, so a program that has quietly
    stopped running has a machine-readable answer for why.

    `cadence` is the dial that was actually in force: the program's baseline, or
    `daily` when a surge is driving. It is what the issue records, so a run made
    at surge cadence is distinguishable from a baseline one after the fact.

    `reason` is one of:
      - `push`        — the human forced it (spec/02 "Manual push"); gate bypassed
      - `surge`       — a verified in-window conference holds one of this
                        program's competitors (spec/02 "Surge mode")
      - `cold_start`  — no previous issue for this program; run #1 is always due
      - `baseline_due`— a full baseline interval has elapsed since the last issue
      - `not_due`     — none of the above; today is a no-op for this program
    """

    run: bool
    reason: str
    cadence: str
    surge: SurgeState | None = None


def program_surge_v2(
    surge_state: SurgeState | None,
    roster: Iterable[str],
    competitors_in_window: Iterable[str],
) -> SurgeState | None:
    """This program's surge, or None — surge is per-program, not global.

    Spec/02 is precise about the scope: a window switches "any program with a
    competitor in that window" to daily, not every program on the machine. So a
    live window is necessary and not sufficient; the window has to hold someone
    on THIS program's roster. An ASH window is a real surge for a heme program
    and a dead week for HMBD-001.

    `surge_state` is `calendar.resolve_surge(...)`'s output and is taken on
    trust: the fails-toward-surging rule and both guards (`require_verified_dates`
    — an unverified window surges nothing; `max_surge_days` — a longer span is a
    data error) are already enforced there, against the `Surge` knobs parsed
    above. Re-checking them here would fork the guard into two homes, which is
    how the two copies eventually disagree.

    `roster` is `programs.program_roster(program, edges)` — promoted edges plus
    the not-yet-promoted `seed_competitors`, which at seed is exactly the cold-start
    set. `competitors_in_window` is who the caller has established is presenting in
    that window; entity ids, matched exactly, because a fuzzy match here would
    surge a program on a name collision.
    """
    if surge_state is None:
        return None
    if set(roster) & set(competitors_in_window):
        return surge_state
    return None


def is_run_day_v2(
    program: Program,
    today: date,
    *,
    last_issue_date: date | None,
    surge_state: SurgeState | None = None,
    push: bool = False,
) -> CadenceDecisionV2:
    """Is today a run day for THIS program? The whole v2 gate, in precedence order.

    The three triggers of spec/02, checked strongest first:

    1. **Manual push** bypasses the gate entirely. Spec/02: the human fires "a
       single out-of-cadence run for a named program", and it produces "a normal,
       dated program issue — same apertures, same gates, same rubric". So push
       forces `run=True` and changes nothing else; it is not a fourth mode. It
       wins over the baseline because its whole purpose is reacting to a break the
       human already knows about, which by definition is not on the dial. The CLI
       flag (`run.py --program hmbd-001 --push`) is the orchestrator's to wire —
       this is only the primitive that answers "yes, forced".

    2. **Surge** — pass `program_surge_v2(...)`'s output as `surge_state`, i.e. a
       verified in-window conference that holds one of this program's competitors.
       It sets cadence to `daily` for the window, so every day in it is a run day
       regardless of when the last issue landed.

    3. **Baseline** — has a full interval of the program's own dial elapsed since
       its previous issue?

    MONTHLY SEMANTICS, stated explicitly because the spec states the consequence
    and not the arithmetic: a program is due when `today` is on or after the
    calendar-month anniversary of its previous issue — 14 Feb after a 14 Jan
    issue, and the last day of the month when the anniversary would fall past it
    (a 31 Jan issue is next due 28 Feb, not 3 March). Calendar months, not a
    30.44-day approximation, because these dates are read by a human against a
    conference calendar; drifting a monthly issue a day earlier each cycle would
    be false precision with a visible cost. The interval is measured from the last
    ISSUE, not from the last attempted run, which is what makes spec/02's
    "coverage window ... from the program's previous issue to today" hold with no
    gaps: a day where the gate says no leaves the pending window untouched and
    slightly wider, never dropped.

    COLD START: no previous issue means run #1, and run #1 is always due — there
    is no interval to have elapsed. `Program.cold_start_lookback_days` (⚑ 7,
    spec/09) then sets how far back that first coverage window reaches; that is
    the orchestrator's to apply, not the gate's.

    Raises ValueError on a baseline the dial's vocabulary does not contain, on
    the same principle as v1's day-name validation: a typo that silently never
    runs is the failure this system exists to refuse, and it would be invisible
    until someone noticed the issues had stopped.
    """
    baseline = (program.cadence_baseline or DEFAULT_BASELINE_V2).lower()
    if baseline != DAILY_V2 and baseline not in BASELINE_MONTHS_V2:
        raise ValueError(
            f"{program.id}: unknown [cadence].baseline {program.cadence_baseline!r} — "
            f"expected one of {sorted([DAILY_V2, *BASELINE_MONTHS_V2])}"
        )

    if push:
        return CadenceDecisionV2(
            run=True, reason="push", cadence=baseline, surge=surge_state
        )

    if surge_state is not None:
        return CadenceDecisionV2(
            run=True, reason="surge", cadence=DAILY_V2, surge=surge_state
        )

    if last_issue_date is None:
        return CadenceDecisionV2(run=True, reason="cold_start", cadence=baseline)

    if today >= next_due_date_v2(last_issue_date, baseline):
        return CadenceDecisionV2(run=True, reason="baseline_due", cadence=baseline)

    return CadenceDecisionV2(run=False, reason="not_due", cadence=baseline)


def next_due_date_v2(last_issue_date: date, baseline: str) -> date:
    """The first date on which `baseline` comes due again after an issue.

    Exposed rather than buried in the gate because it is the one piece of cadence
    arithmetic anything else wants to show: the dashboard's "next run" line and
    an operator asking why nothing ran today both need the same answer, and two
    implementations of a month boundary would eventually disagree on 31 January.
    """
    baseline = (baseline or DEFAULT_BASELINE_V2).lower()
    if baseline == DAILY_V2:
        return last_issue_date + timedelta(days=1)
    months = BASELINE_MONTHS_V2.get(baseline)
    if months is None:
        raise ValueError(
            f"unknown baseline {baseline!r} — "
            f"expected one of {sorted([DAILY_V2, *BASELINE_MONTHS_V2])}"
        )
    return _add_months(last_issue_date, months)


def _add_months(start: date, months: int) -> date:
    """`start` shifted by whole calendar months, clamped to the month's last day.

    The clamp is the only interesting case: month 1 day 31 plus one month has no
    literal answer. Clamping down (31 Jan -> 28 Feb) keeps a program's issues in
    the month they belong to; overflowing up (-> 3 March) would let a late-month
    program skip February's issue entirely in a short year.
    """
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(start.day, monthrange(year, month)[1]))


def cold_start_shortfall_v2(program) -> int | None:
    """Days by which a program's cold-start window falls short of one baseline cycle.

    Returns None when the window is sound, else the shortfall in days.

    A cold-start window SHORTER than the cadence it feeds is a config error that
    reports as a quiet market. run_20260718_2258 is the worked example: a monthly
    program with `cold_start_lookback_days = 7` surfaced five in-scope items —
    including a $1.1B ADC-platform acquisition five days out of window — judged
    them out of scope, and published `house_view.threat_financing` EMPTY. The
    lens was not quiet; the window was.

    This belongs in code rather than in a reviewer's head because it is
    mechanically decidable from two numbers the orchestrator already holds
    ([01] determinism before judgment): a run has no way to know the market was
    louder than its window, so the only moment the mismatch is visible is before
    the run, from config alone.

    The floor is ONE baseline cycle — the weakest defensible bar, not a
    recommendation. A first run's window is the only history the detective ever
    sees for everything preceding it, so a deeper cold start is usually right;
    this catches the case that cannot be right.
    """
    months = BASELINE_MONTHS_V2.get(program.cadence_baseline)
    if months is None:  # daily, or a baseline validated elsewhere
        return None
    cycle_days = months * 30  # nominal; the check is an order-of-magnitude guard
    lookback = program.cold_start_lookback_days
    return cycle_days - lookback if lookback < cycle_days else None
