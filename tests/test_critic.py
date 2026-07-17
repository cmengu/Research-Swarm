"""The Codex critic: the command, the wire format, every not_run route, receipts.

The subprocess is injected so these run offline and deterministically, using the
verified codex wire format (probed live with codex-cli 0.142.5): stdout is JSONL
events, and the bare final message lands in the `-o` file. One live end-to-end
pass is a separate, opt-in test — see tests/test_live_critic.py.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchswarm.critic import (
    ADVISORY_KINDS,
    BLOCKING_KINDS,
    CriticOfflineViolation,
    build_codex_command,
    enforce_receipt_rule,
    run_critic,
)

MODEL = "gpt-5-codex"


def _run_prompt():
    """The stand-in critic prompt these tests inject — the wire is under test, not
    the rubric, so any non-empty prompt does."""
    return "CRITIC PROMPT: judge this issue."

# A realistic stdout event stream (the shape probed live). The verdict does NOT
# come from here — it comes from the -o file — so this is telemetry only.
STDOUT_JSONL = "\n".join(
    [
        json.dumps({"type": "thread.started", "thread_id": "th_abc"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "OK"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 11638, "output_tokens": 5}}),
    ]
)


def _fake_runner(final_message, *, stdout=STDOUT_JSONL, returncode=0, write=True):
    """A fake subprocess.run for codex: records the call, and (like real codex)
    writes the bare final message to the `-o` path found in the argv."""
    calls = []

    def runner(command, **kwargs):
        calls.append(SimpleNamespace(command=command, kwargs=kwargs))
        if write:
            out_path = command[command.index("-o") + 1]
            with open(out_path, "w") as fh:
                fh.write(final_message)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    runner.calls = calls
    return runner


def _verdict(verdict, *, blocking=None, advisory=None):
    return json.dumps(
        {
            "verdict": verdict,
            "blocking_findings": blocking or [],
            "advisory_findings": advisory or [],
        }
    )


@pytest.fixture(autouse=True)
def _offline_off(monkeypatch):
    """These tests inject a fake runner, so the offline guard (which only trips on
    the REAL subprocess.run) is irrelevant — but clear it so a stray real call
    would be caught by the runner-identity check, not the env, and the intent is
    explicit."""
    monkeypatch.delenv("RESEARCHSWARM_OFFLINE", raising=False)


# --- the happy verdicts ----------------------------------------------------


class TestVerdicts:
    def test_pass_with_no_findings(self):
        result = run_critic(_run_prompt(), model=MODEL, runner=_fake_runner(_verdict("pass")))
        assert result.verdict == "pass"
        assert result.blocking_findings == ()
        assert result.advisory_findings == ()
        assert result.reason is None

    def test_pass_with_advisories(self):
        advisory = [{"kind": "thin_sourcing", "where": "watchlist.hengrui", "note": "single source"}]
        runner = _fake_runner(_verdict("pass_with_advisories", advisory=advisory))
        result = run_critic(_run_prompt(), model=MODEL, runner=runner)
        assert result.verdict == "pass_with_advisories"
        assert result.advisory_findings == tuple(advisory)

    def test_blocked_with_valid_blocking_findings(self):
        blocking = [{"kind": "overclaim", "where": "headline", "note": "hedged rendered as certain"}]
        runner = _fake_runner(_verdict("blocked", blocking=blocking))
        result = run_critic(_run_prompt(), model=MODEL, runner=runner)
        assert result.verdict == "blocked"
        assert result.blocking_findings == tuple(blocking)

    def test_metadata_is_pulled_from_stdout(self):
        result = run_critic(_run_prompt(), model=MODEL, runner=_fake_runner(_verdict("pass")))
        assert result.thread_id == "th_abc"
        assert result.usage == {"input_tokens": 11638, "output_tokens": 5}


# --- every not_run route, each with its specific reason --------------------


class TestNotRunRoutes:
    def test_missing_binary(self):
        def runner(command, **kwargs):
            raise FileNotFoundError("codex")

        result = run_critic(_run_prompt(), model=MODEL, runner=runner)
        assert result.verdict == "not_run"
        assert "not found" in result.reason

    def test_nonzero_exit(self):
        runner = _fake_runner(_verdict("pass"), returncode=1, write=False)
        result = run_critic(_run_prompt(), model=MODEL, runner=runner)
        assert result.verdict == "not_run"
        assert "exited 1" in result.reason

    def test_timeout(self):
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(cmd="codex", timeout=1)

        result = run_critic(_run_prompt(), model=MODEL, timeout=1, runner=runner)
        assert result.verdict == "not_run"
        assert "timed out" in result.reason

    def test_no_output_file_written(self):
        result = run_critic(_run_prompt(), model=MODEL, runner=_fake_runner("", write=False))
        assert result.verdict == "not_run"
        assert "no final-message file" in result.reason

    def test_empty_output_file(self):
        result = run_critic(_run_prompt(), model=MODEL, runner=_fake_runner("   \n"))
        assert result.verdict == "not_run"
        assert "empty" in result.reason

    def test_non_json_final_message(self):
        result = run_critic(_run_prompt(), model=MODEL, runner=_fake_runner("LGTM, ship it."))
        assert result.verdict == "not_run"
        assert "unparseable" in result.reason

    def test_bad_verdict_value(self):
        runner = _fake_runner(json.dumps({"verdict": "looks_fine", "blocking_findings": [], "advisory_findings": []}))
        result = run_critic(_run_prompt(), model=MODEL, runner=runner)
        assert result.verdict == "not_run"
        assert "invalid verdict" in result.reason

    def test_critic_may_not_emit_not_run_itself(self):
        """not_run is the ORCHESTRATOR's verdict; a critic claiming it is
        malformed (it would be asserting its own unavailability)."""
        runner = _fake_runner(_verdict("not_run"))
        result = run_critic(_run_prompt(), model=MODEL, runner=runner)
        assert result.verdict == "not_run"
        assert "invalid verdict" in result.reason

    def test_malformed_findings_structure(self):
        runner = _fake_runner(json.dumps({"verdict": "pass", "blocking_findings": "nope", "advisory_findings": []}))
        result = run_critic(_run_prompt(), model=MODEL, runner=runner)
        assert result.verdict == "not_run"
        assert "must be arrays" in result.reason

    def test_a_finding_that_is_not_an_object(self):
        runner = _fake_runner(json.dumps({"verdict": "blocked", "blocking_findings": ["oops"], "advisory_findings": []}))
        result = run_critic(_run_prompt(), model=MODEL, runner=runner)
        assert result.verdict == "not_run"
        assert "not an object" in result.reason

    def test_output_not_an_object(self):
        result = run_critic(_run_prompt(), model=MODEL, runner=_fake_runner(json.dumps(["a", "b"])))
        assert result.verdict == "not_run"
        assert "not a JSON object" in result.reason


# --- unknown kind demotion -------------------------------------------------


def test_unknown_blocking_kind_is_demoted_to_advisory():
    """An invented kind reported as blocking must never gate — it is demoted to
    advisory with a note, so the reader still sees it but it cannot halt the line."""
    blocking = [{"kind": "vibes_off", "where": "headline", "note": "feels wrong"}]
    runner = _fake_runner(_verdict("blocked", blocking=blocking))
    result = run_critic(_run_prompt(), model=MODEL, runner=runner)
    assert result.blocking_findings == ()
    assert len(result.advisory_findings) == 1
    demoted = result.advisory_findings[0]
    assert demoted["kind"] == "vibes_off"
    assert "unknown kind" in demoted["note"]


# --- the offline guard -----------------------------------------------------


def test_offline_guard_raises_only_on_the_real_runner(monkeypatch):
    """RESEARCHSWARM_OFFLINE + the real subprocess.run must never reach codex —
    it raises loudly. A fake runner is exempt: that is how the whole suite runs."""
    monkeypatch.setenv("RESEARCHSWARM_OFFLINE", "1")
    with pytest.raises(CriticOfflineViolation):
        run_critic(_run_prompt(), model=MODEL, runner=subprocess.run)
    # The fake runner still works with the env set.
    result = run_critic(_run_prompt(), model=MODEL, runner=_fake_runner(_verdict("pass")))
    assert result.verdict == "pass"


# --- command construction --------------------------------------------------


class TestCommandConstruction:
    def _command(self):
        runner = _fake_runner(_verdict("pass"))
        run_critic(_run_prompt(), model=MODEL, runner=runner,
                   schema_file=Path("/tmp/schema.json"))
        return runner.calls[0]

    def test_argv_is_a_portable_list_no_shell(self):
        command = self._command().command
        assert isinstance(command, list)
        assert command[:3] == ["codex", "exec", "--json"]
        # No shell wrapper, no bash-ism — this is what "natively on Windows" means.
        assert not any(tok in ("bash", "sh", "-c") for tok in command)

    def test_prompt_rides_on_stdin_not_argv(self):
        call = self._command()
        assert call.command[-1] == "-"  # prompt from stdin
        assert call.kwargs["input"] == _run_prompt()
        assert _run_prompt() not in call.command

    def test_model_and_output_and_schema_flags(self):
        command = self._command().command
        assert command[command.index("-m") + 1] == MODEL
        assert command[command.index("-o") + 1].endswith("last-message.txt")
        assert command[command.index("--output-schema") + 1] == str(Path("/tmp/schema.json"))

    def test_read_only_sandbox_and_no_web_flags(self):
        command = self._command().command
        assert command[command.index("--sandbox") + 1] == "read-only"
        # No flag that could grant the network or a writable sandbox.
        assert "--full-auto" not in command
        assert "danger-full-access" not in command
        assert not any("write" in tok for tok in command)

    def test_schema_is_optional(self):
        runner = _fake_runner(_verdict("pass"))
        run_critic(_run_prompt(), model=MODEL, runner=runner)  # no schema_file
        assert "--output-schema" not in runner.calls[0].command

    def test_build_codex_command_is_pure(self):
        command = build_codex_command(MODEL, last_message_file="/tmp/m.txt")
        assert command[-1] == "-"
        assert "--ephemeral" in command
        assert "--skip-git-repo-check" in command


# --- the receipt rule ------------------------------------------------------


def _issue(coverage=("2026-07-13", "2026-07-16"), extra_urls=()):
    """A minimal issue carrying a coverage window and whatever urls are already
    cited (so 'cited nowhere in the issue' can be exercised)."""
    return {
        "issue": {"coverage_window": {"from": coverage[0], "to": coverage[1]}},
        "watchlist": [
            {"entity_id": e, "sources": [{"url": u}]} for e, u in extra_urls
        ],
    }


def _dropped(url="https://endpoints.com/merck-verastem", tier="primary",
             published_at="2026-07-15"):
    return {
        "kind": "dropped_story",
        "where": "watchlist.merck",
        "note": "Merck/Verastem never covered",
        "source": {"url": url, "publisher": "Endpoints News", "tier": tier,
                   "published_at": published_at},
    }


CORPUS = json.dumps({"ma_dealmaking": {"findings": [
    {"summary": "Merck to buy Verastem",
     "sources": [{"url": "https://endpoints.com/merck-verastem", "tier": "primary"}]}
]}})


class TestReceiptRule:
    def test_well_formed_receipt_blocks(self):
        kept, downgraded = enforce_receipt_rule(
            [_dropped()], findings_corpus=CORPUS, issue=_issue()
        )
        assert len(kept) == 1
        assert downgraded == ()

    def test_missing_source_downgrades(self):
        finding = {"kind": "dropped_story", "where": "watchlist.merck", "note": "x"}
        kept, downgraded = enforce_receipt_rule([finding], findings_corpus=CORPUS, issue=_issue())
        assert kept == ()
        assert "no source" in downgraded[0]["note"]

    def test_incomplete_source_downgrades(self):
        finding = _dropped()
        del finding["source"]["publisher"]
        kept, downgraded = enforce_receipt_rule([finding], findings_corpus=CORPUS, issue=_issue())
        assert kept == ()
        assert "missing publisher" in downgraded[0]["note"]

    def test_url_absent_from_corpus_downgrades(self):
        finding = _dropped(url="https://elsewhere.com/scoop")
        kept, downgraded = enforce_receipt_rule([finding], findings_corpus=CORPUS, issue=_issue())
        assert kept == ()
        assert "does not appear in the raw findings corpus" in downgraded[0]["note"]

    def test_aggregator_tier_downgrades(self):
        finding = _dropped(tier="aggregator")
        kept, downgraded = enforce_receipt_rule([finding], findings_corpus=CORPUS, issue=_issue())
        assert kept == ()
        assert "not primary or trade" in downgraded[0]["note"]

    def test_out_of_window_date_downgrades(self):
        finding = _dropped(published_at="2026-03-12")  # before the window
        kept, downgraded = enforce_receipt_rule([finding], findings_corpus=CORPUS, issue=_issue())
        assert kept == ()
        assert "outside the coverage window" in downgraded[0]["note"]

    def test_url_already_cited_in_issue_downgrades(self):
        # The same url appears in the issue's watchlist — so it was NOT dropped.
        issue = _issue(extra_urls=[("merck", "https://endpoints.com/merck-verastem")])
        kept, downgraded = enforce_receipt_rule([_dropped()], findings_corpus=CORPUS, issue=issue)
        assert kept == ()
        assert "already cited in the issue" in downgraded[0]["note"]

    def test_receipt_url_that_is_a_prefix_of_a_cited_url_still_blocks(self):
        """The cited-nowhere check matches urls EXACTLY, not by substring. A
        receipt for '…/merck' must not be swallowed by a cited '…/merck-verastem'
        — that is a different story, and the drop is real, so it blocks through."""
        receipt_url = "https://endpoints.com/merck"
        cited_superstring = "https://endpoints.com/merck-verastem"
        corpus = json.dumps({"ma": {"findings": [{"sources": [{"url": receipt_url}]}]}})
        issue = _issue(extra_urls=[("verastem", cited_superstring)])
        kept, downgraded = enforce_receipt_rule(
            [_dropped(url=receipt_url)], findings_corpus=corpus, issue=issue
        )
        assert len(kept) == 1  # the prefix is NOT the cited url
        assert downgraded == ()

    def test_non_dropped_story_kinds_pass_through_untouched(self):
        overclaim = {"kind": "overclaim", "where": "headline", "note": "too strong"}
        kept, downgraded = enforce_receipt_rule([overclaim], findings_corpus=CORPUS, issue=_issue())
        assert kept == (overclaim,)
        assert downgraded == ()

    def test_mixed_findings_partition_correctly(self):
        overclaim = {"kind": "overclaim", "where": "headline", "note": "too strong"}
        good = _dropped()
        bad = _dropped(url="https://nope.com/x")
        kept, downgraded = enforce_receipt_rule(
            [overclaim, good, bad], findings_corpus=CORPUS, issue=_issue()
        )
        assert overclaim in kept and good in kept
        assert len(kept) == 2 and len(downgraded) == 1


def test_the_six_blocking_kinds_are_registered():
    """The acceptance criterion, asserted directly: all six blocking kinds exist,
    and only those six gate."""
    assert BLOCKING_KINDS == {
        "provenance_stale", "overclaim", "aggregator_only",
        "unconfirmed_as_fact", "dropped_story", "thesis_impact_false",
    }


def test_the_twelve_advisory_kinds_are_registered():
    """The advisory twin: all twelve spec/06 advisory kinds exist, and none is a
    blocking kind (advisories never gate)."""
    assert ADVISORY_KINDS == {
        "thin_sourcing", "coverage_gap", "weak_angle", "thesis_unseeded",
        "paywalled_primary", "unverifiable_claim", "stale_open_thread",
        "source_unreachable", "calendar_stale", "thread_dropped",
        "continuity_break", "continuity_baseline_expired",
    }
    assert not (ADVISORY_KINDS & BLOCKING_KINDS)
