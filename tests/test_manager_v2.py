"""The manager seam validator, issue.json v2.0.0 (spec/05, spec/07).

`validate_issue_draft` dispatches on the DRAFT's own schema_version. These
exercise the v2 seam contract; the v1 seam tests (test_manager.py) still hold.

The seam runs PRE-derivation, so a valid draft carries `stats == {}` (the
orchestrator derives counts, never the manager). The real published sample has
`stats` filled, so the fixture blanks it — exactly what the manager emits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchswarm.manager import IssueDraftInvalid, validate_issue_draft

SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "schema"
    / "sample-issue-hmbd-001-2026-07-18.json"
)


def _draft() -> dict:
    """The sample as the manager would emit it at the seam: stats blanked, and
    the run identifiers the orchestrator handed it."""
    draft = json.loads(SAMPLE.read_text())
    draft.pop("_comment", None)
    draft["stats"] = {}
    return draft


RUN_ID = "run_20260718_0700"
THESIS_VERSION = 3


def _validate(draft):
    validate_issue_draft(draft, thesis_version=THESIS_VERSION, run_id=RUN_ID)


class TestAValidV2DraftPasses:
    def test_the_real_sample_passes_the_seam(self):
        # run identifiers already match the sample's issue.run block
        _validate(_draft())  # does not raise


class TestTheV2SeamContract:
    def test_a_missing_program_block_is_rejected(self):
        draft = _draft()
        del draft["program"]
        with pytest.raises(IssueDraftInvalid, match="program"):
            _validate(draft)

    def test_a_program_missing_moa_is_rejected(self):
        draft = _draft()
        draft["program"].pop("moa")
        with pytest.raises(IssueDraftInvalid, match="moa"):
            _validate(draft)

    def test_a_missing_v2_key_is_rejected(self):
        draft = _draft()
        del draft["house_view"]
        with pytest.raises(IssueDraftInvalid, match="house_view"):
            _validate(draft)

    def test_authored_stats_is_a_contract_breach(self):
        draft = _draft()
        draft["stats"] = {"competitors_moved": 2}
        with pytest.raises(IssueDraftInvalid, match="stats"):
            _validate(draft)

    def test_a_headline_without_so_what_is_rejected(self):
        draft = _draft()
        draft["headline"].pop("so_what")
        with pytest.raises(IssueDraftInvalid, match="so_what"):
            _validate(draft)

    def test_a_run_id_mismatch_is_rejected(self):
        draft = _draft()
        draft["issue"]["run"]["run_id"] = "run_wrong"
        with pytest.raises(IssueDraftInvalid, match="run_id"):
            _validate(draft)

    def test_a_thesis_version_mismatch_is_rejected(self):
        draft = _draft()
        draft["issue"]["run"]["thesis_version"] = 99
        with pytest.raises(IssueDraftInvalid, match="thesis_version"):
            _validate(draft)

    def test_a_v1_key_set_does_not_satisfy_v2(self):
        # a draft claiming 2.0.0 but carrying v1 sections is rejected
        draft = _draft()
        draft["watchlist"] = draft.pop("competitors")
        with pytest.raises(IssueDraftInvalid, match="competitors"):
            _validate(draft)


class TestDispatchLeavesV1Intact:
    def test_a_v1_draft_still_routes_to_the_v1_contract(self):
        # a 1.0.0 draft missing a v1 key is caught by the v1 path, and the v2
        # program block is NOT demanded of it
        draft = {"schema_version": "1.0.0"}
        with pytest.raises(IssueDraftInvalid) as exc:
            _validate(draft)
        assert "watchlist" in str(exc.value)
        assert "program" not in str(exc.value)
