"""Rendering the dossier-scan template — the fourth aperture kind (#92).

prompts/dossier-scan.md is a document ABOUT the template with the template
fenced inside it, exactly like every other prompt here; the fence is what we
render and the design notes stay out of the model's context.

What these tests hold the renderer to, in priority order:

1. **No placeholder survives.** A literal `{{existing_dossier}}` reaching a model
   is a silent instruction to invent one. Same check, same reason, as
   `tests/test_researcher_prompt_v2.py`.
2. **The window is absent, and stays absent.** This aperture is window-exempt
   (`Aperture.window_exempt`, #92), and the exemption is repealed the moment a
   renderer interpolates a window into it. The `RunContext` carries one; the
   prompt must not.
3. **Program-relative steering never reaches the render.** A dossier is shared
   across programs. Thesis slots, interests and a competitor roster steer ONE
   program's scan, and baking them into a shared record is the same mistake as
   lifting `read_through` off the relation edge (spec/03).
4. **A first sighting is never ambiguous with a failed render.** "(no dossier
   held — first scan)" is stated in words rather than left as a blank block.
5. **It cannot crash on adversarial input.** Null, prose, a list where a mapping
   was expected and a dict nested one level too deep have all shipped as bugs in
   this repo. A renderer that raises takes the run down before the scan that
   would have corrected the record ever ran.

No model is called: rendering is pure string work, and the autouse offline guard
in conftest holds regardless.

Spec: docs/spec/04-researchers.md, docs/spec/03-state-and-governance.md,
      https://github.com/cmengu/Research-Swarm/issues/92
"""

from __future__ import annotations

import pytest

from researchswarm.apertures import (
    DOSSIER_COST_CAP,
    Aperture,
    CostCap,
    dossier_aperture,
    plan_apertures,
)
from researchswarm.dossiers import build_company_dossier_record
from researchswarm.programs import load_program
from researchswarm.prompts import (
    NO_DOSSIER_HELD,
    UNKNOWN_FIELD,
    RunContext,
    UnresolvedPlaceholder,
    load_template,
    render_dossier_prompt,
)

RUN_ID = "run_20260719_0700"
AS_OF = "2026-07-19"
ENTITY_ID = "co_remegen"

# The manager's fields. A dossier is SHARED across programs, so these must have
# no slot here for the same reason they have none in a researcher's contract —
# and one stronger reason: a read-through written into a shared record is
# inherited by every program that later reads it (#92).
INTERPRETIVE_FIELDS = ("read_through", "thesis_bearing", "so_what", "priority")


@pytest.fixture
def template(repo_root):
    return load_template(repo_root / "prompts" / "dossier-scan.md")


@pytest.fixture
def ctx():
    """A context that DOES carry a coverage window, deliberately.

    Rendering against a window-free context would prove nothing: the interesting
    assertion is that a renderer holding a real window still declines to spend
    it, because the exemption is a property of the aperture and not of the run.
    """
    return RunContext(
        run_id=RUN_ID,
        coverage_window_from="2026-04-20",
        # Deliberately NOT `AS_OF`: the two dates must be distinguishable, or a
        # renderer that spent the window would pass by coincidence.
        coverage_window_to="2026-07-17",
    )


@pytest.fixture
def aperture():
    return dossier_aperture(ENTITY_ID, "first_sighting")


@pytest.fixture
def held_dossier():
    """A real prior record, built by the real writer rather than hand-shaped.

    Following the v2 publish tests: where the committed thing IS the subject, use
    the real thing. A hand-written fixture of the record shape would let the
    renderer and the writer drift apart without a test noticing.
    """
    record, _ = build_company_dossier_record(
        None,
        {
            "identity": {
                "legal_name": "RemeGen Co., Ltd.",
                "aliases": ["荣昌生物", "RemeGen"],
                "listings": [
                    {"exchange": "HKEX", "ticker": "9995"},
                    {"exchange": "SSE STAR", "ticker": "688331"},
                ],
                "status": "public",
            },
            "setbacks": [
                {
                    "date": "2023-03-02",
                    "kind": "discontinuation",
                    "detail": "registry record terminated; no press release issued",
                    "program": "second-line NSCLC asset",
                }
            ],
        },
        entity_id=ENTITY_ID,
        run_id="run_20260401_0700",
        date="2026-04-01",
        as_of="2026-04-01",
    )
    return record


