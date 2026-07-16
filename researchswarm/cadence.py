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
class Cadence:
    """What this stage of the system actually reads from cadence.toml.

    The file also carries [surge] and [surge.guard]. They are deliberately not
    parsed here: nothing reads them yet, and a half-wired knob is worse than an
    absent one — it looks configured while doing nothing. Surge mode parses them
    when it grows a consumer.
    """

    days: list[str]
    hour: int
    cold_start_lookback_days: int = 7


def is_run_day(cadence: Cadence, today: date) -> bool:
    """Is today a run day? A skipped day is a no-op, not a run."""
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
    )
