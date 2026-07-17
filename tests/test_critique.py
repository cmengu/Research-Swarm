"""Stage 5 wiring: the critic pass, the receipt rule, and the retry loop.

Drives the real run_critique_stage — prompt render, critic call, receipt rule,
the retry loop (manager edits, rebuttals, adjudication, exhaustion) — with an
injected fake codex runner AND an injected fake manager runner, findings on disk,
everything offline. The verdict → published run.status mapping is also asserted
end to end through main() in tests/test_run_cli.py.
"""

import json
from types import SimpleNamespace

import pytest

from researchswarm.critique import (
    MAX_CRITIC_RETRIES,
    PUBLISHED,
    PUBLISHED_UNCRITIQUED,
    PUBLISHED_WITH_UNRESOLVED,
    run_critique_stage,
)
from researchswarm.prompts import load_template
from researchswarm.state import load_state

RUN_ID = "run_20260716_0700"
MODEL = "gpt-5-codex"
MANAGER_MODEL = "claude-opus-4-8"
THESIS_VERSION = 2
DROPPED_URL = "https://endpoints.com/merck-verastem"


@pytest.fixture(autouse=True)
def _offline_off(monkeypatch):
    monkeypatch.delenv("RESEARCHSWARM_OFFLINE", raising=False)


@pytest.fixture
def critic_template(repo_root):
    return load_template(repo_root / "prompts" / "critic.md")


@pytest.fixture
def retry_template(repo_root):
    return load_template(repo_root / "prompts" / "critic-retry.md")


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


def _source():
    return {"url": "https://x", "publisher": "Endpoints", "tier": "trade", "published_at": "2026-07-15"}


def _draft(**overrides):
    """A full 14-key draft: valid as the critic's input AND as what the retry
    manager re-emits (it must pass validate_issue_draft)."""
    draft = {
        "schema_version": "1.0.0",
        "issue": {"id": "2026-07-16",
                  "coverage_window": {"from": "2026-07-13", "to": "2026-07-16"},
                  "run": {"run_id": RUN_ID, "thesis_version": THESIS_VERSION}},
        "headline": {"title": "t", "so_what": "matters"},
        "stats": {},
        "tldr_bullets": [],
        "catalyst_queue": {},
        "watchlist": [],
        "quiet_this_cycle": {"no_news": [], "critic_catches": [], "open_threads": []},
        "new_on_radar": [],
        "themes_and_signals": [],
        "elsewhere_on_frontier": [],
        "thesis_updates": [],
        "critic_report": {"validator_report": {"passed": True, "retries_used": 0, "findings": []}},
        "sources_and_method": {"beats_run": ["ma_dealmaking"], "beats_failed": [],
                               "source_tier_counts": {}, "paywalled_flagged": []},
    }
    draft.update(overrides)
    return draft


def _draft_rebutting(kind, where, *, note="cut", text="The source does support it."):
    """What the manager re-emits when it REBUTS rather than fixes: the rebuttal
    rides on the finding inside critic_report.blocking_findings."""
    draft = _draft()
    draft["critic_report"]["blocking_findings"] = [{
        "kind": kind, "where": where, "note": note,
        "rebuttal": {"text": text, "sources": [_source()]},
    }]
    return draft


def _codex_runner(*payloads):
    """Fake codex: writes each queued verdict payload to the -o file in order; a
    single payload repeats (the 'critic never changes its mind' case)."""
    queue = list(payloads)

    def runner(command, **kwargs):
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        out = command[command.index("-o") + 1]
        with open(out, "w") as fh:
            fh.write(payload if isinstance(payload, str) else json.dumps(payload))
        return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed","usage":{}}', stderr="")

    return runner


def _manager_runner(*drafts, calls=None):
    """Fake claude-family manager: returns each queued draft in an envelope; a
    single draft repeats (the manager that keeps handing back a still-blocked
    draft). Records each call when `calls` is given."""
    queue = list(drafts)

    def runner(command, **kwargs):
        if calls is not None:
            calls.append(command)
        draft = queue.pop(0) if len(queue) > 1 else queue[0]
        envelope = json.dumps({"is_error": False, "result": json.dumps(draft),
                               "total_cost_usd": 0.1, "num_turns": 2})
        return SimpleNamespace(returncode=0, stdout=envelope, stderr="")

    return runner


