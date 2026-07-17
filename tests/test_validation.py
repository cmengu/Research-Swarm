"""Stage 4's retry loop, and the retry prompt it hands the manager.

The validator is free and deterministic; the loop around it is what costs a
manager call. So these tests drive the loop with a FAKE manager runner — a clean
pass consumes no retry, a fixable block consumes one, and an unfixable block
exhausts the budget and signals a stub. Everything is offline.
"""

import json

import pytest

from researchswarm.prompts import load_template, render_manager_retry_prompt
from researchswarm.state import State
from researchswarm.validation import (
    MAX_VALIDATION_RETRIES,
    ValidationExhausted,
    run_validation_stage,
)
from researchswarm.validator import Finding

RUN_ID = "run_20260716_0700"
THESIS_VERSION = 2


def _state(*entity_ids):
    return State(
        watchlist={"entities": [{"entity_id": e} for e in entity_ids]},
        thesis={"version": THESIS_VERSION, "beliefs": [{"id": "s", "stance": "x"}]},
        catalyst_queue={},
    )


def _source():
    return {"url": "https://x", "publisher": "Endpoints", "tier": "trade", "published_at": "2026-07-15"}


def _draft(*, valid=True):
    """A draft that passes the validator when `valid`, and trips empty_section
    (empty tldr_bullets) when not."""
    return {
        "schema_version": "1.0.0",
        "issue": {"id": "2026-07-16", "run": {"run_id": RUN_ID, "thesis_version": THESIS_VERSION}},
        "headline": {"title": "t", "summary": "s", "so_what": "w",
                     "entity_refs": ["merck"], "sources": [_source()]},
        "stats": {},
        "tldr_bullets": [{"text": "b", "entity_refs": ["merck"], "priority": "high"}] if valid else [],
        "catalyst_queue": {"snapshot_of": "s", "recut_at": None, "items": []},
        "watchlist": [{"entity_id": "merck", "name": "Merck", "summary": "x", "sources": [_source()]}],
        "quiet_this_cycle": {"no_news": [{"entity_id": "pfizer", "cycles_quiet": 1}],
                             "critic_catches": [], "open_threads": []},
        "new_on_radar": [], "themes_and_signals": [], "elsewhere_on_frontier": [],
        "thesis_updates": [], "critic_report": {},
        "sources_and_method": {"beats_run": ["ma_dealmaking"], "beats_failed": [],
                               "source_tier_counts": {}, "paywalled_flagged": []},
    }


def _manager_runner(*drafts, prompts=None):
    """A fake subprocess.run for the retry manager: returns each queued draft in
    an envelope, recording the prompt it was handed."""
    queue = list(drafts)

    def runner(command, **kwargs):
        if prompts is not None:
            prompts.append(command[command.index("-p") + 1])
        envelope = json.dumps(
            {"is_error": False, "result": json.dumps(queue.pop(0)),
             "total_cost_usd": 0.1, "num_turns": 3}
        )
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout=envelope, stderr="")

    return runner


@pytest.fixture
def retry_template(repo_root):
    return load_template(repo_root / "prompts" / "manager-retry.md")


def _stage(tmp_path, draft, *, runner, retry_template, beats_failed=None):
    draft_path = tmp_path / "issue-draft.json"
    draft_path.write_text(json.dumps(draft))
    return run_validation_stage(
        draft=draft,
        draft_path=draft_path,
        state=_state("merck", "pfizer"),
        issues_dir=tmp_path / "issues",
        beats_failed=beats_failed or [],
        retry_template=retry_template,
        model="claude-opus-4-8",
        run_id=RUN_ID,
        thesis_version=THESIS_VERSION,
        runner=runner,
    )


class TestTheLoop:
    def test_a_clean_draft_passes_first_time_with_no_retry(self, tmp_path, retry_template):
        # The runner should never be called on a clean pass — make it explode.
        def never(*a, **k):
            raise AssertionError("the manager must not be called on a clean pass")

        result = _stage(tmp_path, _draft(valid=True), runner=never, retry_template=retry_template)
        assert result.retries_used == 0

    def test_the_validator_report_is_stamped_on_the_passing_draft(self, tmp_path, retry_template):
        result = _stage(tmp_path, _draft(valid=True), runner=None, retry_template=retry_template)
        report = result.draft["critic_report"]["validator_report"]
        assert report["passed"] is True
        assert report["retries_used"] == 0

    def test_the_stamped_draft_is_repersisted(self, tmp_path, retry_template):
        _stage(tmp_path, _draft(valid=True), runner=None, retry_template=retry_template)
        on_disk = json.loads((tmp_path / "issue-draft.json").read_text())
        assert on_disk["critic_report"]["validator_report"]["passed"] is True

    def test_a_block_then_a_fixed_draft_on_retry_one(self, tmp_path, retry_template):
        prompts = []
        runner = _manager_runner(_draft(valid=True), prompts=prompts)
        result = _stage(tmp_path, _draft(valid=False), runner=runner, retry_template=retry_template)
        assert result.retries_used == 1
        assert len(prompts) == 1  # exactly one manager call
        # The earlier round's block is recorded in the published report.
        kinds = {f["kind"] for f in result.draft["critic_report"]["validator_report"]["findings"]}
        assert "empty_section" in kinds

    def test_two_unfixable_blocks_exhaust_the_budget_and_signal_a_stub(self, tmp_path, retry_template):
        # The manager keeps returning an invalid draft — the loop gives up.
        runner = _manager_runner(_draft(valid=False), _draft(valid=False))
        with pytest.raises(ValidationExhausted) as exc:
            _stage(tmp_path, _draft(valid=False), runner=runner, retry_template=retry_template)
        assert exc.value.blocking  # carries the surviving findings for the stub detail

    def test_the_budget_is_exactly_two_retries(self, tmp_path, retry_template):
        prompts = []
        runner = _manager_runner(_draft(valid=False), _draft(valid=False), prompts=prompts)
        with pytest.raises(ValidationExhausted):
            _stage(tmp_path, _draft(valid=False), runner=runner, retry_template=retry_template)
        assert len(prompts) == MAX_VALIDATION_RETRIES  # two calls, never three


