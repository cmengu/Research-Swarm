"""Stage 1 — the self-verifying conference calendar and surge state.

Agenda-setting emissions in oncology are episodic and calendared: both 2026
repricing events landed at a single ASCO plenary. So the loop keeps its Mon+Thu
baseline and adds a conference surge — daily runs inside a verified window,
which slices the firehose into daily servings instead of one 72-hour flood.

Two rules that look contradictory but are not (spec/02):

  - the calendar **fails toward surging** — a wasted run costs quota, a missed
    plenary costs the year's biggest story;
  - except an **unverified window surges nothing** — a guessed date would surge
    while *claiming* verification, and an honest gap beats a confident guess.

The **never-write-unread-dates rule is MECHANICAL**, exactly like the critic's
receipt rule (critic.enforce_receipt_rule): the verifier model PROPOSES dates
and the source URL it read them from; this module's deterministic `_accept_dates`
DECIDES what gets written, and writes a date only when the model attributes it to
the window's OWN `source`. A date read somewhere else, unparseable, or claiming
an impossible span (`max_surge_days`) is dropped — the window stays an honest
empty, never a confident guess. The model cannot write the calendar; only the
orchestrator can, and only through this gate.

A stale calendar is the ONE failure that would otherwise be silent — surge just
never fires. So it is a declared degradation: `stale_reason` names it, run.py
files the `calendar_stale` advisory, and the marker renders on every issue.

Spec: docs/spec/02-cadence-and-surge.md, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from researchswarm.cadence import Surge
from researchswarm.researcher import OFFLINE_ENV
from researchswarm.transport import TransportInvalid, parse_envelope, parse_result_json

log = logging.getLogger("researchswarm.calendar")

# The verifier gets web search/fetch and NOTHING that could write or delegate —
# the same wall the researcher runs behind (researcher.ALLOWED/DISALLOWED). It
# reads a society's own page; it must never touch the calendar it is verifying.
ALLOWED_TOOLS = ("WebSearch", "WebFetch")
DISALLOWED_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Task")


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """One conference window from calendar.toml.

    `starts`/`ends`/`verified_at` ship EMPTY (dates were deliberately not
    invented); a window is `verified` only once a run has resolved them against
    `source`. `typical_window` is the month-level pattern that tells the verifier
    when to look — it is guidance, never a date the loop would write.
    """

    id: str
    name: str
    typical_window: str
    note: str
    source: str
    starts: str
    ends: str
    verified_at: str

    @property
    def verified(self) -> bool:
        """Dates present and internally coherent — the surge precondition.

        Empty is the seeded state (not verified). Non-empty but unparseable, or
        an end before its start, is a corrupt row and also does not count as
        verified: only a well-formed, resolved window may surge."""
        span = self.span
        return span is not None

    @property
    def span(self) -> int | None:
        """Inclusive day count of the window, or None if the dates aren't a
        well-formed present range. One home for 'how long is this window'."""
        if not (self.verified_at and self.starts and self.ends):
            return None
        try:
            start = date.fromisoformat(self.starts)
            end = date.fromisoformat(self.ends)
        except ValueError:
            return None
        if end < start:
            return None
        return (end - start).days + 1


@dataclass(frozen=True)
class Calendar:
    """The six windows plus `valid_through` — past which every issue is stale."""

    valid_through: date | None
    windows: tuple[Window, ...]


def load_calendar(path: Path) -> Calendar:
    """Load and validate config/calendar.toml.

    Strict on the shape (every window needs an id and a source — a window that
    cannot be fetched is a config error, not a silent no-surge), lenient on the
    dates (empty is the honest seeded state). `valid_through` is a bare TOML date;
    tomllib hands it back as a `datetime.date`.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"calendar config not found: {path}")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    valid_through = raw.get("valid_through")
    if valid_through is not None and not isinstance(valid_through, date):
        raise ValueError(f"{path}: valid_through must be a date, got {valid_through!r}")

    windows: list[Window] = []
    for i, block in enumerate(raw.get("window", [])):
        wid = block.get("id")
        source = block.get("source")
        if not wid or not source:
            raise ValueError(f"{path}: [[window]] #{i} is missing id or source")
        windows.append(
            Window(
                id=str(wid),
                name=str(block.get("name", wid)),
                typical_window=str(block.get("typical_window", "")),
                note=str(block.get("note", "")),
                source=str(source),
                starts=str(block.get("starts", "")),
                ends=str(block.get("ends", "")),
                verified_at=str(block.get("verified_at", "")),
            )
        )
    return Calendar(valid_through=valid_through, windows=tuple(windows))


