"""Stage 4's retry loop on the v2 path — specifically, who owns `stats`.

The v1 loop is covered in `test_validation.py`; this file exists for the one
behaviour the two paths do NOT share. In v2 the manager seam forbids the manager
from authoring counts (`stats` must be `{}`) while the validator requires them
populated, so the ORCHESTRATOR derives them. That split has a sharp edge at the
retry boundary, and these tests hold it: a retry calls the manager AGAIN, the
manager obeys its seam AGAIN, and the draft that comes back has `stats` reset.
Deriving once before the loop leaves every retry to fail a check it was never
told to fix — which is exactly how three live runs burned their whole budget on
`malformed_shape@stats` while dutifully fixing everything else.

Offline: the manager is a fake runner, the gate is free and deterministic.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchswarm.prompts import load_template
from researchswarm.publish import derive_full_stats
from researchswarm.validation import ValidationExhausted, run_validation_stage_v2
from researchswarm.validator import derive_stats

from test_validator_v2 import _load_sample, _state_for

# The sample's OWN identifiers. The manager seam re-checks a retry draft's
# `issue.run` against the run it belongs to, so the loop's run_id and
# thesis_version have to be the ones the sample already carries — otherwise the
# fake manager's output is rejected at the seam and never reaches the gate these
# tests are about.
RUN_ID = "run_20260718_0700"
THESIS_VERSION = 3


def _roster(issue: dict) -> set[str]:
    """The narrow accountability set: the entities this issue accounts for.

    Taken from the sample rather than invented so the coverage check sees a
    roster the draft actually answers to.
    """
    quiet = issue.get("quiet_this_cycle") or {}
    return {c["entity_id"] for c in issue.get("competitors") or []} | {
        e["entity_id"] for e in quiet.get("no_news") or []
    }


def _seam_draft(issue: dict) -> dict:
    """The draft AS THE MANAGER EMITS IT — `stats` empty, per its seam."""
    draft = copy.deepcopy(issue)
    draft["stats"] = {}
    return draft


def _breaks_the_gate(draft: dict) -> dict:
    """Trip one blocking check the manager can plausibly fix on retry."""
    broken = copy.deepcopy(draft)
    broken["tldr_bullets"] = []
    return broken


def _manager_runner(*drafts, prompts=None):
    """A fake manager that returns each queued draft, recording its prompt."""
    queue = list(drafts)

    def runner(command, **kwargs):
        if prompts is not None:
            prompts.append(command[command.index("-p") + 1])
        envelope = json.dumps(
            {
                "is_error": False,
                "result": json.dumps(queue.pop(0)),
                "total_cost_usd": 0.1,
                "num_turns": 3,
            }
        )
        return SimpleNamespace(returncode=0, stdout=envelope, stderr="")

    return runner


@pytest.fixture
def retry_template(repo_root):
    return load_template(repo_root / "prompts" / "manager-retry.md")


def _stage(tmp_path, draft, issue, *, runner, retry_template, derive=True):
    draft_path = tmp_path / "issue-draft.json"
    draft_path.write_text(json.dumps(draft))
    issues_dir = tmp_path / "issues"
    return run_validation_stage_v2(
        draft=draft,
        draft_path=draft_path,
        state=_state_for(issue),
        roster=_roster(issue),
        issues_dir=issues_dir,
        retry_template=retry_template,
        model="claude-opus-4-8",
        run_id=RUN_ID,
        thesis_version=THESIS_VERSION,
        derive_stats=(lambda d: derive_full_stats(d, issues_dir)) if derive else None,
        runner=runner,
    )


class TestTheOrchestratorOwnsStats:
    def test_a_seam_shaped_draft_passes_without_the_manager_authoring_counts(
        self, tmp_path, retry_template
    ):
        """`stats: {}` in, real counts out, zero manager calls."""
        issue = _load_sample()

        def never(*a, **k):
            raise AssertionError("the manager must not be called on a clean pass")

        result = _stage(
            tmp_path, _seam_draft(issue), issue, runner=never, retry_template=retry_template
        )
        assert result.retries_used == 0
        assert result.draft["stats"]["competitors_moved"] == len(issue["competitors"])

    def test_the_derived_counts_are_the_gates_own(self, tmp_path, retry_template):
        """Derivation and validation share `derive_stats`, so they cannot drift."""
        issue = _load_sample()
        result = _stage(
            tmp_path, _seam_draft(issue), issue, runner=None, retry_template=retry_template
        )
        for key, want in derive_stats(result.draft).items():
            assert result.draft["stats"][key] == want

    def test_a_retry_draft_is_derived_too(self, tmp_path, retry_template):
        """THE REGRESSION.

        The manager fixes what it was told about and re-emits `stats: {}` because
        that is what its seam demands. If the loop only derived before its first
        iteration, this round would fail `malformed_shape@stats` — a finding the
        manager was never asked to fix and, obeying its seam, cannot fix. The run
        would then exhaust its budget and stub, which is what live runs did.
        """
        issue = _load_sample()
        fixed_but_seam_shaped = _seam_draft(issue)
        runner = _manager_runner(fixed_but_seam_shaped)

        result = _stage(
            tmp_path,
            _breaks_the_gate(_seam_draft(issue)),
            issue,
            runner=runner,
            retry_template=retry_template,
        )

        assert result.retries_used == 1
        assert result.draft["stats"]["competitors_moved"] == len(issue["competitors"])
        kinds = {
            f["kind"] for f in result.draft["critic_report"]["validator_report"]["findings"]
        }
        assert "malformed_shape" not in kinds

    def test_without_derivation_the_seam_and_the_gate_deadlock(
        self, tmp_path, retry_template
    ):
        """The bug this parameter exists to close, pinned as a fact.

        With no derivation the two halves of the contract have no passing path:
        the draft the manager is REQUIRED to emit is one the gate is REQUIRED to
        reject. Asserting it here means a future refactor that quietly drops the
        injection fails loudly rather than costing another live run.
        """
        issue = _load_sample()
        runner = _manager_runner(_seam_draft(issue), _seam_draft(issue))

        with pytest.raises(ValidationExhausted) as exc:
            _stage(
                tmp_path,
                _seam_draft(issue),
                issue,
                runner=runner,
                retry_template=retry_template,
                derive=False,
            )

        assert any(f.where == "stats" for f in exc.value.blocking)


class TestTheRetryTemplateMatchesTheSeam:
    def test_it_does_not_pin_a_schema_version(self, retry_template):
        """Both paths load this one template, so a hard-coded version is wrong
        for one of them. It said v1.0.0 while the v2 path was live."""
        assert "v1.0.0" not in retry_template
        assert "14 top-level keys" not in retry_template

    def test_it_still_tells_the_manager_not_to_author_stats(self, retry_template):
        assert "stats" in retry_template