@pytest.fixture
def first_scan(template, aperture, ctx):
    return render_dossier_prompt(template, aperture, as_of=AS_OF, ctx=ctx)


@pytest.fixture
def refresh(template, ctx, held_dossier):
    return render_dossier_prompt(
        template,
        dossier_aperture(ENTITY_ID, "refresh_due"),
        dossier=held_dossier,
        as_of=AS_OF,
        ctx=ctx,
    )


class TestLoadTemplate:
    def test_extracts_the_template_from_the_doc(self, template):
        """The file is a document about the template. Shipping the whole file
        would put the design rationale — and the render-time placeholder table —
        into the model's context."""
        assert template.startswith("You are a DOSSIER RESEARCHER")
        assert "Render-time placeholder notes" not in template
        assert "Design choices worth stating" not in template


class TestRendering:
    @pytest.mark.parametrize("which", ["first_scan", "refresh"])
    def test_no_placeholder_survives(self, which, request):
        """The load-bearing check. A leftover `{{...}}` is an invitation to
        invent, on a first scan and on a refresh alike."""
        rendered = request.getfixturevalue(which)
        assert "{{" not in rendered
        assert "}}" not in rendered

    def test_unknown_placeholder_raises_rather_than_shipping(self, aperture, ctx):
        with pytest.raises(UnresolvedPlaceholder, match="mystery"):
            render_dossier_prompt(
                "hello {{mystery}}", aperture, as_of=AS_OF, ctx=ctx
            )

    def test_names_the_company_the_run_planned_to_scan(self, first_scan):
        """`aperture.scope` IS the entity_id, so the company the run planned and
        the company the prompt names are provably the same value."""
        assert ENTITY_ID in first_scan

    def test_echoes_the_run_identity_and_as_of(self, first_scan):
        assert RUN_ID in first_scan
        assert AS_OF in first_scan

    def test_states_the_scan_trigger(self, first_scan, refresh):
        """The trigger is the audit trail, and it changes the model's job: a
        first build is not a refresh."""
        assert "first_sighting" in first_scan
        assert "refresh_due" in refresh


class TestWindowExemption:
    """The exemption is the whole reason this template is not the shared one.

    A seven-day window recently discarded a $1.1B platform acquisition (#92). A
    dossier's subject is history, so nothing here may be date-bounded — least of
    all by accident, via a renderer that had a window in hand and spent it.
    """

    def test_the_aperture_declares_the_exemption(self, aperture):
        assert aperture.window_exempt is True
        assert aperture.window_bounded is False

    @pytest.mark.parametrize("which", ["first_scan", "refresh"])
    def test_the_run_window_is_never_interpolated(self, which, request, ctx):
        rendered = request.getfixturevalue(which)
        assert ctx.coverage_window_from not in rendered
        assert ctx.coverage_window_to not in rendered

    def test_the_prompt_says_it_is_exempt_in_as_many_words(self, first_scan):
        """The model has seen window-bounded instructions in every other prompt
        in this repo and will assume the same unless told otherwise."""
        assert "EXEMPT FROM THE COVERAGE WINDOW" in first_scan


class TestFactsOnly:
    """A dossier is shared; an opinion is not (spec/03, #92)."""

    @pytest.mark.parametrize("field", INTERPRETIVE_FIELDS)
    def test_the_manager_fields_are_forbidden_by_name(self, field, first_scan):
        """Stated, not merely omitted: the pull toward "and this threatens us"
        is strongest in a prompt whose subject is a competitor."""
        assert field in first_scan

    def test_no_program_relative_steering_reaches_the_render(
        self, template, aperture, ctx, repo_root
    ):
        """The renderer takes no program, so the pilot's stance cannot leak.

        Asserted against the REAL committed program config rather than a
        fixture: if someone later adds a `{{thesis_slots}}` to this template and
        wires the pilot's stance in, this fails.
        """
        program = load_program(repo_root / "config", "hmbd-001")
        rendered = render_dossier_prompt(template, aperture, as_of=AS_OF, ctx=ctx)
        assert program.id not in rendered
        assert program.target not in rendered

    def test_the_dossier_renderer_is_not_the_researcher_renderer(self, repo_root):
        """v2-alongside-v1, and fourth-kind-alongside-the-other-three: the shared
        researcher template hard-codes the window it cannot repeal, so the three
        cycle apertures must stay window-bounded."""
        program = load_program(repo_root / "config", "hmbd-001")
        assert all(a.window_bounded for a in plan_apertures(program))