# ---------------------------------------------------------------------------
# Surge state — resolved from ALREADY-verified dates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurgeState:
    """A live surge: today falls inside a verified window.

    `run_block` is the spec-pinned `{window, day, of}` the issue carries (absent,
    not null, on a baseline run). `starts`/`ends` are the conference window the
    researcher carve-out and the critic's `provenance_stale` compare against —
    NOT stamped into `run.surge`, which stays exactly three fields for the
    dashboard, but supplied to the prompts that need the dates.
    """

    window: str
    window_id: str
    day: int
    of: int
    starts: str
    ends: str

    @property
    def run_block(self) -> dict:
        return {"window": self.window, "day": self.day, "of": self.of}


def resolve_surge(calendar: Calendar, surge: Surge | None, today: date) -> SurgeState | None:
    """The single verified window containing today, or None — the whole gate.

    Fails toward surging, with one exception the guard enforces: an UNVERIFIED
    window surges nothing when `require_verified_dates` is set (a guessed date is
    as likely to surge on the wrong week as the right one). `max_surge_days`
    rejects a window whose resolved span is impossibly long — a data error must
    not switch the loop to daily for a fortnight. First containing window wins;
    the six real windows do not overlap.
    """
    if surge is None or not surge.enabled:
        return None
    for window in calendar.windows:
        if surge.require_verified_dates and not window.verified:
            continue
        span = window.span
        if span is None:
            continue
        if span > surge.max_surge_days:
            log.warning(
                "surge: window %r resolves to %d days (> max_surge_days %d) — "
                "treating as a data error, not surging",
                window.id,
                span,
                surge.max_surge_days,
            )
            continue
        start = date.fromisoformat(window.starts)
        end = date.fromisoformat(window.ends)
        if start <= today <= end:
            return SurgeState(
                window=window.name,
                window_id=window.id,
                day=(today - start).days + 1,
                of=span,
                starts=window.starts,
                ends=window.ends,
            )
    return None


# ---------------------------------------------------------------------------
# Staleness — the only otherwise-silent failure
# ---------------------------------------------------------------------------


def freshest_verified_at(calendar: Calendar) -> str | None:
    """The most recent `verified_at` across all windows, or None if none verified.

    None is the seeded state and the run-#1 state: nothing has ever been resolved,
    so the calendar is stale until the verification step succeeds once."""
    stamps = [w.verified_at for w in calendar.windows if w.verified_at]
    return max(stamps) if stamps else None


def runs_since_verified(issues_dir: Path, calendar: Calendar) -> int | None:
    """How many runs have published since the freshest verification, or None.

    Each run writes exactly one dated issue (published or stub), so the issues on
    disk ARE the run ledger — counting those dated strictly after the freshest
    `verified_at` is 'cycles since a window last verified', per run and
    independent of surge cadence, with no new persistent counter to keep honest.
    None when nothing has ever been verified (the caller treats that as stale
    outright). Unreadable issue files are skipped, never fatal.
    """
    fresh = freshest_verified_at(calendar)
    if fresh is None:
        return None
    fresh_date = fresh[:10]  # date part; issue ids are dates, compared as strings
    issues_dir = Path(issues_dir)
    count = 0
    for path in issues_dir.glob("*.json"):
        if path.name == "index.json":
            continue
        try:
            issue = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        iid = issue.get("issue", {}).get("id")
        if isinstance(iid, str) and iid > fresh_date:
            count += 1
    return count


