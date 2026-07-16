"""Stage 5 wiring: verdict → run.status, the receipt rule applied, not_run banner.

Drives the real run_critique_stage (prompt render + critic call + receipt rule +
status map) with an injected fake codex runner and findings on disk. The verdict
→ published run.status mapping is also asserted end to end through main() in
tests/test_run_cli.py.
"""

import json
from types import SimpleNamespace

import pytest

from researchswarm.critique import (
    PUBLISHED,
    PUBLISHED_UNCRITIQUED,
    PUBLISHED_WITH_UNRESOLVED,
    run_critique_stage,
)
from researchswarm.prompts import load_template
from researchswarm.state import load_state

RUN_ID = "run_20260716_0700"
MODEL = "gpt-5.6-codex"
DROPPED_URL = "https://endpoints.com/merck-verastem"


@pytest.fixture(autouse=True)
def _offline_off(monkeypatch):
    monkeypatch.delenv("RESEARCHSWARM_OFFLINE", raising=False)


@pytest.fixture
def critic_template(repo_root):
    return load_template(repo_root / "prompts" / "critic.md")


@pytest.fixture
def state(repo_root):
    return load_state(repo_root / "state")


def _seed_findings(root, *, url=DROPPED_URL):
    findings_dir = root / "runs" / RUN_ID / "findings"
    findings_dir.mkdir(parents=True)
    (findings_dir / "ma_dealmaking.json").write_text(json.dumps({
        "beat": "ma_dealmaking",
        "findings": [{"summary": "Merck to buy Verastem",
                      "sources": [{"url": url, "tier": "primary"}]}],
    }))
    return ["ma_dealmaking"]


def _draft():
    return {
        "schema_version": "1.0.0",
        "issue": {"id": "2026-07-16",
                  "coverage_window": {"from": "2026-07-13", "to": "2026-07-16"},
                  "run": {"run_id": RUN_ID}},
        "headline": {"title": "t", "so_what": "matters"},
        "watchlist": [],
        "critic_report": {"validator_report": {"passed": True, "retries_used": 0, "findings": []}},
    }


def _codex_runner(verdict_payload, *, write=True):
    def runner(command, **kwargs):
        if write:
            out = command[command.index("-o") + 1]
            with open(out, "w") as fh:
                fh.write(verdict_payload if isinstance(verdict_payload, str) else json.dumps(verdict_payload))
        return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed","usage":{}}', stderr="")
    return runner


def _stage(root, state, critic_template, runner):
    return run_critique_stage(
        root, draft=_draft(), state=state, run_id=RUN_ID, beats_run=["ma_dealmaking"],
        issues_dir=root / "issues", critic_template=critic_template, model=MODEL, runner=runner,
    )


class TestVerdictToStatus:
    def test_pass_publishes(self, tmp_path, state, critic_template):
        _seed_findings(tmp_path)
        runner = _codex_runner({"verdict": "pass", "blocking_findings": [], "advisory_findings": []})
        result = _stage(tmp_path, state, critic_template, runner)
        assert result.status == PUBLISHED
        assert result.verdict == "pass"

    def test_pass_with_advisories_publishes(self, tmp_path, state, critic_template):
        _seed_findings(tmp_path)
        runner = _codex_runner({"verdict": "pass_with_advisories", "blocking_findings": [],
                                "advisory_findings": [{"kind": "thin_sourcing", "where": "x", "note": "n"}]})
        result = _stage(tmp_path, state, critic_template, runner)
        assert result.status == PUBLISHED
        assert result.verdict == "pass_with_advisories"
        assert len(result.advisory_findings) == 1

    def test_not_run_publishes_uncritiqued_with_reason(self, tmp_path, state, critic_template):
        _seed_findings(tmp_path)
        result = _stage(tmp_path, state, critic_template, _codex_runner("not json"))
        assert result.status == PUBLISHED_UNCRITIQUED
        assert result.verdict == "not_run"
        assert "unparseable" in result.reason


class TestReceiptRuleInTheStage:
    def test_blocked_with_valid_receipt_holds(self, tmp_path, state, critic_template):
        _seed_findings(tmp_path)  # corpus carries DROPPED_URL
        blocking = [{"kind": "dropped_story", "where": "watchlist.merck", "note": "cut",
                     "source": {"url": DROPPED_URL, "publisher": "Endpoints",
                                "tier": "primary", "published_at": "2026-07-15"}}]
        runner = _codex_runner({"verdict": "blocked", "blocking_findings": blocking, "advisory_findings": []})
        result = _stage(tmp_path, state, critic_template, runner)
        assert result.status == PUBLISHED_WITH_UNRESOLVED
        assert result.verdict == "blocked"
        assert len(result.blocking_findings) == 1

    def test_blocked_with_bad_receipt_evaporates_to_published(self, tmp_path, state, critic_template):
        _seed_findings(tmp_path, url="https://different.com/x")  # url NOT in corpus
        blocking = [{"kind": "dropped_story", "where": "watchlist.merck", "note": "cut",
                     "source": {"url": DROPPED_URL, "publisher": "Endpoints",
                                "tier": "primary", "published_at": "2026-07-15"}}]
        runner = _codex_runner({"verdict": "blocked", "blocking_findings": blocking, "advisory_findings": []})
        result = _stage(tmp_path, state, critic_template, runner)
        # No blocking finding survives → the run is not blocked; it publishes clean.
        assert result.status == PUBLISHED
        assert result.verdict == "pass_with_advisories"
        assert result.blocking_findings == ()
        assert any("receipt downgrade" in f["note"] for f in result.advisory_findings)


def test_the_prompt_carries_all_five_inputs(tmp_path, state, critic_template, monkeypatch):
    """The load-bearing decision: the critic sees five inputs, not just the digest.
    Capture the rendered prompt at the boundary and assert each input reached it."""
    _seed_findings(tmp_path)
    (tmp_path / "issues").mkdir()

    captured = {}

    def runner(command, **kwargs):
        captured["prompt"] = kwargs["input"]
        out = command[command.index("-o") + 1]
        with open(out, "w") as fh:
            fh.write(json.dumps({"verdict": "pass", "blocking_findings": [], "advisory_findings": []}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    _stage(tmp_path, state, critic_template, runner)
    prompt = captured["prompt"]
    assert "Merck to buy Verastem" in prompt          # raw findings corpus
    assert "(no previous issue)" in prompt            # previous issue (run #1)
    assert state.thesis["beliefs"][0]["id"] in prompt  # thesis
    assert state.watchlist["entities"][0]["entity_id"] in prompt  # watchlist
    assert '"so_what": "matters"' in prompt           # the issue under judgment
