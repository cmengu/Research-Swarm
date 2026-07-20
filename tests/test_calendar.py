"""The self-verifying conference calendar and surge state (build 10, #37).

These are the ticket's acceptance criteria as behaviour: the never-write-unread-
dates rule is mechanical (the model proposes, the gate decides), an unverified
window surges nothing, a verified window switches to daily and stamps run.surge,
max_surge_days guards an impossible span, and a stale calendar is loud, not silent.

Every model-facing test injects a fake runner — nothing here reaches a real
`claude` binary (the offline fixture is the backstop).
"""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from researchswarm.cadence import Surge
from researchswarm.calendar import (
    Calendar,
    Window,
    _source_matches,
    freshest_verified_at,
    load_calendar,
    resolve_surge,
    runs_since_verified,
    stale_reason,
    verify_calendar,
    verify_window,
    write_verified_dates,
)

# ASCO 2026 as a resolved window: Fri 29 May → Tue 2 Jun (5 days).
ASCO_STARTS = "2026-05-29"
ASCO_ENDS = "2026-06-02"


def _window(wid="asco", *, starts="", ends="", verified_at="", source="https://asco.org/am"):
    return Window(
        id=wid, name=f"{wid.upper()} Annual Meeting", typical_window="late May",
        note="", source=source, starts=starts, ends=ends, verified_at=verified_at,
    )


def _verified_window(**kw):
    return _window(starts=ASCO_STARTS, ends=ASCO_ENDS, verified_at="2026-05-20T07:00:00", **kw)


def _surge_cfg(*, enabled=True, require_verified=True, max_days=7, stale_after=8):
    return Surge(
        enabled=enabled, cadence="daily", require_verified_dates=require_verified,
        max_surge_days=max_days, stale_after_cycles=stale_after,
    )


def _runner(payload, *, returncode=0, cost=0.1):
    """A fake claude -p subprocess.run: wraps `payload` (a dict, or a raw string
    for the malformed cases) in the result envelope the transport layer parses."""
    result = payload if isinstance(payload, str) else json.dumps(payload)

    def run(command, **kwargs):
        envelope = json.dumps(
            {"is_error": False, "result": result, "total_cost_usd": cost, "num_turns": 2}
        )
        return SimpleNamespace(returncode=returncode, stdout=envelope, stderr="")

    return run


def _proposal(starts=ASCO_STARTS, ends=ASCO_ENDS, source="https://asco.org/am", found=True):
    return {"found": found, "starts": starts, "ends": ends, "source": source}


class TestLoadCalendar:
    def test_loads_the_real_seeded_config(self, repo_root):
        """The committed calendar must parse, and every window it claims to have
        verified must be well-formed.

        This test used to assert that NO window was verified. That was true, and
        it was true for the wrong reason: `_main_v2` never called the verifier, so
        the calendar could not become fresh and "all unverified" had quietly
        become an invariant of a bug. The moment verification was wired up, ASH
        resolved against hematology.org and this test failed — correctly.

        So it no longer asserts the verification STATE at all. This file is
        loop-maintained; its dates are runtime data, and a test that pins runtime
        data breaks every time the maintenance works. What must hold is the
        structure: the six windows exist, and anything marked verified carries
        dates that actually parse."""
        calendar = load_calendar(repo_root / "config" / "calendar.toml")
        ids = {w.id for w in calendar.windows}
        assert ids == {"jpm", "aacr", "asco", "wclc", "esmo", "ash"}
        assert calendar.valid_through == date(2027, 1, 31)
        for window in calendar.windows:
            if window.verified:
                assert window.starts and window.ends and window.verified_at
                assert date.fromisoformat(window.starts) <= date.fromisoformat(window.ends)

    def test_missing_file_is_an_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_calendar(tmp_path / "nope.toml")

    def test_window_without_source_fails_loudly(self, tmp_path):
        path = tmp_path / "calendar.toml"
        path.write_text('[[window]]\nid = "asco"\nname = "ASCO"\n')
        with pytest.raises(ValueError, match="missing id or source"):
            load_calendar(path)


class TestWindowVerified:
    def test_empty_is_unverified(self):
        assert not _window().verified

    def test_resolved_window_is_verified_with_its_span(self):
        w = _verified_window()
        assert w.verified
        assert w.span == 5  # 29 May → 2 Jun inclusive

    def test_end_before_start_is_not_verified(self):
        assert not _window(starts=ASCO_ENDS, ends=ASCO_STARTS, verified_at="x").verified

    def test_dates_without_a_stamp_are_not_verified(self):
        """A row with dates but no verified_at is not a resolved window."""
        assert not _window(starts=ASCO_STARTS, ends=ASCO_ENDS).verified