def _no_manager(*a, **k):
    raise AssertionError("the manager must not be called")


def _stage(root, state, critic_template, retry_template, runner, *, manager_runner=_no_manager):
    return run_critique_stage(
        root, draft=_draft(), state=state, run_id=RUN_ID, beats_run=["ma_dealmaking"],
        issues_dir=root / "issues", critic_template=critic_template,
        retry_template=retry_template, model=MODEL, manager_model=MANAGER_MODEL,
        draft_path=root / "runs" / RUN_ID / "issue-draft.json",
        thesis_version=THESIS_VERSION, runner=runner, manager_runner=manager_runner,
    )


class TestVerdictToStatus:
    def test_pass_publishes(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path)
        runner = _codex_runner({"verdict": "pass", "blocking_findings": [], "advisory_findings": []})
        result = _stage(tmp_path, state, critic_template, retry_template, runner)
        assert result.status == PUBLISHED
        assert result.verdict == "pass"
        assert result.retries_used == 0

    def test_pass_with_advisories_publishes(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path)
        runner = _codex_runner({"verdict": "pass_with_advisories", "blocking_findings": [],
                                "advisory_findings": [{"kind": "thin_sourcing", "where": "x", "note": "n"}]})
        result = _stage(tmp_path, state, critic_template, retry_template, runner)
        assert result.status == PUBLISHED
        assert result.verdict == "pass_with_advisories"
        assert len(result.advisory_findings) == 1

    def test_not_run_publishes_uncritiqued_with_reason(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path)
        result = _stage(tmp_path, state, critic_template, retry_template, _codex_runner("not json"))
        assert result.status == PUBLISHED_UNCRITIQUED
        assert result.verdict == "not_run"
        assert "unparseable" in result.reason


class TestReceiptRuleInTheStage:
    def test_blocked_with_bad_receipt_evaporates_to_published(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path, url="https://different.com/x")  # url NOT in corpus
        blocking = [{"kind": "dropped_story", "where": "watchlist.merck", "note": "cut",
                     "source": {"url": DROPPED_URL, "publisher": "Endpoints",
                                "tier": "primary", "published_at": "2026-07-15"}}]
        runner = _codex_runner({"verdict": "blocked", "blocking_findings": blocking, "advisory_findings": []})
        # No blocking finding survives → nothing to retry; it publishes clean.
        result = _stage(tmp_path, state, critic_template, retry_template, runner)
        assert result.status == PUBLISHED
        assert result.verdict == "pass_with_advisories"
        assert result.blocking_findings == ()
        assert result.retries_used == 0
        assert any("receipt downgrade" in f["note"] for f in result.advisory_findings)


def _blocked(kind="overclaim", where="headline", note="too strong"):
    return {"verdict": "blocked",
            "blocking_findings": [{"kind": kind, "where": where, "note": note}],
            "advisory_findings": []}


def _clean():
    return {"verdict": "pass", "blocking_findings": [], "advisory_findings": []}


