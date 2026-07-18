"""Rendering the manager template.

The manager is the only component that interprets, so the load-bearing tests
here are about the duties the template CARRIES — the stats-must-be-empty
instruction, the inline-degradation duty, the dormant marker — and about the
propagation contract binding the manager exactly as it binds the researcher:
stances arrive interpolated fresh, never baked into the file.
"""

import json

import pytest

from researchswarm.prompts import (
    RunContext,
    UnresolvedPlaceholder,
    load_template,
    render_manager_prompt,
)
from researchswarm.state import load_state

RUN_ID = "run_20260717_0045"
DORMANT_MARKER = "No thesis seeded — facts only"


@pytest.fixture
def ctx():
    return RunContext(
        run_id=RUN_ID,
        coverage_window_from="2026-07-13",
        coverage_window_to="2026-07-17",
    )


@pytest.fixture
def state(repo_root):
    return load_state(repo_root / "state")


@pytest.fixture
def template(repo_root):
    return load_template(repo_root / "prompts" / "manager.md")


def _findings(beat_id, **overrides):
    payload = {
        "beat": beat_id,
        "run_id": RUN_ID,
        "coverage_window": {"from": "2026-07-13", "to": "2026-07-17"},
        "quiet": False,
        "findings": [
            {
                "summary": f"{beat_id} found a thing.",
                "entity_ids": ["merck"],
                "sources": [{"url": "https://x", "publisher": "Endpoints News",
                             "tier": "trade", "published_at": "2026-07-15", "paywalled": False}],
                "beat_priority": "high",
            }
        ],
        "coverage_notes": {"angles_run": ["a"], "entities_checked": ["merck"], "notes": "n"},
        "errors": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def rendered(template, ctx, state):
    return render_manager_prompt(
        template,
        ctx,
        state,
        findings_by_beat={"ma_dealmaking": _findings("ma_dealmaking")},
        beats_failed=["startup_frontier"],
        prior_quiet={},
        models={"researchers": "sonnet", "manager": "claude-opus-4-8", "critic": None},
        issue_id="2026-07-17",
        published_at="2026-07-17T00:45:00+08:00",
    )


class TestLoadTemplate:
    def test_extracts_the_template_from_the_doc(self, repo_root):
        """manager.md is a document ABOUT the template with the template fenced
        inside it. Rendering the whole file would ship the design notes."""
        template = load_template(repo_root / "prompts" / "manager.md")
        assert template.startswith("You are the MANAGER")
        assert "Render-time placeholder notes" not in template
        assert "Design choices worth stating" not in template


class TestRendering:
    def test_no_placeholder_survives(self, rendered):
        assert "{{" not in rendered
        assert "}}" not in rendered

    def test_unknown_placeholder_raises_rather_than_shipping(self, ctx, state):
        with pytest.raises(UnresolvedPlaceholder, match="mystery"):
            render_manager_prompt(
                "hello {{mystery}}", ctx, state,
                findings_by_beat={}, beats_failed=[], prior_quiet={},
                models={}, issue_id="x", published_at="y",
            )

    def test_carries_run_identity(self, rendered):
        assert RUN_ID in rendered
        assert "2026-07-17" in rendered
        assert "2026-07-13 → 2026-07-17" in rendered

    def test_stamps_the_models_block(self, rendered):
        assert "claude-opus-4-8" in rendered
        assert '"critic": null' in rendered


class TestTheAuthorshipDuties:
    def test_carries_the_stats_must_be_empty_instruction(self, rendered):
        """stats == {} is the bar that cannot lie — the orchestrator derives
        every count. The template must say so."""
        assert "stats: {}" in rendered
        assert "orchestrator derives" in rendered

    def test_carries_the_inline_degradation_duty(self, rendered):
        """A dead beat is marked at the point of the absence, not only in a
        footer — a reader who never scrolls to Sources reads a thin section as
        a fact about the world."""
        assert "beat_failed" in rendered
        assert "M&A coverage unavailable this cycle — beat failed" in rendered

    def test_keeps_so_what_and_research_angle_distinct(self, rendered):
        """Collapsing them lets a dormant thesis silence the headline's reason
        to care — a thesis-gated field swallowing a thesis-independent duty."""
        assert "so_what and research_angle are DIFFERENT fields" in rendered

    def test_carries_the_dormant_marker_as_a_literal(self, rendered):
        """The exact bytes the model must emit for a dormant slot are an
        instruction, not state, so they live in the template."""
        assert DORMANT_MARKER in rendered


class TestThesisIsInterpolatedFresh:
    def test_stance_text_is_never_baked_into_the_template_file(self, repo_root, state):
        """The propagation contract binds the manager too: a template that
        inlined a stance would argue the old worldview after an owner edit."""
        raw = (repo_root / "prompts" / "manager.md").read_text()
        for belief in state.thesis["beliefs"]:
            if belief["stance"]:
                assert belief["stance"] not in raw

    def test_stances_are_read_fresh_into_the_render(self, rendered, state):
        for belief in state.thesis["beliefs"]:
            if belief["stance"]:
                assert belief["stance"] in rendered

    def test_dormant_slot_renders_the_marker_in_the_thesis_block(self, template, ctx, state):
        """A dormant slot shows the researcher-style marker in the lens, telling
        the manager which slot to gate. The stance itself is gone."""
        dormant = state.thesis["beliefs"][0]
        original = dormant["stance"]
        dormant["stance"] = None
        out = render_manager_prompt(
            template, ctx, state,
            findings_by_beat={}, beats_failed=[], prior_quiet={},
            models={}, issue_id="x", published_at="y",
        )
        lens = out[out.index("## Thesis lens") : out.index("## Catalyst queue")]
        assert "(no stance seeded)" in lens
        assert original not in lens


class TestFindingsCorpus:
    def test_embeds_each_beats_findings_verbatim(self, template, ctx, state):
        corpus = {
            "ma_dealmaking": _findings("ma_dealmaking"),
            "backstop": _findings("backstop"),
        }
        out = render_manager_prompt(
            template, ctx, state,
            findings_by_beat=corpus, beats_failed=[], prior_quiet={},
            models={}, issue_id="x", published_at="y",
        )
        for beat_id, payload in corpus.items():
            assert f"findings from beat: {beat_id}" in out
            # The whole object is embedded, not a summary of it.
            assert json.dumps(payload, indent=2) in out

    def test_names_the_failed_beats(self, rendered):
        """The manager must see the hole to mark it. Failed beats are named next
        to the facts, not hidden."""
        assert "startup_frontier" in rendered
        assert "beats that failed" in rendered.lower()


class TestPriorQuietCounts:
    def test_run_one_has_no_previous_issue(self, rendered):
        assert "(no previous issue)" in rendered

    def test_prior_counts_render_for_increment(self, template, ctx, state):
        out = render_manager_prompt(
            template, ctx, state,
            findings_by_beat={}, beats_failed=[],
            prior_quiet={"pfizer": 2, "roche": 1},
            models={}, issue_id="x", published_at="y",
        )
        assert "- pfizer: 2" in out
        assert "- roche: 1" in out


class TestCatalystQueueSnapshot:
    def test_is_json_the_manager_reproduces_verbatim(self, rendered, state):
        """The manager copies factual fields verbatim, so it is handed JSON, not
        a table it would have to re-serialise."""
        for item in state.catalyst_queue["queue"]:
            assert item["id"] in rendered
            assert item["catalyst"] in rendered

    def test_omits_what_it_would_prove_so_the_manager_authors_it(self, rendered):
        """what_it_would_prove is thesis-gated interpretation — the manager
        authors it rather than copying the state's placeholder."""
        snapshot = rendered[rendered.index("## Catalyst queue") : rendered.index("## Prior quiet")]
        assert "what_it_would_prove" not in snapshot
