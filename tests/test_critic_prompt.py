"""Rendering the critic rubric: five inputs in, no placeholder left, rubric intact.

The load-bearing decision of the rubric is WHAT THE CRITIC SEES — so the tests
here assert all five inputs reach the prompt, and that the load-bearing rubric
strings (the sorting principle, the receipt requirement, the verdict contract, the
no-web statement) are actually in the template the model receives.
"""

import json

import pytest

from researchswarm.prompts import (
    UnresolvedPlaceholder,
    load_template,
    render_critic_prompt,
)


@pytest.fixture
def template(repo_root):
    return load_template(repo_root / "prompts" / "critic.md")


ISSUE = {"issue": {"id": "2026-07-16", "coverage_window": {"from": "2026-07-13", "to": "2026-07-16"}},
         "headline": {"title": "Merck buys Verastem", "so_what": "resets ADC pricing"}}
FINDINGS = {"ma_dealmaking": {"beat": "ma_dealmaking",
                              "findings": [{"summary": "a scoop", "sources": []}]}}
WATCHLIST = {"entities": [{"entity_id": "merck", "name": "Merck & Co."}]}
THESIS = {"version": 2, "beliefs": [{"id": "pharma-ma-appetite", "stance": "Acquirers wait."}]}


def _render(template, **overrides):
    kwargs = dict(issue=ISSUE, findings_by_beat=FINDINGS, previous_issue=None,
                  watchlist=WATCHLIST, thesis=THESIS)
    kwargs.update(overrides)
    return render_critic_prompt(template, **kwargs)


class TestFiveInputs:
    def test_issue_is_inlined(self, template):
        assert "resets ADC pricing" in _render(template)

    def test_findings_corpus_is_inlined_and_labelled(self, template):
        prompt = _render(template)
        assert "findings from beat: ma_dealmaking" in prompt
        assert "a scoop" in prompt

    def test_previous_issue_none_renders_the_marker(self, template):
        assert "(no previous issue)" in _render(template)

    def test_previous_issue_is_inlined_when_present(self, template):
        prev = {"issue": {"id": "2026-07-13"}, "headline": {"title": "last week"}}
        assert "last week" in _render(template, previous_issue=prev)

    def test_watchlist_is_inlined(self, template):
        assert "merck" in _render(template)

    def test_thesis_is_inlined(self, template):
        assert "pharma-ma-appetite" in _render(template)

    def test_no_surge_renders_the_coverage_window_fallback(self, template):
        assert "compare provenance_stale against issue.coverage_window" in _render(template)

    def test_surge_supplies_the_conference_window(self, template):
        """run.surge in the issue carries only {window, day, of}, so the critic
        gets the conference DATES here — the provenance_stale reference in surge."""
        from researchswarm.calendar import SurgeState

        surge = SurgeState(window="ASCO 2026", window_id="asco", day=2, of=5,
                           starts="2026-05-29", ends="2026-06-02")
        out = _render(template, surge=surge)
        assert "ASCO 2026" in out
        assert "2026-05-29 to 2026-06-02" in out
        assert "CONFERENCE window" in out


class TestNoPlaceholderSurvives:
    def test_a_fully_rendered_prompt_has_no_double_brace(self, template):
        assert "{{" not in _render(template)

    def test_a_missing_value_raises_rather_than_reaching_the_model(self):
        # A template referencing an unrendered placeholder must raise — a literal
        # {{issue_json}} reaching Codex is an instruction to invent.
        with pytest.raises(UnresolvedPlaceholder):
            render_critic_prompt("judge {{nonexistent}}", issue=ISSUE, findings_by_beat=FINDINGS,
                                 previous_issue=None, watchlist=WATCHLIST, thesis=THESIS)


class TestLoadBearingRubric:
    def test_the_sorting_principle_is_present(self, template):
        assert "misled about a FACT" in template

    def test_the_receipt_requirement_is_spelled_out(self, template):
        assert "RECEIPT REQUIRED" in template
        assert "APPEARS in the raw findings corpus" in template
        assert "cited NOWHERE in the issue" in template

    def test_the_verdict_contract_is_present(self, template):
        for verdict in ("pass", "pass_with_advisories", "blocked"):
            assert verdict in template

    def test_the_no_web_statement_is_present(self, template):
        assert "web access" in template.lower()
        assert "FOUND and then" in template  # found-and-then-lost boundary

    def test_all_six_blocking_kinds_named(self, template):
        for kind in ("provenance_stale", "overclaim", "aggregator_only",
                     "unconfirmed_as_fact", "dropped_story", "thesis_impact_false"):
            assert kind in template

    def test_all_twelve_advisory_kinds_named(self, template):
        from researchswarm.critic import ADVISORY_KINDS
        for kind in ADVISORY_KINDS:
            assert kind in template

    def test_provenance_stale_states_the_surge_exception(self, template):
        """spec/06: the comparison window is coverage normally, the CONFERENCE
        window during a surge — the one reference-window change, not a relaxed bar.
        Now that surge has landed (build 10), the rubric states the rule
        conditionally and is handed the dates via {{surge_window}}."""
        # Fragments, because the template wraps the sentence across lines.
        assert "issue.coverage_window" in template
        assert "when run.surge is present" in template
        assert "compare against the CONFERENCE window" in template
        assert "{{surge_window}}" in template

    def test_output_is_one_json_object_no_fences(self, template):
        assert "EXACTLY ONE JSON object" in template
        assert "no markdown fences" in template
