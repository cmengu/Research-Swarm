"""Invoking a researcher: the wall, the transport, the retry.

The subprocess is injected so these run offline and deterministically. One live
end-to-end run is a separate, opt-in test — see tests/test_live_researcher.py.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from researchswarm.beats import Beat
from researchswarm.researcher import (
    ALLOWED_TOOLS,
    DISALLOWED_TOOLS,
    ResearcherFailed,
    build_command,
    run_researcher,
)

BEAT = Beat(
    id="ma_dealmaking", name="Pharma M&A", charter="deals", seed_angles=["a"],
    notes="", model="sonnet", max_turns=30,
)
WINDOW = {"from": "2026-07-13", "to": "2026-07-16"}
RUN_ID = "run_20260716_0700"
KNOWN = frozenset({"merck"})


def _valid_findings(**overrides):
    payload = {
        "beat": "ma_dealmaking",
        "run_id": RUN_ID,
        "coverage_window": dict(WINDOW),
        "quiet": True,
        "findings": [],
        "coverage_notes": {"angles_run": ["deals"], "entities_checked": ["merck"], "notes": "quiet"},
        "errors": [],
    }
    payload.update(overrides)
    return payload


def _envelope(result_text, is_error=False):
    return json.dumps(
        {"is_error": is_error, "result": result_text, "total_cost_usd": 0.01, "num_turns": 3}
    )


def _runner_returning(*stdouts, returncode=0):
    """A fake subprocess.run yielding each stdout in turn; records the calls."""
    calls = []
    queue = list(stdouts)

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=returncode, stdout=queue.pop(0), stderr="")

    runner.calls = calls
    return runner


class TestTheReadOnlyWall:
    """Read-only is enforced by permission flags, not prompt text. A researcher
    cannot write a file even if a prompt injection convinces it to try.

    Verified live: with these flags the model reports no Write/Edit/shell tool
    is available and permission_denials stays EMPTY — the tools are never
    presented. These tests guard that the flags don't quietly regress."""

    def test_only_web_tools_are_allowed(self):
        command = build_command("prompt", BEAT)
        allowed = command[command.index("--allowedTools") + 1 : command.index("--disallowedTools")]
        assert allowed == list(ALLOWED_TOOLS)
        assert set(allowed) == {"WebSearch", "WebFetch"}

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"])
    def test_every_writing_tool_is_denied(self, tool):
        assert tool in DISALLOWED_TOOLS
        assert tool in build_command("prompt", BEAT)

    def test_task_is_denied_so_a_subagent_cannot_route_around_the_wall(self):
        """A live probe spawned a subagent before Task was blocked. A subagent
        inherits its own tool set, so an unblocked Task is a hole in the wall."""
        assert "Task" in DISALLOWED_TOOLS

    def test_never_skips_permissions(self):
        """--dangerously-skip-permissions would delete the wall entirely."""
        command = " ".join(build_command("prompt", BEAT))
        assert "dangerously-skip-permissions" not in command
        assert "bypassPermissions" not in command


class TestTransport:
    def test_reads_findings_from_the_stdout_envelope(self):
        runner = _runner_returning(_envelope(json.dumps(_valid_findings())))
        result = run_researcher(
            BEAT, "prompt", run_id=RUN_ID, window=WINDOW,
            known_entity_ids=KNOWN, runner=runner,
        )
        assert result.findings["beat"] == "ma_dealmaking"
        assert result.attempts == 1
        assert result.cost_usd == 0.01

    def test_tolerates_a_fenced_json_block(self):
        """The prompt forbids fences, but burning a retry — and a fresh set of
        web searches — to re-punctuate good facts is a bad trade."""
        fenced = "```json\n" + json.dumps(_valid_findings()) + "\n```"
        runner = _runner_returning(_envelope(fenced))
        result = run_researcher(
            BEAT, "prompt", run_id=RUN_ID, window=WINDOW,
            known_entity_ids=KNOWN, runner=runner,
        )
        assert result.attempts == 1

    def test_model_comes_from_the_beat(self):
        command = build_command("prompt", BEAT)
        assert command[command.index("--model") + 1] == "sonnet"


class TestRetry:
    def test_retries_once_with_the_error_appended(self):
        runner = _runner_returning(
            _envelope("not json at all"),
            _envelope(json.dumps(_valid_findings())),
        )
        result = run_researcher(
            BEAT, "ORIGINAL PROMPT", run_id=RUN_ID, window=WINDOW,
            known_entity_ids=KNOWN, runner=runner,
        )
        assert result.attempts == 2
        retry_prompt = runner.calls[1][2]
        assert "failed validation" in retry_prompt
        assert "ORIGINAL PROMPT" in retry_prompt  # the original survives the retry

    def test_gives_up_after_two_attempts(self):
        runner = _runner_returning(_envelope("garbage"), _envelope("still garbage"))
        with pytest.raises(ResearcherFailed, match="after 2 attempts"):
            run_researcher(
                BEAT, "prompt", run_id=RUN_ID, window=WINDOW,
                known_entity_ids=KNOWN, runner=runner,
            )
        assert len(runner.calls) == 2  # exactly two, never three

    def test_a_schema_failure_also_retries(self):
        """Not just parse failures — a well-formed object that breaks the
        contract gets the same one retry."""
        bad = _valid_findings(quiet=True, findings=[{"summary": "x"}])
        runner = _runner_returning(
            _envelope(json.dumps(bad)),
            _envelope(json.dumps(_valid_findings())),
        )
        result = run_researcher(
            BEAT, "prompt", run_id=RUN_ID, window=WINDOW,
            known_entity_ids=KNOWN, runner=runner,
        )
        assert result.attempts == 2


class TestBeatDeath:
    def test_a_nonzero_exit_fails_the_beat(self):
        runner = _runner_returning("", returncode=1)
        with pytest.raises(ResearcherFailed, match="exited 1"):
            run_researcher(
                BEAT, "prompt", run_id=RUN_ID, window=WINDOW,
                known_entity_ids=KNOWN, runner=runner,
            )

    def test_a_timeout_fails_the_beat(self):
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 900)

        with pytest.raises(ResearcherFailed, match="timed out"):
            run_researcher(
                BEAT, "prompt", run_id=RUN_ID, window=WINDOW,
                known_entity_ids=KNOWN, runner=runner,
            )

    def test_an_error_envelope_fails_the_beat(self):
        runner = _runner_returning(
            _envelope("rate limited", is_error=True),
            _envelope("rate limited", is_error=True),
        )
        with pytest.raises(ResearcherFailed):
            run_researcher(
                BEAT, "prompt", run_id=RUN_ID, window=WINDOW,
                known_entity_ids=KNOWN, runner=runner,
            )
