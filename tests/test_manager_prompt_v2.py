"""Rendering the v2 manager template — the per-program detective (spec/05, spec/07).

The v2 manager is still the only component that interprets, so the load-bearing
tests here are about the DUTIES the template carries — the read-through-on-every-
item rule, the admission rule, stats-must-be-empty, the platform_threat→house
asymmetry, inline dormancy — and about the propagation contract binding the
manager TWICE now: both the thesis stances and the interest list arrive
interpolated fresh, never baked into the file.

These exercise `render_manager_prompt_v2` against the REAL pilot config
(config/programs/hmbd-001.toml, config/interests.toml, state/thesis.json) plus the
hand-built v2 findings fixtures, so a drift between the authored config and the
renderer fails here. No live model is called — that is the parent's job.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchswarm.apertures import plan_apertures
from researchswarm.programs import (
    load_edges,
    load_entities,
    load_interests,
    load_program,
)
from researchswarm.prompts import (
    UnresolvedPlaceholder,
    load_template,
    render_manager_prompt_v2,
)

RUN_ID = "run_20260718_0700"
DORMANT_MARKER = "No thesis seeded — facts only"

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "findings-v2"

# Fixture filename → the aperture id the manager keys the corpus by. The arena
# file uses a dash in its name (filesystem-friendly) but the aperture id keeps the
# spec's colon form (arena_scan:<indication>).
_FIXTURE_APERTURES = {
    "biology_scan.json": "biology_scan",
    "arena_scan-squamous-nsclc.json": "arena_scan:squamous-nsclc",
    "house_sweep.json": "house_sweep",
}


@pytest.fixture
def program(repo_root):
    return load_program(repo_root / "config", "hmbd-001")


@pytest.fixture
def interests(repo_root):
    return load_interests(repo_root / "config")


@pytest.fixture
def thesis(repo_root):
    return json.loads((repo_root / "state" / "thesis.json").read_text())


@pytest.fixture
def catalyst_queue(repo_root):
    return json.loads(
        (repo_root / "state" / "programs" / "hmbd-001" / "catalyst-queue.json").read_text()
    )


@pytest.fixture
def edges(repo_root):
    return load_edges(repo_root / "state", "hmbd-001")


@pytest.fixture
def entities(repo_root):
    return load_entities(repo_root / "state")


@pytest.fixture
def findings():
    """The three hand-built aperture findings, keyed by aperture id."""
    corpus = {}
    for filename, aperture_id in _FIXTURE_APERTURES.items():
        corpus[aperture_id] = json.loads((FIXTURES / filename).read_text())
    return corpus


@pytest.fixture
def template(repo_root):
    return load_template(repo_root / "prompts" / "manager-v2.md")


def _render(template, program, interests, thesis, catalyst_queue, edges, entities,
            findings, **overrides):
    kwargs = dict(
        program=program,
        interests=interests,
        apertures=plan_apertures(program),
        findings_by_aperture=findings,
        apertures_degraded=["arena_scan:nrg1-fusion-solid-tumors"],
        thesis=thesis,
        catalyst_queue=catalyst_queue,
        edges=edges,
        entities=entities,
        prior_quiet={},
        run_id=RUN_ID,
        issue_id="2026-07-18",
        published_at="2026-07-18T07:41:00+08:00",
        coverage_window_from="2026-07-14",
        coverage_window_to="2026-07-18",
        thesis_version=3,
        interest_list_version=4,
        models={"researchers": "claude-sonnet-5", "manager": "claude-opus-4-8", "critic": "gpt-5-codex"},
    )
    kwargs.update(overrides)
    return render_manager_prompt_v2(template, **kwargs)


@pytest.fixture
def rendered(template, program, interests, thesis, catalyst_queue, edges, entities, findings):
    return _render(template, program, interests, thesis, catalyst_queue, edges, entities, findings)


class TestLoadTemplate:
    def test_extracts_the_v2_template_from_the_doc(self, repo_root):
        """manager-v2.md is a document ABOUT the template with the template fenced
        inside it. Rendering the whole file would ship the design notes."""
        template = load_template(repo_root / "prompts" / "manager-v2.md")
        assert template.startswith("You are the MANAGER")
        assert "Render-time placeholder notes" not in template
        assert "Design choices worth stating" not in template


class TestRendering:
    def test_no_placeholder_survives(self, rendered):
        assert "{{" not in rendered
        assert "}}" not in rendered

    def test_unknown_placeholder_raises_rather_than_shipping(
        self, program, interests, thesis, catalyst_queue, edges, entities, findings
    ):
        with pytest.raises(UnresolvedPlaceholder, match="mystery"):
            _render(
                "hello {{mystery}}", program, interests, thesis,
                catalyst_queue, edges, entities, findings,
            )

    def test_echoes_the_run_identity(self, rendered):
        assert RUN_ID in rendered
        assert "2026-07-18" in rendered
        assert "2026-07-14 → 2026-07-18" in rendered
        assert "hmbd-001" in rendered

    def test_echoes_both_version_stamps(self, rendered):
        """A read-through's steering is valid only against the thesis AND interest
        versions that argued it — both are echoed into the run block."""
        assert "thesis_version: 3" in rendered
        assert "interest_list_version: 4" in rendered

    def test_stamps_the_models_block(self, rendered):
        assert "claude-opus-4-8" in rendered
        assert "gpt-5-codex" in rendered


class TestTheProgramIdentity:
    def test_carries_the_program_subject(self, rendered):
        assert "HMBD-001" in rendered
        assert "HER3 (ERBB3)" in rendered

    def test_carries_the_load_bearing_moa(self, rendered):
        """moa separates a target_twin from a mechanism_twin — the distinction the
        whole competitor model turns on, so it must reach the manager."""
        assert "signalling_blockade" in rendered

    def test_carries_the_active_arena_scope(self, rendered):
        """The aperture roster names each scan's scope so the manager knows which
        sections a dormant scan leaves a hole in."""
        assert "squamous-nsclc" in rendered
        assert "nrg1-fusion-solid-tumors" in rendered


class TestTheAuthorshipDuties:
    def test_output_is_schema_v2(self, rendered):
        assert '"2.0.0"' in rendered
        assert "15 top-level keys" in rendered

    def test_carries_the_stats_must_be_empty_instruction(self, rendered):
        """stats == {} is the bar that cannot lie — the orchestrator derives every
        count. The template must say so."""
        assert "stats: {}" in rendered
        assert "orchestrator derives" in rendered

    def test_requires_a_read_through_on_every_item(self, rendered):
        assert "read_through" in rendered
        assert "load-bearing authored object" in rendered

    def test_carries_the_typed_relation_set(self, rendered):
        for relation in ("mechanism_twin", "target_twin", "setting_rival", "benchmark_soc", "platform_threat"):
            assert relation in rendered

    def test_platform_threat_routes_to_the_house_view(self, rendered):
        """platform_threat is company-unit and NEVER in competitors[] — the one
        relation whose unit is a company, not the program."""
        assert "NEVER appears in competitors[]" in rendered

    def test_carries_the_admission_rule(self, rendered):
        """Every surfaced item → a read-through, a capped blind spot, or a
        dropped-with-receipt. Nothing silently omitted."""
        assert "DROPPED WITH RECEIPT" in rendered
        assert "CAPPED BLIND SPOT" in rendered
        assert "dropped_with_receipt" in rendered

    def test_entity_refs_point_at_competitors_not_the_program(self, rendered):
        """entity_refs resolve against state/entities/ (competitor/house entities);
        the program is config, not an entity. A live run put the program slug in
        headline.entity_refs and it dangled — the template must forbid it."""
        assert "entity_refs point at COMPETITORS, never at the program" in rendered
        assert "NEVER put the program's own id or slug" in rendered

    def test_keeps_so_what_and_read_through_distinct(self, rendered):
        """Collapsing them lets a dormant thesis silence the headline's reason to
        care — a thesis-gated field swallowing a thesis-independent duty."""
        assert "so_what and read_through.text are DIFFERENT fields" in rendered

    def test_carries_the_efficacy_primary_only_rule(self, rendered):
        assert "PRIMARY-SOURCE-ONLY" in rendered
        assert "landscape_number_unsourced" in rendered

    def test_carries_the_blind_spot_cap_and_overflow(self, rendered):
        assert "cap" in rendered
        assert "overflow" in rendered

    def test_carries_the_dormant_marker_as_a_literal(self, rendered):
        """The exact bytes the model must emit for a dormant slot are an
        instruction, not state, so they live in the template."""
        assert DORMANT_MARKER in rendered

    def test_carries_the_inline_degradation_duty(self, rendered):
        """A dormant/dead aperture is marked at the point of the absence, not only
        in a footer — a reader who never scrolls to Sources reads a thin section
        as a fact about the world."""
        assert "arena_scan_dormant" in rendered
        assert "arena_scan_failed" in rendered

    def test_pins_the_apertures_degraded_string_shape(self, rendered):
        """The validator's empty_section check reads apertures_degraded literally:
        it must be aperture-id STRINGS, not rich objects (a live run emitted
        objects and dropped the dormant aperture from apertures_run — a real bug
        the sample-matching instruction prevents)."""
        assert "flat list of aperture-id STRINGS" in rendered
        assert '"apertures_degraded": ["arena_scan:nrg1-fusion-solid-tumors"]' in rendered

    def test_pins_apertures_run_includes_the_dormant_aperture(self, rendered):
        """A dormant arena appears in apertures_run with status dormant AND its id
        in apertures_degraded — the sample's shape the validator confirms against."""
        assert "NOT omitted from apertures_run" in rendered
        assert '"status": "dormant"' in rendered


