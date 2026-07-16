"""Rendering the shared researcher template.

One template, six beats. Beats differ in SCOPE, never in RULES — so the rules
live here exactly once and per-beat values are interpolated.

The load-bearing test in this file is the one asserting stance text is never
baked into the template file. If a stance is inlined at build time, an owner
can edit their worldview and the next issue still argues the old one, silently.
That is the single failure the propagation contract exists to prevent.
"""

import pytest

from researchswarm.beats import load_beats
from researchswarm.prompts import (
    RunContext,
    UnresolvedPlaceholder,
    load_template,
    render_researcher_prompt,
)
from researchswarm.state import load_state


@pytest.fixture
def ctx():
    return RunContext(
        run_id="run_20260716_0700",
        coverage_window_from="2026-07-13",
        coverage_window_to="2026-07-16",
    )


@pytest.fixture
def rendered(repo_root, ctx):
    state = load_state(repo_root / "state")
    beats = load_beats(repo_root / "config" / "beats.toml")
    template = load_template(repo_root / "prompts" / "researcher.md")
    beat = next(b for b in beats if b.id == "ma_dealmaking")
    return render_researcher_prompt(template, beat, ctx, state)


class TestLoadTemplate:
    def test_extracts_the_template_from_the_doc(self, repo_root):
        """researcher.md is a document ABOUT the template with the template
        fenced inside it. Rendering the whole file would ship the design notes
        to the model."""
        template = load_template(repo_root / "prompts" / "researcher.md")
        assert template.startswith("You are the {{beat_name}} researcher")
        assert "Render-time placeholder notes" not in template
        assert "## The template" not in template

    def test_missing_fence_is_an_error(self, tmp_path):
        path = tmp_path / "researcher.md"
        path.write_text("# No fenced template here\n")
        with pytest.raises(ValueError, match="no fenced"):
            load_template(path)


class TestRendering:
    def test_no_placeholder_survives(self, rendered):
        """Zero unresolved placeholders. A {{leftover}} reaching the model is a
        silent instruction to hallucinate."""
        assert "{{" not in rendered
        assert "}}" not in rendered

    def test_unknown_placeholder_raises_rather_than_shipping(self, ctx, repo_root):
        state = load_state(repo_root / "state")
        beats = load_beats(repo_root / "config" / "beats.toml")
        beat = beats[0]
        with pytest.raises(UnresolvedPlaceholder, match="mystery"):
            render_researcher_prompt("hello {{mystery}}", beat, ctx, state)

    def test_carries_the_beat_scope(self, rendered):
        assert "Pharma M&A & dealmaking" in rendered
        assert "acquisitions and takeouts announced this window" in rendered
        assert "label rumour vs confirmed" in rendered

    def test_carries_run_context(self, rendered):
        assert "run_20260716_0700" in rendered
        assert "2026-07-13" in rendered
        assert "2026-07-16" in rendered


class TestWatchlistRoster:
    def test_renders_every_entity_compactly(self, rendered, repo_root):
        state = load_state(repo_root / "state")
        for entity in state.watchlist["entities"]:
            assert entity["entity_id"] in rendered
            assert entity["name"] in rendered

    def test_excludes_why_tracked(self, rendered, repo_root):
        """why_tracked is a SUMMARY, and summaries are the manager's job. A
        researcher handed a summary is being handed an interpretation."""
        state = load_state(repo_root / "state")
        for entity in state.watchlist["entities"]:
            assert entity["why_tracked"] not in rendered

    def test_includes_watch_for(self, rendered):
        """watch_for is what makes the coverage duty actionable."""
        assert "Keytruda LOE mitigation" in rendered

    def test_marks_assets_as_assets(self, rendered):
        """Tickers vanish on acquisition; assets don't. Both are valid refs."""
        assert "asset_daraxonrasib" in rendered
        assert "frontier_asset" in rendered


class TestThesisLens:
    def test_stance_text_is_never_baked_into_the_template_file(self, repo_root):
        """The propagation contract's single invariant. A template that inlines
        stance text is a bug, not an optimisation: the owner edits their view,
        the next issue argues the old one, and nothing says a word."""
        raw = (repo_root / "prompts" / "researcher.md").read_text()
        state = load_state(repo_root / "state")
        for belief in state.thesis["beliefs"]:
            if belief["stance"]:
                assert belief["stance"] not in raw

    def test_stances_are_read_fresh_into_the_render(self, rendered, repo_root):
        state = load_state(repo_root / "state")
        for belief in state.thesis["beliefs"]:
            if belief["stance"]:
                assert belief["stance"] in rendered

    def test_stamps_the_thesis_version(self, rendered):
        """An issue's angles are valid only against the version they argued."""
        assert "version 2" in rendered

    def test_renders_stance_provenance(self, rendered):
        """4 of 6 stances are provisional. A lens the reader knows is
        provisional is safer than one presented as settled."""
        assert "agent_draft_delegated" in rendered
        assert "owner" in rendered

    def test_dormant_slot_renders_the_marker_not_an_invention(self, ctx, repo_root):
        state = load_state(repo_root / "state")
        state.thesis["beliefs"][0]["stance"] = None
        beats = load_beats(repo_root / "config" / "beats.toml")
        template = load_template(repo_root / "prompts" / "researcher.md")
        out = render_researcher_prompt(template, beats[0], ctx, state)
        assert "(no stance seeded)" in out

    def test_lens_framing_survives(self, rendered):
        """The lens changes what a researcher NOTICES, never what it claims."""
        assert "ATTENTION LENS, not a conclusion" in rendered


class TestCatalystQueue:
    def test_renders_active_items(self, rendered, repo_root):
        state = load_state(repo_root / "state")
        active = [i for i in state.catalyst_queue["queue"] if i["status"] in ("pending", "slipped")]
        assert active, "fixture should have active items"
        for item in active:
            assert item["id"] in rendered

    def test_omits_terminal_items(self, ctx, repo_root):
        """delivered and dead are terminal and are not chased."""
        state = load_state(repo_root / "state")
        state.catalyst_queue["queue"].append(
            {"id": "cat_dead_one", "asset": "x", "entity_ids": [], "catalyst": "y",
             "expected_window": None, "status": "delivered"}
        )
        beats = load_beats(repo_root / "config" / "beats.toml")
        template = load_template(repo_root / "prompts" / "researcher.md")
        out = render_researcher_prompt(template, beats[0], ctx, state)
        assert "cat_dead_one" not in out


class TestSurgeIsAbsentForNow:
    def test_no_surge_block_outside_a_window(self, rendered):
        """Surge lands in its own build. Outside a window the block is empty and
        the carve-out says so — the placeholders still resolve."""
        assert "No carve-outs." in rendered
        assert "surge:" not in rendered