class TestTheRetryLoop:
    def test_a_clean_first_pass_never_calls_the_manager(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path)
        result = _stage(tmp_path, state, critic_template, retry_template, _codex_runner(_clean()))
        assert result.retries_used == 0  # _no_manager would have raised

    def test_a_fix_on_retry_one_publishes_clean_and_the_edit_flows_out(
        self, tmp_path, state, critic_template, retry_template
    ):
        _seed_findings(tmp_path)
        codex = _codex_runner(_blocked(), _clean())  # blocked, then satisfied
        manager = _manager_runner(_draft(headline={"title": "t", "so_what": "softened"}))
        result = _stage(tmp_path, state, critic_template, retry_template, codex, manager_runner=manager)
        assert result.status == PUBLISHED
        assert result.verdict == "pass"
        assert result.retries_used == 1
        # The draft that comes OUT of the loop is the manager's edited one.
        assert result.draft["headline"]["so_what"] == "softened"

    def test_the_edited_draft_is_repersisted_each_round(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path)
        codex = _codex_runner(_blocked(), _clean())
        manager = _manager_runner(_draft(headline={"title": "t", "so_what": "softened"}))
        _stage(tmp_path, state, critic_template, retry_template, codex, manager_runner=manager)
        on_disk = json.loads((tmp_path / "runs" / RUN_ID / "issue-draft.json").read_text())
        assert on_disk["headline"]["so_what"] == "softened"

    def test_validator_report_survives_a_retry(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path)
        codex = _codex_runner(_blocked(), _clean())
        # The manager re-emits a draft whose critic_report has NO validator_report;
        # the loop must carry stage 4's record across the edit.
        edited = _draft()
        edited["critic_report"] = {}
        result = _stage(tmp_path, state, critic_template, retry_template,
                        codex, manager_runner=_manager_runner(edited))
        assert result.draft["critic_report"]["validator_report"]["passed"] is True

    def test_two_unresolved_blocks_exhaust_the_budget(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path)
        codex = _codex_runner(_blocked())  # blocked on every pass
        calls = []
        manager = _manager_runner(_draft(), calls=calls)  # keeps handing back a still-blocked draft
        result = _stage(tmp_path, state, critic_template, retry_template, codex, manager_runner=manager)
        assert result.status == PUBLISHED_WITH_UNRESOLVED
        assert result.verdict == "blocked"
        assert result.retries_used == MAX_CRITIC_RETRIES
        assert len(calls) == MAX_CRITIC_RETRIES  # two manager calls, never three
        assert result.blocking_findings[0]["kind"] == "overclaim"

    def test_a_withdrawn_rebuttal_publishes_clean_with_both_sides(self, tmp_path, state, critic_template, retry_template):
        _seed_findings(tmp_path)
        # Pass 1 blocks; the manager rebuts; pass 2 the critic drops it → withdrawn.
        codex = _codex_runner(_blocked(), _clean())
        manager = _manager_runner(_draft_rebutting("overclaim", "headline"))
        result = _stage(tmp_path, state, critic_template, retry_template, codex, manager_runner=manager)
        assert result.status == PUBLISHED
        assert result.verdict == "pass"
        assert result.blocking_findings == ()
        assert result.retries_used == 1
        # The dispute does not vanish: it publishes as a non-gating advisory record
        # carrying both sides, adjudication withdrawn.
        withdrawn = [f for f in result.advisory_findings if f.get("rebuttal")]
        assert len(withdrawn) == 1
        assert withdrawn[0]["kind"] == "overclaim"
        assert withdrawn[0]["rebuttal"]["adjudication"] == "withdrawn"
        assert withdrawn[0]["rebuttal"]["text"]

    def test_the_final_retry_is_comply_only_and_a_late_rebuttal_is_ignored(
        self, tmp_path, state, critic_template, retry_template
    ):
        _seed_findings(tmp_path)
        prompts = []
        # retry 1: manager fails to fix (no rebuttal); retry 2: manager tries to
        # rebut, but the channel is closed on the final round.
        manager = _manager_runner(_draft(), _draft_rebutting("overclaim", "headline"), calls=None)

        def recording(command, **kwargs):
            prompts.append(command[command.index("-p") + 1])
            return manager(command, **kwargs)

        result = _stage(tmp_path, state, critic_template, retry_template,
                        _codex_runner(_blocked()), manager_runner=recording)
        assert result.status == PUBLISHED_WITH_UNRESOLVED
        # The retry-2 prompt is comply-only; the retry-1 prompt offers the channel.
        assert "rebuttal channel is CLOSED" in prompts[1]
        assert "REBUT it" in prompts[0] and "rebuttal channel is CLOSED" not in prompts[0]
        # The late rebuttal never reaches the published finding.
        assert "rebuttal" not in result.blocking_findings[0]

    def test_a_reworded_where_still_reaffirms_the_rebuttal(
        self, tmp_path, state, critic_template, retry_template
    ):
        _seed_findings(tmp_path)
        # The manager rebuts overclaim@headline; the critic re-files the same fault
        # as overclaim@headline.summary. The sole same-kind survivor is an
        # unambiguous re-file, so the rebuttal is reaffirmed, not lost.
        codex = _codex_runner(_blocked("overclaim", "headline"),
                              _blocked("overclaim", "headline.summary"))
        manager = _manager_runner(_draft_rebutting("overclaim", "headline"), _draft())
        result = _stage(tmp_path, state, critic_template, retry_template, codex, manager_runner=manager)
        assert result.status == PUBLISHED_WITH_UNRESOLVED
        survivor = result.blocking_findings[0]
        assert survivor["where"] == "headline.summary"
        assert survivor["rebuttal"]["adjudication"] == "reaffirmed"

    def test_manager_failure_stamps_the_cause_on_the_survivors(
        self, tmp_path, state, critic_template, retry_template
    ):
        _seed_findings(tmp_path)

        def broken_manager(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="boom", stderr="")

        result = _stage(tmp_path, state, critic_template, retry_template,
                        _codex_runner(_blocked()), manager_runner=broken_manager)
        assert result.status == PUBLISHED_WITH_UNRESOLVED
        # A reader can tell "manager crashed at retry N" from "survived retry 2".
        assert "manager unavailable at retry 1" in result.blocking_findings[0]["note"]

    def test_the_edited_sections_are_logged_for_visibility(
        self, tmp_path, state, critic_template, retry_template, caplog
    ):
        import logging
        caplog.set_level(logging.WARNING, logger="researchswarm.critique")
        _seed_findings(tmp_path)
        codex = _codex_runner(_blocked(), _clean())
        manager = _manager_runner(_draft(headline={"title": "t", "so_what": "softened"}))
        _stage(tmp_path, state, critic_template, retry_template, codex, manager_runner=manager)
        lines = [r.getMessage() for r in caplog.records if "edited section(s)" in r.getMessage()]
        assert lines and "headline" in lines[0]

    def test_a_reaffirmed_rebuttal_prints_both_sides_on_exhaustion(
        self, tmp_path, state, critic_template, retry_template
    ):
        _seed_findings(tmp_path)
        # The critic re-files the SAME finding every pass; the manager rebutted it
        # on retry 1, so the survivor carries the rebuttal marked reaffirmed.
        codex = _codex_runner(_blocked())
        manager = _manager_runner(_draft_rebutting("overclaim", "headline"), _draft())
        result = _stage(tmp_path, state, critic_template, retry_template, codex, manager_runner=manager)
        assert result.status == PUBLISHED_WITH_UNRESOLVED
        survivor = result.blocking_findings[0]
        assert survivor["kind"] == "overclaim"
        assert survivor["rebuttal"]["adjudication"] == "reaffirmed"
        assert survivor["rebuttal"]["text"]  # the manager's argument, printed
        assert survivor["rebuttal"]["sources"]  # and its sources

    def test_a_reaffirmed_finding_is_marked_comply_in_the_retry_prompt(
        self, tmp_path, state, critic_template, retry_template
    ):
        _seed_findings(tmp_path)
        prompts = []

        def manager(command, **kwargs):
            prompts.append(command[command.index("-p") + 1])
            draft = _draft_rebutting("overclaim", "headline")
            envelope = json.dumps({"is_error": False, "result": json.dumps(draft),
                                   "total_cost_usd": 0.1, "num_turns": 2})
            return SimpleNamespace(returncode=0, stdout=envelope, stderr="")

        _stage(tmp_path, state, critic_template, retry_template,
               _codex_runner(_blocked()), manager_runner=manager)
        # Retry 1's finding is fresh; retry 2's finding carries the per-finding
        # reaffirmed marker (the critic overruled the retry-1 rebuttal). The phrase
        # is unique to the rendered findings block, not the template's static rules.
        marker = "it weighed your rebuttal and stood by"
        assert marker not in prompts[0]
        assert marker in prompts[1]

    def test_a_retry_manager_failure_degrades_rather_than_crashes(
        self, tmp_path, state, critic_template, retry_template
    ):
        _seed_findings(tmp_path)

        def broken_manager(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="boom", stderr="")

        result = _stage(tmp_path, state, critic_template, retry_template,
                        _codex_runner(_blocked()), manager_runner=broken_manager)
        # The draft already passed the validator — a broken retry manager publishes
        # the dispute rather than failing the run.
        assert result.status == PUBLISHED_WITH_UNRESOLVED
        assert result.retries_used == 0
        assert result.blocking_findings[0]["kind"] == "overclaim"


def test_the_prompt_carries_all_five_inputs(tmp_path, state, critic_template, retry_template):
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

    _stage(tmp_path, state, critic_template, retry_template, runner)
    prompt = captured["prompt"]
    assert "Merck to buy Verastem" in prompt          # raw findings corpus
    assert "(no previous issue)" in prompt            # previous issue (run #1)
    assert state.thesis["beliefs"][0]["id"] in prompt  # thesis
    assert state.watchlist["entities"][0]["entity_id"] in prompt  # watchlist
    assert '"so_what": "matters"' in prompt           # the issue under judgment
