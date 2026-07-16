"""Invoking the manager: no tools, the transport, the seam, the retry.

The subprocess is injected so these run offline and deterministically. One live
end-to-end run is a separate, opt-in test — see tests/test_live_manager.py.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from researchswarm.manager import (
    DISALLOWED_TOOLS,
    IssueDraftInvalid,
    ManagerFailed,
    build_manager_command,
    load_models,
    run_manager,
    validate_issue_draft,
)

RUN_ID = "run_20260717_0045"
THESIS_VERSION = 2


def _valid_draft(**overrides):
    draft = {
        "schema_version": "1.0.0",
        "issue": {
            "id": "2026-07-17",
            "published_at": "2026-07-17T00:45:00+08:00",
            "coverage_window": {"from": "2026-07-13", "to": "2026-07-17"},
            "run": {
                "run_id": RUN_ID,
                "status": "published_uncritiqued",
                "critic_verdict": "not_run",
                "critic_retries": 0,
                "thesis_version": THESIS_VERSION,
                "models": {"researchers": "sonnet", "manager": "claude-opus-4-8", "critic": None},
            },
        },
        "headline": {"title": "t", "summary": "s", "so_what": "why it matters today",
                     "entity_refs": ["merck"], "confidence": "high", "sources": []},
        "stats": {},
        "tldr_bullets": [],
        "catalyst_queue": {"snapshot_of": "state/catalyst-queue.json", "recut_at": None, "items": []},
        "watchlist": [],
        "quiet_this_cycle": {"no_news": [], "critic_catches": [], "open_threads": []},
        "new_on_radar": [],
        "themes_and_signals": [],
        "elsewhere_on_frontier": [],
        "thesis_updates": [],
        "critic_report": {"verdict": "not_run", "retries_used": 0, "blocking_findings": [],
                          "advisory_findings": [], "validator_report": None},
        "sources_and_method": {"beats_run": ["ma_dealmaking"], "beats_failed": [],
                               "source_tier_counts": {}, "paywalled_flagged": []},
    }
    draft.update(overrides)
    return draft


def _envelope(result_text, is_error=False):
    return json.dumps(
        {"is_error": is_error, "result": result_text, "total_cost_usd": 0.42, "num_turns": 7}
    )


def _runner_returning(*stdouts, returncode=0):
    calls = []
    queue = list(stdouts)

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=returncode, stdout=queue.pop(0), stderr="")

    runner.calls = calls
    return runner


def _run(runner):
    return run_manager(
        "PROMPT", model="claude-opus-4-8",
        thesis_version=THESIS_VERSION, run_id=RUN_ID, runner=runner,
    )


class TestLoadModels:
    def test_reads_the_manager_id_from_the_repo(self, repo_root):
        models = load_models(repo_root / "config" / "models.toml")
        assert models["manager"] == "claude-opus-4-8"

    def test_missing_file_fails_loudly(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="models config not found"):
            load_models(tmp_path / "models.toml")

    def test_missing_manager_id_fails_loudly(self, tmp_path):
        """A missing manager id must fail here, not surface as an empty --model
        flag the CLI rejects three stages later."""
        path = tmp_path / "models.toml"
        path.write_text("[models]\ncritic = \"gpt-5.6-codex\"\n")
        with pytest.raises(ValueError, match="manager is required"):
            load_models(path)


class TestTheNoToolWall:
    """The manager gets NO tools — no web (it adds no facts) and no write
    (stdout is the transport). Enforced by flags, guarded here against regress."""

    def test_grants_nothing(self):
        command = build_manager_command("prompt", "claude-opus-4-8")
        allowed = command[command.index("--allowedTools") + 1 : command.index("--disallowedTools")]
        assert allowed == [""]  # an explicitly empty allow list

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Task"])
    def test_every_writing_and_delegation_tool_is_denied(self, tool):
        assert tool in DISALLOWED_TOOLS
        assert tool in build_manager_command("prompt", "claude-opus-4-8")

    def test_the_web_is_denied_here_though_the_researcher_gets_it(self):
        """No new facts: the manager works from the findings corpus, not the web."""
        assert "WebSearch" in DISALLOWED_TOOLS
        assert "WebFetch" in DISALLOWED_TOOLS

    def test_never_skips_permissions(self):
        command = " ".join(build_manager_command("prompt", "claude-opus-4-8"))
        assert "dangerously-skip-permissions" not in command
        assert "bypassPermissions" not in command

    def test_model_comes_from_the_argument(self):
        command = build_manager_command("prompt", "claude-opus-4-8")
        assert command[command.index("--model") + 1] == "claude-opus-4-8"


class TestTransport:
    def test_reads_the_draft_from_the_stdout_envelope(self):
        result = _run(_runner_returning(_envelope(json.dumps(_valid_draft()))))
        assert result.draft["schema_version"] == "1.0.0"
        assert result.attempts == 1
        assert result.cost_usd == 0.42
        assert result.num_turns == 7

    def test_tolerates_a_fenced_json_block(self):
        fenced = "```json\n" + json.dumps(_valid_draft()) + "\n```"
        result = _run(_runner_returning(_envelope(fenced)))
        assert result.attempts == 1

    def test_an_error_envelope_fails(self):
        runner = _runner_returning(
            _envelope("rate limited", is_error=True),
            _envelope("rate limited", is_error=True),
        )
        with pytest.raises(ManagerFailed):
            _run(runner)


class TestTheSeam:
    def test_authored_stats_is_caught(self):
        """A non-empty stats is a contract breach — the bar cannot lie. It must
        be caught at the seam, before critic budget is spent."""
        runner = _runner_returning(
            _envelope(json.dumps(_valid_draft(stats={"tracked_updates": 9}))),
            _envelope(json.dumps(_valid_draft(stats={"tracked_updates": 9}))),
        )
        with pytest.raises(ManagerFailed, match="after 2 attempts"):
            _run(runner)
        with pytest.raises(IssueDraftInvalid, match="stats must be exactly"):
            validate_issue_draft(
                _valid_draft(stats={"x": 1}), thesis_version=THESIS_VERSION, run_id=RUN_ID
            )

    def test_missing_top_level_key_is_caught(self):
        broken = _valid_draft()
        del broken["catalyst_queue"]
        with pytest.raises(IssueDraftInvalid, match="catalyst_queue"):
            validate_issue_draft(broken, thesis_version=THESIS_VERSION, run_id=RUN_ID)

    def test_empty_so_what_is_caught(self):
        broken = _valid_draft()
        broken["headline"]["so_what"] = ""
        with pytest.raises(IssueDraftInvalid, match="so_what"):
            validate_issue_draft(broken, thesis_version=THESIS_VERSION, run_id=RUN_ID)

    def test_run_block_must_echo_the_identifiers(self):
        broken = _valid_draft()
        broken["issue"]["run"]["run_id"] = "run_wrong"
        with pytest.raises(IssueDraftInvalid, match="run_id"):
            validate_issue_draft(broken, thesis_version=THESIS_VERSION, run_id=RUN_ID)

    def test_collects_every_problem_not_just_the_first(self):
        broken = _valid_draft(schema_version="0.9.0", stats={"x": 1})
        with pytest.raises(IssueDraftInvalid) as exc:
            validate_issue_draft(broken, thesis_version=THESIS_VERSION, run_id=RUN_ID)
        assert "schema_version" in str(exc.value)
        assert "stats" in str(exc.value)


class TestRetry:
    def test_retries_once_with_the_error_appended(self):
        runner = _runner_returning(
            _envelope("not json at all"),
            _envelope(json.dumps(_valid_draft())),
        )
        result = run_manager(
            "ORIGINAL PROMPT", model="claude-opus-4-8",
            thesis_version=THESIS_VERSION, run_id=RUN_ID, runner=runner,
        )
        assert result.attempts == 2
        retry_prompt = runner.calls[1][2]
        assert "failed validation" in retry_prompt
        assert "ORIGINAL PROMPT" in retry_prompt

    def test_gives_up_after_two_attempts(self):
        runner = _runner_returning(_envelope("garbage"), _envelope("still garbage"))
        with pytest.raises(ManagerFailed, match="after 2 attempts"):
            _run(runner)
        assert len(runner.calls) == 2  # exactly two, never three


class TestProcessFailures:
    def test_a_nonzero_exit_carries_the_stdout_snippet(self):
        """claude -p reports errors on stdout, not stderr — a blank exit-1 error
        would tell the operator nothing."""
        runner = _runner_returning(
            '{"is_error": true, "result": "credit balance too low"}', returncode=1
        )
        with pytest.raises(ManagerFailed, match="credit balance too low"):
            _run(runner)

    def test_a_timeout_fails(self):
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 900)

        with pytest.raises(ManagerFailed, match="timed out"):
            _run(runner)

    def test_offline_guard_refuses_a_real_subprocess(self, monkeypatch):
        """The same guard the researcher has: a test reaching a real model by
        accident burns money and hangs CI. Refuse in milliseconds instead."""
        monkeypatch.setenv("RESEARCHSWARM_OFFLINE", "1")
        with pytest.raises(ManagerFailed, match="tried to call a real model"):
            run_manager("PROMPT", model="claude-opus-4-8",
                        thesis_version=THESIS_VERSION, run_id=RUN_ID)