class TestResolveSurge:
    def test_verified_window_containing_today_surges(self):
        cal = Calendar(valid_through=None, windows=(_verified_window(),))
        surge = resolve_surge(cal, _surge_cfg(), date(2026, 5, 30))
        assert surge is not None
        assert surge.run_block == {"window": "ASCO Annual Meeting", "day": 2, "of": 5}
        assert (surge.starts, surge.ends) == (ASCO_STARTS, ASCO_ENDS)

    def test_first_and_last_day_are_in_window(self):
        cal = Calendar(valid_through=None, windows=(_verified_window(),))
        assert resolve_surge(cal, _surge_cfg(), date(2026, 5, 29)).day == 1
        assert resolve_surge(cal, _surge_cfg(), date(2026, 6, 2)).day == 5

    def test_outside_the_window_no_surge(self):
        cal = Calendar(valid_through=None, windows=(_verified_window(),))
        assert resolve_surge(cal, _surge_cfg(), date(2026, 6, 3)) is None

    def test_unverified_window_surges_nothing(self):
        """require_verified_dates: a guessed date would surge while claiming
        verification — an honest gap beats a confident guess."""
        cal = Calendar(valid_through=None, windows=(_window(),))  # empty dates
        assert resolve_surge(cal, _surge_cfg(require_verified=True), date(2026, 5, 30)) is None

    def test_disabled_surge_never_fires(self):
        cal = Calendar(valid_through=None, windows=(_verified_window(),))
        assert resolve_surge(cal, _surge_cfg(enabled=False), date(2026, 5, 30)) is None
        assert resolve_surge(cal, None, date(2026, 5, 30)) is None

    def test_the_cadence_knob_is_honoured(self):
        """spec/02: a surge window sets cadence='daily'. The knob is read, not
        assumed — a value other than 'daily' fires nothing."""
        import dataclasses

        cal = Calendar(valid_through=None, windows=(_verified_window(),))
        weekly = dataclasses.replace(_surge_cfg(), cadence="weekly")
        assert resolve_surge(cal, weekly, date(2026, 5, 30)) is None
        assert resolve_surge(cal, _surge_cfg(), date(2026, 5, 30)) is not None

    def test_impossible_span_is_a_data_error_not_a_surge(self):
        """max_surge_days guards a window that resolved to an impossible length —
        it must not switch the loop to daily for a fortnight."""
        long = _window(starts="2026-05-29", ends="2026-06-20", verified_at="x")  # 23 days
        cal = Calendar(valid_through=None, windows=(long,))
        assert resolve_surge(cal, _surge_cfg(max_days=7), date(2026, 5, 30)) is None


class TestStaleness:
    def test_never_verified_is_stale(self):
        """The seeded state and run #1: nothing resolved, so stale until the
        verification step succeeds once."""
        cal = Calendar(valid_through=date(2027, 1, 31), windows=(_window(),))
        assert freshest_verified_at(cal) is None
        reason = stale_reason(cal, today=date(2026, 5, 1), cycles_since_verified=None, stale_after_cycles=8)
        assert reason and "ever been verified" in reason

    def test_valid_through_passed_is_stale(self):
        cal = Calendar(valid_through=date(2026, 1, 31), windows=(_verified_window(),))
        reason = stale_reason(cal, today=date(2026, 5, 1), cycles_since_verified=0, stale_after_cycles=8)
        assert reason and "valid_through" in reason

    def test_fresh_within_n_cycles_is_not_stale(self):
        cal = Calendar(valid_through=date(2027, 1, 31), windows=(_verified_window(),))
        assert stale_reason(cal, today=date(2026, 5, 25), cycles_since_verified=3, stale_after_cycles=8) is None

    def test_n_cycles_without_a_verify_is_stale(self):
        cal = Calendar(valid_through=date(2027, 1, 31), windows=(_verified_window(),))
        reason = stale_reason(cal, today=date(2026, 6, 30), cycles_since_verified=8, stale_after_cycles=8)
        assert reason and "8 run" in reason

    def test_runs_since_verified_counts_issues_after_the_stamp(self, tmp_path):
        """Each run writes one dated issue, so the issues on disk are the run
        ledger — count those dated after the freshest verification."""
        issues = tmp_path / "issues"
        issues.mkdir()
        for iid in ("2026-05-18", "2026-05-21", "2026-05-25"):  # stamp is 2026-05-20
            (issues / f"{iid}.json").write_text(json.dumps({"issue": {"id": iid}}))
        (issues / "index.json").write_text("{}")  # never counted
        cal = Calendar(valid_through=None, windows=(_verified_window(),))
        assert runs_since_verified(issues, cal) == 2  # 21st and 25th, not the 18th

    def test_runs_since_verified_is_none_when_never_verified(self, tmp_path):
        (tmp_path / "issues").mkdir()
        cal = Calendar(valid_through=None, windows=(_window(),))
        assert runs_since_verified(tmp_path / "issues", cal) is None