class TestExistingDossierBlock:
    def test_a_first_sighting_is_stated_not_blank(self, first_scan):
        """A blank block reads as "we hold nothing" — the right answer for a
        first sighting and a dangerous lie for a record we failed to load. The
        model cannot tell the two apart from an absence, so we say it."""
        assert NO_DOSSIER_HELD in first_scan

    def test_a_refresh_carries_what_we_already_hold(self, refresh):
        """Extend-don't-restate is only honourable if the model can see the
        current values."""
        assert NO_DOSSIER_HELD not in refresh
        assert "RemeGen Co., Ltd." in refresh
        assert "discontinuation" in refresh

    def test_identity_seeds_the_subject_header(self, refresh):
        """Aliases and listings are what let a scan find HKEX/CSRC disclosure
        filed under a romanisation we do not use — the rank-1 blind spot."""
        assert "荣昌生物" in refresh
        assert "HKEX:9995" in refresh
        assert "SSE STAR:688331" in refresh

    def test_prior_thin_sections_are_named_as_the_targets(self, refresh):
        """The refresh's highest-value work is where the last scan failed, and
        the marker is recomputed by the writer so it describes what we NOW hold."""
        assert "funding" in refresh
        assert "highest-value targets" in refresh

    def test_a_candidate_seeds_identity_on_a_first_sighting(
        self, template, aperture, ctx
    ):
        """On a first sighting there is no record and the discovery candidate is
        all there is."""
        rendered = render_dossier_prompt(
            template,
            aperture,
            candidate={"name": "Akeso, Inc.", "aliases": ["康方生物"]},
            as_of=AS_OF,
            ctx=ctx,
        )
        assert "Akeso, Inc." in rendered
        assert "康方生物" in rendered

    def test_the_held_record_outranks_the_candidate(
        self, template, ctx, held_dossier
    ):
        """The propagation contract applied to identity: an established,
        provenanced name outranks whatever a discovery finding happened to
        spell. The scan can still correct it — that is the `corrects` path."""
        rendered = render_dossier_prompt(
            template,
            dossier_aperture(ENTITY_ID, "refresh_due"),
            dossier=held_dossier,
            candidate={"name": "Remegen (misspelled by discovery)"},
            as_of=AS_OF,
            ctx=ctx,
        )
        assert "RemeGen Co., Ltd." in rendered
        assert "misspelled by discovery" not in rendered

    def test_linked_assets_render_from_both_directions(
        self, template, ctx, held_dossier
    ):
        """The store splits by kind, so an asset reaches its company two ways:
        forward via the dossier's pipeline, backward via an asset record's
        `held_by`. The union means an asset the pipeline has not caught up with
        is still visible to the scan."""
        rendered = render_dossier_prompt(
            template,
            dossier_aperture(ENTITY_ID, "refresh_due"),
            dossier=held_dossier,
            assets=["rc48_disitamab_vedotin"],
            as_of=AS_OF,
            ctx=ctx,
        )
        assert "rc48_disitamab_vedotin" in rendered


