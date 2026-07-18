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
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