class TestVerifyWindowMechanicalGate:
    """The never-write-unread-dates rule: the model proposes, the gate decides,
    and it writes a date only when the model attributes it to the window's OWN
    source (mirrors the critic's mechanical receipt rule)."""

    def test_accepts_a_well_attributed_date(self):
        out = verify_window(_window(), model="m", max_surge_days=7, runner=_runner(_proposal()))
        assert out.verified
        assert (out.starts, out.ends) == (ASCO_STARTS, ASCO_ENDS)

    def test_rejects_a_date_read_from_a_different_source(self):
        """The heart of the rule: a date the model read somewhere ELSE is not
        attributed to this window's source, so it is dropped — never written."""
        payload = _proposal(source="https://some-blog.example.com/asco-dates")
        out = verify_window(_window(), model="m", max_surge_days=7, runner=_runner(payload))
        assert not out.verified
        assert "own source" in out.reason

    def test_accepts_a_deeper_subpage_of_the_same_source(self):
        payload = _proposal(source="https://asco.org/am/attend/dates")
        out = verify_window(_window(source="https://asco.org/am"), model="m",
                            max_surge_days=7, runner=_runner(payload))
        assert out.verified

    def test_rejects_an_over_max_surge_span(self):
        """max_surge_days at the write boundary too: a hallucinated far-future end
        never becomes a written date."""
        payload = _proposal(ends="2026-06-20")  # 23 days
        out = verify_window(_window(), model="m", max_surge_days=7, runner=_runner(payload))
        assert not out.verified
        assert "span" in out.reason

    def test_rejects_unparseable_dates(self):
        payload = _proposal(starts="early June", ends="mid June")
        out = verify_window(_window(), model="m", max_surge_days=7, runner=_runner(payload))
        assert not out.verified and "unparseable" in out.reason

    def test_end_before_start_is_rejected(self):
        payload = _proposal(starts=ASCO_ENDS, ends=ASCO_STARTS)
        out = verify_window(_window(), model="m", max_surge_days=7, runner=_runner(payload))
        assert not out.verified and "before" in out.reason

    def test_found_false_writes_nothing(self):
        out = verify_window(_window(), model="m", max_surge_days=7,
                            runner=_runner(_proposal(found=False)))
        assert not out.verified and "no dates" in out.reason


class TestVerifyWindowFailuresDegrade:
    """A broken verifier degrades to no-surge, it never crashes the run."""

    def test_nonzero_exit_degrades(self):
        out = verify_window(_window(), model="m", max_surge_days=7,
                            runner=_runner(_proposal(), returncode=1))
        assert not out.verified and "exited" in out.reason

    def test_unparseable_envelope_degrades(self):
        out = verify_window(_window(), model="m", max_surge_days=7, runner=_runner("not json at all"))
        assert not out.verified and "unparseable" in out.reason

    def test_offline_guard_degrades_without_calling(self, monkeypatch):
        """Offline + the real runner: no verification this cycle, and crucially
        NO real call — but a degrade, not a raise, because 'could not reach the
        source' is a first-class spec outcome for the verifier."""
        import subprocess
        monkeypatch.setenv("RESEARCHSWARM_OFFLINE", "1")
        out = verify_window(_window(), model="m", max_surge_days=7, runner=subprocess.run)
        assert not out.verified and "offline" in out.reason


