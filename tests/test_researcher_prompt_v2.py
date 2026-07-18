"""Rendering the v2 researcher template — one template, N apertures (spec/04).

The pivot replaced the six fixed beats with APERTURES (biology_scan, arena_scan,
house_sweep), but the template pattern survived: apertures differ in SCOPE, never
in RULES. So the load-bearing tests here are (1) that the scope block is the ONE
thing that changes across the three kinds — biology carries target+moa, arena
carries one indication, house carries the two lenses — and (2) that every rule
below the scope holds for all three: the read-only wall, the coverage duty, the
findings contract, and the deliberate ABSENCE of the manager's fields
(read_through / thesis_bearing / so_what).

These exercise `render_researcher_prompt_v2` against the REAL pilot config
(config/programs/hmbd-001.toml, config/interests.toml, state/thesis.json) via
`plan_apertures`, so a drift between the authored config and the renderer fails
here. No live model is called — that is the parent's job (spec/04 transport).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchswarm.apertures import plan_apertures
from researchswarm.calendar import SurgeState
from researchswarm.programs import (
    load_edges,
    load_interests,
    load_program,
)
from researchswarm.prompts import (
    RunContext,
    UnresolvedPlaceholder,
    load_template,
    render_researcher_prompt_v2,
)

RUN_ID = "run_20260718_0700"
DORMANT_MARKER = "(no stance seeded)"

# The three fields that are the MANAGER's — a researcher's contract has no slot
# for them and the template must forbid them by name (spec/04 "why it isn't
# issue.json-shaped").
MANAGER_ONLY_FIELDS = ("read_through", "thesis_bearing", "so_what")


@pytest.fixture
def program(repo_root):
    return load_program(repo_root / "config", "hmbd-001")


@pytest.fixture
def interests(repo_root):
    return load_interests(repo_root / "config")


@pytest.fixture
def edges(repo_root):
    return load_edges(repo_root / "state", "hmbd-001")


@pytest.fixture
def thesis(repo_root):
    return json.loads((repo_root / "state" / "thesis.json").read_text())


@pytest.fixture
def ctx():
    return RunContext(
        run_id=RUN_ID,
        coverage_window_from="2026-07-14",
        coverage_window_to="2026-07-18",
    )


@pytest.fixture
def template(repo_root):
    return load_template(repo_root / "prompts" / "researcher-v2.md")


@pytest.fixture
def apertures(program):
    return plan_apertures(program)


def _aperture(apertures, kind_or_id):
    """The aperture whose id OR kind matches — arena is keyed by its colon id."""
    for aperture in apertures:
        if kind_or_id in (aperture.id, aperture.kind):
            return aperture
    raise AssertionError(f"no aperture matching {kind_or_id!r} in {apertures}")


def _render(template, aperture, program, interests, edges, thesis, ctx):
    return render_researcher_prompt_v2(
        template,
        aperture,
        program=program,
        interests=interests,
        edges=edges,
        thesis=thesis,
        ctx=ctx,
    )


@pytest.fixture
def biology(template, apertures, program, interests, edges, thesis, ctx):
    return _render(template, _aperture(apertures, "biology_scan"),
                   program, interests, edges, thesis, ctx)


@pytest.fixture
def arena(template, apertures, program, interests, edges, thesis, ctx):
    return _render(template, _aperture(apertures, "arena_scan:squamous-nsclc"),
                   program, interests, edges, thesis, ctx)


@pytest.fixture
def house(template, apertures, program, interests, edges, thesis, ctx):
    return _render(template, _aperture(apertures, "house_sweep"),
                   program, interests, edges, thesis, ctx)


class TestLoadTemplate:
    def test_extracts_the_v2_template_from_the_doc(self, repo_root):
        """researcher-v2.md is a document ABOUT the template with the template
        fenced inside it. Rendering the whole file would ship the design notes."""
        template = load_template(repo_root / "prompts" / "researcher-v2.md")
        assert template.startswith("You are a RESEARCHER")
        assert "Render-time placeholder notes" not in template
        assert "Design choices worth stating" not in template


class TestRendering:
    """The renders must be clean for EVERY aperture kind — a leftover placeholder
    is a silent instruction to invent, and it must never reach any of the three."""

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_no_placeholder_survives(self, which, request):
        rendered = request.getfixturevalue(which)
        assert "{{" not in rendered
        assert "}}" not in rendered

    def test_unknown_placeholder_raises_rather_than_shipping(
        self, apertures, program, interests, edges, thesis, ctx
    ):
        with pytest.raises(UnresolvedPlaceholder, match="mystery"):
            _render("hello {{mystery}}", _aperture(apertures, "biology_scan"),
                    program, interests, edges, thesis, ctx)

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_echoes_the_run_context(self, which, request):
        rendered = request.getfixturevalue(which)
        assert RUN_ID in rendered
        assert "2026-07-14" in rendered
        assert "2026-07-18" in rendered

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_carries_the_program_subject(self, which, request):
        """Every aperture is the detective for the SAME program — its identity
        (and the load-bearing moa) reaches all three."""
        rendered = request.getfixturevalue(which)
        assert "hmbd-001" in rendered
        assert "HER3 (ERBB3)" in rendered
        assert "signalling_blockade" in rendered


class TestScopeIsTheOnlyThingThatChanges:
    """The whole point of "one template, N apertures": the SCOPE block differs,
    every rule below it is identical. These pin the three scopes to their kinds."""

    def test_biology_scope_is_target_plus_moa_indication_blind(self, biology):
        assert "BIOLOGY SCAN" in biology
        assert "INDICATION-BLIND" in biology
        assert "target=HER3 (ERBB3), moa=signalling_blockade" in biology
        assert "mechanism twins" in biology
        assert "target twins" in biology
        # The findings `aperture` field echoes the aperture id.
        assert '"aperture": "biology_scan"' in biology

    def test_arena_scope_names_the_one_indication(self, arena):
        assert "ARENA SCAN" in arena
        assert "squamous-nsclc" in arena
        assert "setting rivals" in arena
        assert "benchmark / SOC" in arena
        assert '"aperture": "arena_scan:squamous-nsclc"' in arena

    def test_house_scope_carries_the_two_lenses_and_blind_spots(self, house):
        assert "HOUSE SWEEP" in house
        assert "partnership_bd" in house
        assert "threat_financing" in house
        assert "blind-spot detection" in house
        # Discovery is folded into the house sweep, not a separate agent (spec/04).
        assert "Discovery is FOLDED IN" in house
        assert '"aperture": "house_sweep"' in house

    def test_house_lens_is_required_for_house_only(self, biology, arena, house):
        """house_lens is a house_sweep-only field — required there, null for the
        others. The scope block, not a bare comment, must ground that."""
        assert "house_lens is REQUIRED" in house
        assert "house_lens stays null for this aperture" in biology
        assert "house_lens stays null for this aperture" in arena


class TestTheReadOnlyWallAndNoManagerFields:
    """A researcher reports FACTS, deliberately NOT issue.json-shaped. The
    manager's fields have no slot in the contract, and the template forbids them
    by name — the same duty holds for all three apertures."""

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_forbids_the_manager_only_fields_by_name(self, which, request):
        rendered = request.getfixturevalue(which)
        for field in MANAGER_ONLY_FIELDS:
            assert field in rendered  # named so it can be forbidden
        assert "MANAGER's and have NO slot in your contract" in rendered

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_read_only_wall_is_stated(self, which, request):
        rendered = request.getfixturevalue(which)
        assert "no write access" in rendered
        assert "report FACTS" in rendered or "report facts" in rendered

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_priority_hint_is_the_only_triage_hint_and_within_aperture(
        self, which, request
    ):
        rendered = request.getfixturevalue(which)
        assert "priority_hint" in rendered
        assert "within-aperture" in rendered.lower() or "WITHIN this aperture" in rendered

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_a_researcher_proposes_but_never_writes_an_edge(self, which, request):
        rendered = request.getfixturevalue(which)
        assert "proposed_entity" in rendered
        assert "proposed_relation" in rendered
        assert "never write an edge" in rendered.lower() or "NEVER write an edge" in rendered or "never an edge" in rendered


class TestTheCoverageDuty:
    """Every typed competitor and every strong-tier interest in scope must be
    checked and recorded either way (spec/04 "a coverage duty")."""

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_states_the_coverage_duty(self, which, request):
        rendered = request.getfixturevalue(which)
        assert "COVERAGE DUTY" in rendered
        assert "coverage_notes.entities_checked" in rendered

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_renders_the_seed_competitors_untyped_at_cold_start(self, which, request):
        """At seed no edges exist, so the roster is exactly the cold-start seed
        competitors, each marked untyped so the researcher knows to still cover
        them even though the manager has not typed them."""
        rendered = request.getfixturevalue(which)
        assert "asset_her3_dxd · (seed — untyped)" in rendered
        assert "asset_ivonescimab · (seed — untyped)" in rendered

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_renders_the_interest_notes_and_tiers(self, which, request, interests):
        rendered = request.getfixturevalue(which)
        for interest in interests.interests:
            assert interest.note in rendered
            assert interest.tier in rendered


class TestTheFindingsContract:
    """The findings.json contract, per spec/04 — aperture-scoped fields, no
    manager fields, coverage_notes always present."""

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_carries_the_contract_fields(self, which, request):
        rendered = request.getfixturevalue(which)
        for field in (
            '"aperture"', '"program_id"', '"quiet"', '"findings"',
            '"entity_ids"', '"house_lens"', '"registry_delta"',
            '"catalyst_refs"', '"priority_hint"', '"unconfirmed"',
            '"coverage_notes"', '"errors"',
        ):
            assert field in rendered

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_coverage_notes_is_always_required(self, which, request):
        rendered = request.getfixturevalue(which)
        assert "coverage_notes is ALWAYS required" in rendered

    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_sourcing_rules_are_present(self, which, request):
        rendered = request.getfixturevalue(which)
        for tier in ("primary", "trade", "aggregator"):
            assert tier in rendered
        assert "aggregator can never be the only source" in rendered
        # Sources are objects with all four fields.
        assert "published_at" in rendered


class TestThesisLens:
    def test_stance_text_is_never_baked_into_the_template_file(self, repo_root, thesis):
        """The propagation contract binds the researcher too: a template that
        inlined a stance would chase the old worldview after an owner edit."""
        raw = (repo_root / "prompts" / "researcher-v2.md").read_text()
        for belief in thesis["beliefs"]:
            if belief["stance"]:
                assert belief["stance"] not in raw

    def test_stances_are_read_fresh_into_the_render(self, biology, thesis):
        for belief in thesis["beliefs"]:
            if belief["stance"]:
                assert belief["stance"] in biology

    def test_thesis_is_framed_as_a_lens_not_a_conclusion(self, biology):
        assert "ATTENTION LENS, not a conclusion" in biology

    def test_dormant_slot_renders_the_marker(
        self, template, apertures, program, interests, edges, thesis, ctx
    ):
        """A dormant slot shows the marker, not an invented stance."""
        thesis["beliefs"][0]["stance"] = None
        out = _render(template, _aperture(apertures, "biology_scan"),
                      program, interests, edges, thesis, ctx)
        assert DORMANT_MARKER in out


class TestTheCatalystQueueStandingDuty:
    @pytest.mark.parametrize("which", ["biology", "arena", "house"])
    def test_states_the_standing_duty(self, which, request):
        rendered = request.getfixturevalue(which)
        assert "catalyst queue" in rendered.lower()
        assert "catalyst_refs" in rendered
        for transition in ("DELIVERED", "SLIPPED", "DIED"):
            assert transition in rendered


class TestSurgeCarveOut:
    def test_baseline_run_has_no_carve_out(self, biology):
        assert "No carve-outs." in biology

    def test_surge_window_is_carved_out(
        self, template, apertures, program, interests, edges, thesis
    ):
        """Inside a surge window, an in-window story that lands outside the
        narrowed coverage window is fair game — the same carve-out the v1
        researcher gets, reused so the two never disagree on in-window."""
        surge = SurgeState(
            window="ESMO 2026", window_id="esmo-2026", day=2, of=4,
            starts="2026-10-17", ends="2026-10-21",
        )
        ctx = RunContext(
            run_id=RUN_ID,
            coverage_window_from="2026-10-18",
            coverage_window_to="2026-10-18",
            surge=surge,
        )
        out = _render(template, _aperture(apertures, "biology_scan"),
                      program, interests, edges, thesis, ctx)
        assert "ESMO 2026" in out
        assert "Carve-out:" in out