class TestTheSteeringWheel:
    def test_carries_the_interest_notes(self, rendered, interests):
        for interest in interests.interests:
            assert interest.note in rendered

    def test_marks_a_fresh_list_fresh(self, rendered):
        """The pilot interest list was just edited, so it is fresh, not stale."""
        assert "rot: fresh" in rendered

    def test_a_stale_list_renders_the_degradation(
        self, template, program, thesis, catalyst_queue, edges, entities, findings, repo_root
    ):
        """Rot is fail-visible, never silent: an old interest list must tell the
        manager to stamp rot_status: stale."""
        from researchswarm.programs import Interest, InterestList

        stale = InterestList(
            version=4,
            last_edited="2020-01-01",
            last_edited_by="owner",
            interests=(Interest(tier="strong", note="something"),),
        )
        out = _render(
            template, program, stale, thesis, catalyst_queue, edges, entities, findings
        )
        assert "STALE" in out


class TestThesisIsInterpolatedFresh:
    def test_stance_text_is_never_baked_into_the_template_file(self, repo_root, thesis):
        """The propagation contract binds the v2 manager too: a template that
        inlined a stance would argue the old worldview after an owner edit."""
        raw = (repo_root / "prompts" / "manager-v2.md").read_text()
        for belief in thesis["beliefs"]:
            if belief["stance"]:
                assert belief["stance"] not in raw

    def test_stances_are_read_fresh_into_the_render(self, rendered, thesis):
        for belief in thesis["beliefs"]:
            if belief["stance"]:
                assert belief["stance"] in rendered

    def test_dormant_slot_renders_the_marker_in_the_thesis_block(
        self, template, program, interests, thesis, catalyst_queue, edges, entities, findings
    ):
        """A dormant slot shows the researcher-style marker in the lens, telling
        the manager which slot to gate. The stance itself is gone."""
        dormant = thesis["beliefs"][0]
        original = dormant["stance"]
        dormant["stance"] = None
        out = _render(
            template, program, interests, thesis, catalyst_queue, edges, entities, findings
        )
        lens = out[out.index("## Thesis lens") : out.index("## Interest list")]
        assert "(no stance seeded)" in lens
        assert original not in lens