class TestVerifyCalendar:
    def test_collects_verified_windows_and_their_sources(self):
        good = _window("asco", source="https://asco.org/am")
        bad = _window("aacr", source="https://aacr.org/meeting")

        def runner(command, **kwargs):
            # ASCO's source is in its prompt; return a matching proposal for it and
            # a wrong-source (dropped) proposal for AACR.
            prompt = command[command.index("-p") + 1]
            if "asco.org" in prompt:
                result = json.dumps(_proposal(source="https://asco.org/am"))
            else:
                result = json.dumps(_proposal(source="https://blog.example.com"))
            envelope = json.dumps({"is_error": False, "result": result,
                                   "total_cost_usd": 0.1, "num_turns": 2})
            return SimpleNamespace(returncode=0, stdout=envelope, stderr="")

        cal = Calendar(valid_through=None, windows=(good, bad))
        result = verify_calendar(cal, model="m", max_surge_days=7, runner=runner)
        assert result.updated == ("asco",)
        assert result.sources["asco"] == "https://asco.org/am"


class TestWriteVerifiedDates:
    def test_surgical_edit_fills_one_window_and_preserves_comments(self, repo_root, tmp_path):
        """The write is a minimal, comment-preserving diff: only the named window's
        starts/ends/verified_at change; every comment and other window stays byte
        for byte — the diff IS the review (spec/02)."""
        src = (repo_root / "config" / "calendar.toml").read_text()
        path = tmp_path / "calendar.toml"
        path.write_text(src)

        changed = write_verified_dates(
            path, "2026-05-20T07:00:00", {"asco": {"starts": ASCO_STARTS, "ends": ASCO_ENDS}}
        )
        assert changed is True
        after = path.read_text()

        # The comments survive verbatim.
        assert "WHO MAINTAINS IT" in after
        # ASCO now carries its dates.
        cal = load_calendar(path)
        asco = next(w for w in cal.windows if w.id == "asco")
        assert (asco.starts, asco.ends, asco.verified_at) == (ASCO_STARTS, ASCO_ENDS, "2026-05-20T07:00:00")
        # No other window was touched — AACR is still empty.
        aacr = next(w for w in cal.windows if w.id == "aacr")
        assert not aacr.verified
        # The diff is exactly the three changed lines (× nothing else).
        changed_lines = [
            (a, b) for a, b in zip(src.splitlines(), after.splitlines()) if a != b
        ]
        assert len(changed_lines) == 3

    def test_no_matching_window_is_a_noop(self, repo_root, tmp_path):
        path = tmp_path / "calendar.toml"
        path.write_text((repo_root / "config" / "calendar.toml").read_text())
        assert write_verified_dates(path, "x", {"nonexistent": {"starts": "a", "ends": "b"}}) is False

    def test_a_block_missing_a_field_line_writes_nothing_and_warns(self, tmp_path, caplog):
        """A window block without a `starts`/`ends` line must not get verified_at
        stamped alone — that leaves a half-written window that reads as verified
        while its span degrades silently. Refuse the write and name the gap."""
        import logging

        path = tmp_path / "calendar.toml"
        # A block with ends + verified_at but NO starts line.
        path.write_text(
            '[[window]]\nid = "asco"\nends = ""\nverified_at = ""\n'
        )
        with caplog.at_level(logging.WARNING):
            changed = write_verified_dates(
                path, "2026-05-20T07:00:00", {"asco": {"starts": ASCO_STARTS, "ends": ASCO_ENDS}}
            )
        assert changed is False
        assert 'verified_at = ""' in path.read_text()  # nothing stamped
        assert "asco" in caplog.text and "starts" in caplog.text


class TestSourceMatches:
    @pytest.mark.parametrize(
        "reported,canonical",
        [
            ("https://asco.org/am", "https://asco.org/am"),
            ("http://www.asco.org/am/", "https://asco.org/am"),
            ("https://asco.org/am/attend/dates", "https://asco.org/am"),
        ],
    )
    def test_same_site_matches(self, reported, canonical):
        assert _source_matches(reported, canonical)

    @pytest.mark.parametrize(
        "reported,canonical",
        [
            ("https://blog.example.com/asco", "https://asco.org/am"),
            ("https://ascopubs.org/x", "https://asco.org/am"),
            ("", "https://asco.org/am"),
        ],
    )
    def test_different_site_does_not_match(self, reported, canonical):
        assert not _source_matches(reported, canonical)

    def test_prefix_without_a_host_boundary_is_rejected(self):
        """The attack: a look-alike host that merely STARTS with the source.
        The `/` boundary is what makes asco.org.evil.example a different host."""
        assert not _source_matches("https://asco.org.evil.example/dates", "https://asco.org")

    def test_a_bare_homepage_is_not_attribution_for_a_deeper_page(self):
        """The dropped reverse direction: dates we told the model to read on
        asco.org/am are not attributed by a report of the bare homepage."""
        assert not _source_matches("https://asco.org", "https://asco.org/am")