def stale_reason(
    calendar: Calendar, *, today: date, cycles_since_verified: int | None, stale_after_cycles: int
) -> str | None:
    """Why the calendar is stale, or None if it is fresh — the mechanical trigger.

    Three conditions, checked most-fundamental first (spec/02 staleness table):
      1. `valid_through` has passed — the whole calendar is out of date;
      2. no window has ever been verified — the seeded state, and run #1;
      3. no window verified in N (`stale_after_cycles`) runs — the loop's own
         verification step is failing.
    The string is the audit crumb; the marker the reader sees is fixed text.
    """
    if calendar.valid_through is not None and today > calendar.valid_through:
        return f"valid_through {calendar.valid_through.isoformat()} has passed"
    if cycles_since_verified is None:
        return "no conference window has ever been verified against its source"
    if cycles_since_verified >= stale_after_cycles:
        return f"no window verified in {cycles_since_verified} run(s) (limit {stale_after_cycles})"
    return None


# ---------------------------------------------------------------------------
# The verifier — model proposes, the gate decides what is written
# ---------------------------------------------------------------------------


VERIFIER_PROMPT = """\
You are the conference-calendar verifier for ResearchSwarm. Your ONLY job is to
read the official dates of ONE conference from its own page and report them.

Conference: {name}
Its own page (the ONLY source you may report dates from): {source}
Typical timing (guidance for which year's meeting to find, NOT a date to emit): {typical_window}

Fetch that page (follow one or two links within the same site if the dates live
on a sub-page). Find the NEXT edition's start and end dates. Report ONLY dates
you actually read on that site — never guess, never infer from the typical
timing, never carry over a prior year. If you cannot find dates on that site,
report found=false.

Your ENTIRE final message must be EXACTLY ONE JSON object — no markdown fences,
no preamble, no commentary. It is machine-parsed. The shape:

{{
  "found": true,
  "starts": "YYYY-MM-DD",
  "ends": "YYYY-MM-DD",
  "source": "the exact URL on {source}'s own site where you read these dates"
}}

If you could not read the dates from that site, emit exactly:

{{"found": false, "starts": null, "ends": null, "source": null}}
"""


@dataclass(frozen=True)
class WindowVerification:
    """One window's verification outcome BEFORE the calendar is written.

    `verified` means the model's proposed dates cleared the mechanical gate and
    the orchestrator may write them. Every failure — unreachable model, unparseable
    output, dates the model would not attribute to this window's own source, an
    impossible span — is `verified=False` with a `reason`, never a raise: a broken
    verifier degrades to no-surge, it never crashes the run (spec/02)."""

    window_id: str
    verified: bool
    starts: str | None = None
    ends: str | None = None
    source: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CalendarVerification:
    """The cycle's verification pass. `updated` names the windows whose dates the
    gate accepted this run — what the diff commits and what the commit message
    cites; `sources` maps each to the URL its dates were read from."""

    windows: tuple[WindowVerification, ...]
    updated: tuple[str, ...]
    sources: dict[str, str]


def build_verifier_command(prompt: str, model: str) -> list[str]:
    """The argv for one read-only verifier pass — twin of researcher.build_command.

    Web search/fetch in, everything that writes or delegates out, enforced by
    permission flags, not by asking. `-p` carries the prompt (short — one window),
    `--output-format json` gives the envelope the transport layer already parses.
    """
    return [
        "claude",
        "-p", prompt,
        "--model", model,
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--output-format", "json",
    ]