class TestCostCap:
    """History search is unbounded by nature; the ceiling must reach the model."""

    def test_the_apertures_cap_is_what_the_prompt_states(self, template, ctx):
        rendered = render_dossier_prompt(
            template,
            dossier_aperture(ENTITY_ID, "first_sighting", cost_cap=CostCap(7, 11)),
            as_of=AS_OF,
            ctx=ctx,
        )
        assert "hard cap of 7 tool turns" in rendered

    def test_the_default_cap_is_stated_when_none_was_declared(self, template, ctx):
        """An aperture built by hand may carry no cap. Rendering "None" into a
        budget section reads as "unbounded" — the exact failure the cap exists
        to prevent."""
        rendered = render_dossier_prompt(
            template,
            Aperture(id="x", kind="dossier_scan", scope=ENTITY_ID, active=True),
            as_of=AS_OF,
            ctx=ctx,
        )
        assert f"hard cap of {DOSSIER_COST_CAP.max_searches} tool turns" in rendered
        assert "hard cap of None" not in rendered


class TestAdversarialInput:
    """The failure mode this repo has shipped five times.

    Every argument below is machine-assembled from state files or model output.
    A renderer that raises on a malformed prior record takes the run down BEFORE
    the scan that would have corrected the record ever runs — strictly worse
    than rendering an honest "(unknown — establish it)".
    """

    @pytest.mark.parametrize(
        "dossier",
        [
            None,
            "the dossier is in the other file",
            [],
            [{"identity": {}}],
            0,
            {},
            {"facts": None},
            {"facts": "prose"},
            {"facts": []},
            {"facts": {"identity": None}},
            {"facts": {"identity": "prose"}},
            {"facts": {"identity": {"value": None}}},
            {"facts": {"identity": {"value": "prose"}}},
            {"facts": {"identity": {"value": {"aliases": "not a list"}}}},
            {"facts": {"identity": {"value": {"listings": "not a list"}}}},
            {"facts": {"identity": {"value": {"listings": ["not a mapping"]}}}},
            {"facts": {"identity": {"value": {"listings": [{"exchange": None}]}}}},
            # a dict nested one level too deep — the classic
            {"facts": {"facts": {"identity": {"value": {"legal_name": "x"}}}}},
            {"facts": {"pipeline": {"value": "prose"}}},
            {"facts": {"pipeline": {"value": [None, "prose", {}]}}},
            {"coverage": "prose"},
            {"coverage": {"thin_sections": "funding"}},
            {"coverage": {"thin_sections": [None, 3]}},
            {"as_of": 20260401, "version": "many"},
        ],
    )
    def test_a_malformed_prior_record_never_crashes(
        self, template, aperture, ctx, dossier
    ):
        rendered = render_dossier_prompt(
            template, aperture, dossier=dossier, as_of=AS_OF, ctx=ctx
        )
        assert "{{" not in rendered
        assert ENTITY_ID in rendered

    @pytest.mark.parametrize(
        "candidate", [None, "Akeso", [], ["Akeso"], 7, {"name": None}, {"aliases": 3}]
    )
    def test_a_malformed_candidate_never_crashes(
        self, template, aperture, ctx, candidate
    ):
        rendered = render_dossier_prompt(
            template, aperture, candidate=candidate, as_of=AS_OF, ctx=ctx
        )
        assert "{{" not in rendered

    @pytest.mark.parametrize(
        "assets", [None, "rc48", ["rc48", None, 3, ""], (), {"rc48"}, 9, {"a": "b"}]
    )
    def test_a_malformed_asset_list_never_crashes(
        self, template, aperture, ctx, assets
    ):
        rendered = render_dossier_prompt(
            template, aperture, assets=assets, as_of=AS_OF, ctx=ctx
        )
        assert "{{" not in rendered

    @pytest.mark.parametrize("as_of", [None, "", 20260719, [], {}])
    def test_a_missing_as_of_renders_the_unknown_marker_not_a_blank(
        self, template, aperture, ctx, as_of
    ):
        """A blank after `as_of:` reads to a model as a rendering bug and invites
        it to invent one."""
        rendered = render_dossier_prompt(
            template, aperture, as_of=as_of, ctx=ctx
        )
        assert UNKNOWN_FIELD in rendered
        assert "{{" not in rendered

    def test_an_aperture_missing_its_fields_never_crashes(self, template, ctx):
        """A duck-typed aperture from a stubbed planner still renders."""

        class Bare:
            pass

        rendered = render_dossier_prompt(template, Bare(), as_of=AS_OF, ctx=ctx)
        assert "{{" not in rendered
        assert UNKNOWN_FIELD in rendered