class TestTheCompetitorRoster:
    def test_seed_competitors_render_untyped_at_cold_start(self, rendered):
        """At seed no edges exist, so the roster is exactly the cold-start seed
        competitors, each marked untyped so the manager knows to type it."""
        assert "asset_her3_dxd · (seed — untyped)" in rendered
        assert "asset_ivonescimab · (seed — untyped)" in rendered


class TestFindingsCorpus:
    def test_embeds_each_apertures_findings_verbatim(self, rendered, findings):
        for aperture_id, payload in findings.items():
            assert f"findings from aperture: {aperture_id}" in rendered
            # The whole object is embedded, not a summary of it.
            assert json.dumps(payload, indent=2, ensure_ascii=False) in rendered

    def test_names_the_degraded_apertures(self, rendered):
        """The manager must see the hole to mark it. Dormant/failed apertures are
        named next to the facts, not hidden."""
        assert "arena_scan:nrg1-fusion-solid-tumors" in rendered
        assert "apertures that failed" in rendered.lower()

    def test_embeds_the_real_grounded_facts(self, rendered):
        """The fixtures carry real HER3/HMBD-001 public facts, not inventions."""
        assert "HARMONi-6" in rendered
        assert "patritumab deruxtecan" in rendered


class TestCatalystQueueSnapshot:
    def test_points_at_the_per_program_path(self, rendered):
        assert "state/programs/hmbd-001/catalyst-queue.json" in rendered

    def test_omits_what_it_would_prove_so_the_manager_authors_it(self, rendered):
        """what_it_would_prove is thesis-gated interpretation — the manager
        authors it rather than copying the state's placeholder."""
        snapshot = rendered[
            rendered.index("## Catalyst queue snapshot") : rendered.index("## Prior quiet")
        ]
        assert "what_it_would_prove" not in snapshot


class TestPriorQuietCounts:
    def test_run_one_has_no_previous_issue(self, rendered):
        assert "(no previous issue)" in rendered