class TestTheContinuityWalk:
    def _publish(self, issues_dir, issue_id, status, items):
        issues_dir.mkdir(parents=True, exist_ok=True)
        (issues_dir / f"{issue_id}.json").write_text(json.dumps({
            "issue": {"id": issue_id, "run": {"status": status}},
            "catalyst_queue": {"items": items} if items is not None else {},
        }))

    def _queue_item(self, **overrides):
        item = {"id": "q1", "entity_ids": [], "first_expected_window": "2026-Q2",
                "expected_window": "2026-Q2", "window_source": _source(),
                "status": "pending", "slip_log": [], "sources": [_source()]}
        item.update(overrides)
        return item

    def test_the_walk_binds_past_a_stub_to_the_snapshot_carrying_issue(self, tmp_path, retry_template):
        """Baseline, then a stub, then this draft: the tamper check must compare
        against the baseline the stub is transparent to, not the stub."""
        issues = tmp_path / "issues"
        self._publish(issues, "2026-07-09", "published", [self._queue_item()])
        self._publish(issues, "2026-07-13", "failed", None)  # a stub carries no snapshot

        draft = _draft(valid=True)
        # Tamper: first_expected_window changed vs the 07-09 baseline.
        draft["catalyst_queue"]["items"] = [self._queue_item(first_expected_window="2026-Q3")]

        with pytest.raises(ValidationExhausted) as exc:
            _stage(
                tmp_path, draft,
                runner=_manager_runner(draft, draft),  # manager cannot fix it
                retry_template=retry_template,
            )
        assert any(f.kind == "queue_tamper" for f in exc.value.blocking)

    def test_the_floor_files_a_continuity_baseline_expired_advisory(self, tmp_path, retry_template):
        issues = tmp_path / "issues"
        # 13 stubs and no snapshot within the floor → the search expires.
        for d in range(1, 14):
            self._publish(issues, f"2026-06-{d:02d}", "failed", None)
        result = _stage(tmp_path, _draft(valid=True), runner=None, retry_template=retry_template)
        kinds = {f["kind"] for f in result.draft["critic_report"]["validator_report"]["findings"]}
        assert "continuity_baseline_expired" in kinds

    def test_run_one_empty_walk_is_silent(self, tmp_path, retry_template):
        result = _stage(tmp_path, _draft(valid=True), runner=None, retry_template=retry_template)
        assert result.advisory == ()


class TestTheRetryPrompt:
    def test_it_carries_the_prior_draft_and_the_findings(self, retry_template):
        draft = _draft(valid=False)
        findings = (Finding("empty_section", "tldr_bullets", "required section is empty"),)
        prompt = render_manager_retry_prompt(
            retry_template, prior_draft=draft, blocking_findings=findings
        )
        assert json.dumps(draft, indent=2, ensure_ascii=False) in prompt
        assert "empty_section at tldr_bullets" in prompt

    def test_it_states_the_edit_not_regenerate_rule(self, retry_template):
        """The load-bearing instruction: sections that passed must not mutate."""
        prompt = render_manager_retry_prompt(
            retry_template, prior_draft=_draft(), blocking_findings=()
        )
        assert "EDIT the draft, do NOT regenerate it" in prompt
        assert "Add NO new facts" in prompt

    def test_it_restates_the_one_json_object_output_contract(self, retry_template):
        prompt = render_manager_retry_prompt(
            retry_template, prior_draft=_draft(), blocking_findings=()
        )
        assert "EXACTLY ONE JSON object" in prompt
        assert "no markdown fences" in prompt

    def test_advisories_never_enter_the_retry_payload(self):
        """The template document says advisories are withheld — they are the
        record, not a to-do list. The renderer only ever receives blocking."""
        # render_manager_retry_prompt takes only blocking_findings; there is no
        # channel for advisories to leak in. Guard the doc-stated contract.
        from researchswarm.prompts import render_manager_retry_prompt as fn
        import inspect
        assert "advisory" not in inspect.signature(fn).parameters
