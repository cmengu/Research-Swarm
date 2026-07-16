"""The one critic test that calls the real codex binary. Opt-in, triply gated.

    pytest tests/test_live_critic.py --live

It needs three things the rest of the suite fakes: --live (it spends real Codex
quota and takes minutes), the codex binary ACTUALLY on PATH, and a real published
issue plus its findings corpus on disk to judge — you cannot critique an issue that
was never synthesised. Any of the three missing is a clean skip, not a failure.

It reads the newest run that has BOTH a published issue and a findings/ directory,
renders the real rubric, and runs one real Codex pass — asserting only the verdict
CONTRACT shape (a real judgment is non-deterministic; the contract is not).

Written to exercise the live wire format end to end. DO NOT run it in CI.
"""

import json
import shutil

import pytest

from researchswarm.critic import CRITIC_VERDICTS, run_critic
from researchswarm.manager import load_models
from researchswarm.prompts import load_template, render_critic_prompt
from researchswarm.runs import latest_covering_issue
from researchswarm.state import load_state


@pytest.fixture(autouse=True)
def allow_live(request, monkeypatch):
    if not request.config.getoption("--live", default=False):
        pytest.skip("live model test; pass --live to run (spends Codex quota, takes minutes)")
    if shutil.which("codex") is None:
        pytest.skip("codex binary not on PATH — the critic cannot run")
    monkeypatch.delenv("RESEARCHSWARM_OFFLINE", raising=False)


def _newest_judgeable_run(repo_root):
    """The newest run_id whose findings/ exist AND whose issue actually published,
    or None. The critic needs the raw corpus (receipts) and a real issue to judge."""
    runs = repo_root / "runs"
    for findings_dir in sorted(
        (d for d in runs.glob("*/findings") if any(d.glob("*.json"))), reverse=True
    ):
        run_id = findings_dir.parent.name
        by_beat = {p.stem: json.loads(p.read_text()) for p in sorted(findings_dir.glob("*.json"))}
        return run_id, by_beat
    return None


@pytest.mark.live
def test_a_real_critic_returns_a_contract_shaped_verdict(repo_root):
    judgeable = _newest_judgeable_run(repo_root)
    if judgeable is None:
        pytest.skip("no runs/<run_id>/findings/ on disk — run a research fan-out first")
    _, findings_by_beat = judgeable

    published = latest_covering_issue(repo_root / "issues").payload
    if published is None:
        pytest.skip("no published issue on disk — publish one before critiquing it")

    state = load_state(repo_root / "state")
    template = load_template(repo_root / "prompts" / "critic.md")
    models = load_models(repo_root / "config" / "models.toml")

    prompt = render_critic_prompt(
        template,
        issue=published,
        findings_by_beat=findings_by_beat,
        previous_issue=None,
        watchlist=state.watchlist,
        thesis=state.thesis,
    )

    result = run_critic(
        prompt,
        model=models["critic"],
        timeout=1200,
        schema_file=repo_root / "prompts" / "critic-output-schema.json",
    )

    # A real judgment is non-deterministic, but the contract is not.
    assert result.verdict in (*CRITIC_VERDICTS, "not_run")
    if result.verdict == "not_run":
        pytest.skip(f"critic did not run: {result.reason}")
    for finding in (*result.blocking_findings, *result.advisory_findings):
        assert isinstance(finding, dict)
        assert finding.get("kind") and finding.get("where") is not None