def verify_window(
    window: Window,
    *,
    model: str,
    max_surge_days: int,
    timeout: int = 300,
    runner=subprocess.run,
) -> WindowVerification:
    """Verify one window against its source. Never raises on a verifier failure.

    Every failure mode — offline guard, missing binary, nonzero exit, timeout,
    unparseable envelope or payload, `found: false`, and every reason the
    mechanical gate rejects the proposed dates — resolves to
    `WindowVerification(verified=False, reason=...)`. That is the spec contract:
    the calendar fails toward a WASTED run (no surge), never toward a crash or a
    guessed date. The offline guard degrades here rather than raising because
    'the verifier could not reach its source' is a first-class, spec-defined
    outcome — unlike the researcher/critic, whose offline misuse is purely a test
    artifact; it still logs loudly and never spends a real call.
    """
    if os.environ.get(OFFLINE_ENV) and runner is subprocess.run:
        log.warning(
            "calendar: %s not verified — %s is set and no fake runner was injected "
            "(no surge this cycle)",
            window.id,
            OFFLINE_ENV,
        )
        return WindowVerification(window.id, False, reason="offline: no verifier runner")

    prompt = VERIFIER_PROMPT.format(
        name=window.name, source=window.source, typical_window=window.typical_window
    )
    command = build_verifier_command(prompt, model)
    try:
        completed = runner(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return WindowVerification(window.id, False, reason="claude binary not found on PATH")
    except subprocess.TimeoutExpired:
        return WindowVerification(window.id, False, reason=f"verifier timed out after {timeout}s")

    if completed.returncode != 0:
        return WindowVerification(
            window.id, False, reason=f"claude exited {completed.returncode}"
        )

    try:
        envelope = parse_envelope(completed.stdout)
        payload = parse_result_json(envelope.get("result", ""))
    except TransportInvalid as exc:
        return WindowVerification(window.id, False, reason=f"unparseable verifier output: {exc}")

    return _accept_dates(window, payload, max_surge_days=max_surge_days)


def _accept_dates(window: Window, payload, *, max_surge_days: int) -> WindowVerification:
    """The mechanical never-write-unread-dates gate — the module's whole point.

    The model PROPOSES; this DECIDES. A date is accepted only when it clears every
    check, and the checks are ordered so the reason names the most fundamental
    problem first:

      - the payload is a JSON object with `found: true`;
      - `starts` and `ends` parse as ISO dates, `ends` on/after `starts`;
      - the span is within `max_surge_days` — the guard against a hallucinated end;
      - and the model attributes the dates to the window's OWN `source`
        (`_source_matches`) — a date read on some other page is exactly the guess
        this rule refuses to launder into a claimed verification.

    Anything else is `verified=False` with the reason. Only on a full pass are the
    dates returned for writing; the orchestrator writes nothing else.
    """
    if not isinstance(payload, dict):
        return WindowVerification(window.id, False, reason="verifier output was not a JSON object")
    if not payload.get("found"):
        return WindowVerification(window.id, False, reason="verifier found no dates on the source")

    starts, ends = payload.get("starts"), payload.get("ends")
    try:
        start = date.fromisoformat(str(starts))
        end = date.fromisoformat(str(ends))
    except (TypeError, ValueError):
        return WindowVerification(
            window.id, False, reason=f"proposed dates are unparseable ({starts!r} → {ends!r})"
        )
    if end < start:
        return WindowVerification(window.id, False, reason="proposed end is before its start")

    span = (end - start).days + 1
    if span > max_surge_days:
        return WindowVerification(
            window.id,
            False,
            reason=f"proposed span is {span} days (> max_surge_days {max_surge_days})",
        )

    reported = payload.get("source")
    if not isinstance(reported, str) or not _source_matches(reported, window.source):
        return WindowVerification(
            window.id,
            False,
            reason=f"dates not attributed to the window's own source (read from {reported!r})",
        )

    return WindowVerification(
        window.id, True, starts=start.isoformat(), ends=end.isoformat(), source=reported
    )


def _source_matches(reported: str, canonical: str) -> bool:
    """Did the model read the dates from the window's OWN source?

    Mechanical, like the receipt rule's url-in-corpus check. Both URLs are
    normalised (scheme, `www.`, and trailing slash stripped, lowercased) and one
    must be a prefix of the other — the model may cite the canonical page or a
    deeper sub-page under the same site, but a date read on a different host does
    not clear the bar. It never judges whether the dates are RIGHT, only that they
    came from where they were supposed to.
    """
    a, b = _normalize_url(reported), _normalize_url(canonical)
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def _normalize_url(url: str) -> str:
    u = url.strip().lower()
    for scheme in ("https://", "http://"):
        if u.startswith(scheme):
            u = u[len(scheme):]
            break
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def verify_calendar(
    calendar: Calendar,
    *,
    model: str,
    max_surge_days: int,
    timeout: int = 300,
    runner=subprocess.run,
) -> CalendarVerification:
    """Re-verify every window against its source this cycle (spec/02 criterion 1).

    One verifier call per window; each is independent and its failure isolated —
    a dead society page for AACR must not stop ASCO from verifying. Returns the
    per-window outcomes plus the ids whose dates the gate accepted, which is what
    run.py writes and commits.
    """
    outcomes: list[WindowVerification] = []
    updated: list[str] = []
    sources: dict[str, str] = {}
    for window in calendar.windows:
        outcome = verify_window(
            window, model=model, max_surge_days=max_surge_days, timeout=timeout, runner=runner
        )
        outcomes.append(outcome)
        if outcome.verified:
            updated.append(outcome.window_id)
            sources[outcome.window_id] = outcome.source or window.source
            log.info(
                "calendar: %s verified %s → %s (source %s)",
                window.id, outcome.starts, outcome.ends, outcome.source,
            )
        else:
            log.info("calendar: %s not verified this cycle — %s", window.id, outcome.reason)
    return CalendarVerification(
        windows=tuple(outcomes), updated=tuple(updated), sources=sources
    )


# ---------------------------------------------------------------------------
# The write — a minimal, comment-preserving diff
# ---------------------------------------------------------------------------


_WINDOW_SPLIT = re.compile(r"(?=^\[\[window\]\])", re.MULTILINE)


def write_verified_dates(path: Path, verified_at: str, dates: dict[str, dict]) -> bool:
    """Write accepted dates into calendar.toml in place, one window at a time.

    A SURGICAL edit, not a re-serialisation: the file is heavily commented (it is
    config a human seeded and reviews), and rewriting it through a TOML dumper
    would drown the one line that changed under lost comments and reflowed
    formatting. So each window's own `[[window]]` block has just its
    `starts`/`ends`/`verified_at` values replaced — the diff is exactly the dates
    the loop read, which is what makes it reviewable (spec/02: 'a visible git
    diff with the source cited'). Returns True if anything changed.

    `dates` maps window_id → {"starts", "ends"}; `verified_at` is this run's
    stamp, shared across every window resolved this cycle.
    """
    path = Path(path)
    text = path.read_text()
    parts = _WINDOW_SPLIT.split(text)
    changed = False
    new_parts = []
    for part in parts:
        match = re.search(r'^\s*id\s*=\s*"([^"]+)"', part, re.MULTILINE)
        wid = match.group(1) if match else None
        if wid in dates:
            updated = _set_field(part, "starts", dates[wid]["starts"])
            updated = _set_field(updated, "ends", dates[wid]["ends"])
            updated = _set_field(updated, "verified_at", verified_at)
            if updated != part:
                changed = True
                part = updated
        new_parts.append(part)
    if changed:
        path.write_text("".join(new_parts))
    return changed


def _set_field(block: str, key: str, value: str) -> str:
    """Replace one `key = "..."` value inside a window block, preserving the
    key's own alignment and every surrounding comment. Only the quoted value
    between the first pair of quotes on that line changes."""
    return re.sub(
        rf'(?m)^(\s*{key}\s*=\s*)"[^"]*"',
        lambda m: f'{m.group(1)}"{value}"',
        block,
        count=1,
    )
