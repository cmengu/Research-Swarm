"""The aperture roster planner (spec/04) and the dossier scan (spec #92).

Derived from the REAL pilot program config, so a drift between the config and the
`1 + N + 1` roster fails here.

The dossier half asserts external behaviour at the planning seam — which
apertures come back for a given (company set, dossier state, clock, event set) —
never how the decision was reached. Everything is injected: there is no clock
read, no state-directory read, and no model call anywhere in this file.

One class of test carries extra weight here: ADVERSARIAL SHAPE. A planner that
raises on null / prose / wrong container / wrong depth takes the run down after
the cycle's real intelligence was already gathered, which is strictly worse than
one that plans a redundant scan. That bug has shipped five times in this repo.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from researchswarm.apertures import (
    ARENA_SCAN,
    BIOLOGY_SCAN,
    DOSSIER_COST_CAP,
    DOSSIER_REFRESH_DAYS,
    DOSSIER_SCAN,
    DOSSIER_SCAN_COST_CAPPED,
    DOSSIER_TRIGGER_FIRST_SIGHTING,
    DOSSIER_TRIGGER_MATERIAL_EVENT,
    DOSSIER_TRIGGER_REFRESH_DUE,
    HOUSE_SWEEP,
    Aperture,
    CostCap,
    active_apertures,
    cap_receipt,
    company_ids_from_entities,
    company_ids_from_holders,
    company_entity_id,
    dossier_aperture,
    dossier_trigger,
    plan_apertures,
    plan_dossier_scans,
)
from researchswarm.programs import load_program

CONFIG = Path(__file__).resolve().parents[1] / "config"


def _pilot():
    return load_program(CONFIG, "hmbd-001")


class TestTheRoster:
    def test_it_is_one_biology_n_arena_one_house(self):
        apertures = plan_apertures(_pilot())
        kinds = [a.kind for a in apertures]
        # 1 biology + 2 indications + 1 house = 4
        assert kinds == [BIOLOGY_SCAN, ARENA_SCAN, ARENA_SCAN, HOUSE_SWEEP]

    def test_the_biology_scan_carries_target_and_moa(self):
        biology = plan_apertures(_pilot())[0]
        assert biology.id == BIOLOGY_SCAN
        assert "HER3 (ERBB3)" in biology.scope
        assert "signalling_blockade" in biology.scope
        assert biology.active

    def test_an_arena_scan_per_indication_keyed_by_indication(self):
        apertures = plan_apertures(_pilot())
        arena_ids = [a.id for a in apertures if a.kind == ARENA_SCAN]
        assert arena_ids == [
            "arena_scan:squamous-nsclc",
            "arena_scan:nrg1-fusion-solid-tumors",
        ]

    def test_the_house_sweep_is_fixed_and_last(self):
        house = plan_apertures(_pilot())[-1]
        assert house.id == HOUSE_SWEEP
        assert house.active


class TestDormancy:
    def test_a_priority_indication_arena_scan_is_dormant(self):
        by_id = {a.id: a for a in plan_apertures(_pilot())}
        # squamous-nsclc is active_arena; nrg1 is priority_indication → dormant
        assert by_id["arena_scan:squamous-nsclc"].active
        assert by_id["arena_scan:nrg1-fusion-solid-tumors"].dormant

    def test_active_apertures_excludes_the_dormant_arena(self):
        active = active_apertures(_pilot())
        active_ids = {a.id for a in active}
        assert "arena_scan:nrg1-fusion-solid-tumors" not in active_ids
        # the real agent count this cycle: biology + 1 active arena + house
        assert len(active) == 3

    def test_active_apertures_is_a_subset_of_the_full_roster(self):
        full = {a.id for a in plan_apertures(_pilot())}
        active = {a.id for a in active_apertures(_pilot())}
        assert active <= full


# ---------------------------------------------------------------------------
# dossier_scan — the fourth kind (spec #92)
# ---------------------------------------------------------------------------

TODAY = date(2026, 7, 19)
FRESH = "2026-07-01"  # inside the quarterly dial
STALE = "2026-01-01"  # well past it


def _dossier(as_of: str) -> dict:
    return {"entity_id": "co_remegen", "kind": "company", "as_of": as_of}


class TestTheDossierIsNotOnTheCycleRoster:
    """It triggers on sighting / dial / event, so it must not appear per cycle."""

    def test_plan_apertures_is_unchanged_and_carries_no_dossier_scan(self):
        kinds = [a.kind for a in plan_apertures(_pilot())]
        assert kinds == [BIOLOGY_SCAN, ARENA_SCAN, ARENA_SCAN, HOUSE_SWEEP]
        assert DOSSIER_SCAN not in kinds

    def test_active_apertures_carries_no_dossier_scan_either(self):
        assert all(a.kind != DOSSIER_SCAN for a in active_apertures(_pilot()))

    def test_a_known_fresh_company_plans_no_scan_this_cycle(self):
        planned = plan_dossier_scans(
            ["co_remegen"], dossiers={"co_remegen": _dossier(FRESH)}, today=TODAY
        )
        assert planned == []


class TestTheTwoLoadBearingDifferences:
    """Both must be askable ON the aperture, not special-cased by the caller."""

    def test_a_dossier_scan_is_window_exempt(self):
        scan = dossier_aperture("co_remegen", DOSSIER_TRIGGER_FIRST_SIGHTING)
        assert scan.window_exempt
        assert not scan.window_bounded

    def test_every_cycle_aperture_stays_window_bounded(self):
        for aperture in plan_apertures(_pilot()):
            assert aperture.window_bounded
            assert not aperture.window_exempt

    def test_a_dossier_scan_declares_an_explicit_cost_cap(self):
        scan = dossier_aperture("co_remegen", DOSSIER_TRIGGER_FIRST_SIGHTING)
        assert scan.cost_cap == DOSSIER_COST_CAP
        assert scan.cost_cap.max_searches > 0
        assert scan.cost_cap.max_sources > 0

    def test_a_cycle_aperture_declares_no_cost_cap(self):
        assert all(a.cost_cap is None for a in plan_apertures(_pilot()))

    def test_the_id_and_kind_follow_the_house_slug_convention(self):
        scan = dossier_aperture("co_remegen", DOSSIER_TRIGGER_FIRST_SIGHTING)
        assert scan.id == "dossier_scan:co_remegen"
        assert scan.kind == DOSSIER_SCAN
        assert scan.scope == "co_remegen"
        assert scan.active


class TestTheThreeTriggers:
    def test_first_sighting_of_an_unknown_company(self):
        planned = plan_dossier_scans(["co_akeso"], dossiers={}, today=TODAY)
        assert [a.id for a in planned] == ["dossier_scan:co_akeso"]
        assert planned[0].trigger == DOSSIER_TRIGGER_FIRST_SIGHTING

    def test_the_slow_dial_fires_on_a_stale_record(self):
        planned = plan_dossier_scans(
            ["co_remegen"], dossiers={"co_remegen": _dossier(STALE)}, today=TODAY
        )
        assert [a.trigger for a in planned] == [DOSSIER_TRIGGER_REFRESH_DUE]

    def test_the_dial_default_is_quarterly(self):
        assert DOSSIER_REFRESH_DAYS == 91
        just_inside = TODAY.replace(day=18).toordinal() - (DOSSIER_REFRESH_DAYS - 1)
        as_of = date.fromordinal(just_inside).isoformat()
        assert dossier_trigger(
            "co_remegen", dossiers={"co_remegen": _dossier(as_of)}, today=TODAY
        ) in (None, DOSSIER_TRIGGER_REFRESH_DUE)
        old = date.fromordinal(TODAY.toordinal() - DOSSIER_REFRESH_DAYS).isoformat()
        assert (
            dossier_trigger(
                "co_remegen", dossiers={"co_remegen": _dossier(old)}, today=TODAY
            )
            == DOSSIER_TRIGGER_REFRESH_DUE
        )

    def test_a_material_event_does_not_wait_for_the_dial(self):
        planned = plan_dossier_scans(
            ["co_remegen"],
            dossiers={"co_remegen": _dossier(FRESH)},
            today=TODAY,
            material_events=["co_remegen"],
        )
        assert [a.trigger for a in planned] == [DOSSIER_TRIGGER_MATERIAL_EVENT]

    def test_a_material_event_may_arrive_as_an_event_mapping(self):
        planned = plan_dossier_scans(
            ["co_remegen"],
            dossiers={"co_remegen": _dossier(FRESH)},
            today=TODAY,
            material_events=[{"entity_id": "co_remegen", "kind": "M&A"}],
        )
        assert [a.trigger for a in planned] == [DOSSIER_TRIGGER_MATERIAL_EVENT]

    def test_first_sighting_outranks_a_material_event(self):
        # Saying "refresh" or "event" of a record that never existed would be a
        # lie in the receipt: there is nothing to refresh.
        trigger = dossier_trigger(
            "co_akeso", dossiers={}, today=TODAY, material_events=["co_akeso"]
        )
        assert trigger == DOSSIER_TRIGGER_FIRST_SIGHTING


class TestThePlannedRoster:
    def test_it_plans_one_scan_per_company_in_caller_order(self):
        planned = plan_dossier_scans(
            ["co_akeso", "co_hengrui", "co_shengdi"], dossiers={}, today=TODAY
        )
        assert [a.scope for a in planned] == ["co_akeso", "co_hengrui", "co_shengdi"]

    def test_it_deduplicates_a_company_named_twice(self):
        planned = plan_dossier_scans(["co_akeso", "co_akeso"], dossiers={}, today=TODAY)
        assert len(planned) == 1

    def test_the_cost_cap_is_injectable_per_run(self):
        planned = plan_dossier_scans(
            ["co_akeso"], dossiers={}, today=TODAY, cost_cap=CostCap(2, 3)
        )
        assert planned[0].cost_cap == CostCap(2, 3)

    def test_no_companies_plans_nothing(self):
        assert plan_dossier_scans([], dossiers={}, today=TODAY) == []


class TestDiscoveryFeedsTheRoster:
    def test_company_records_are_picked_out_of_the_shared_fact_layer(self):
        entities = {
            "co_remegen": {"kind": "company"},
            "asset_her3_dxd": {"kind": "asset"},
        }
        assert company_ids_from_entities(entities) == ["co_remegen"]

    def test_a_legacy_record_without_a_kind_falls_back_to_the_id_convention(self):
        assert company_ids_from_entities({"co_legacy": {"name": "Legacy"}}) == [
            "co_legacy"
        ]

    def test_a_newly_discovered_company_queues_its_own_first_scan(self):
        ids = company_ids_from_entities({}, extra=["co_shengdi"])
        planned = plan_dossier_scans(ids, dossiers={}, today=TODAY)
        assert [a.trigger for a in planned] == [DOSSIER_TRIGGER_FIRST_SIGHTING]


class TestResolvingACompanyNameToAnId:
    """`company_entity_id` is identity resolution from prose — the weakest link in
    the entity spine, so its rules are pinned here rather than left to a regex."""

    def test_the_spec_example_resolves(self):
        assert company_entity_id("RemeGen Co., Ltd.") == "co_remegen"

    def test_the_same_company_written_two_ways_merges(self):
        """The merge that matters: two issues naming one company must not mint
        two records and therefore two dossiers."""
        assert company_entity_id("RemeGen") == company_entity_id("RemeGen Co., Ltd.")

    def test_a_multiword_name_keeps_its_words(self):
        assert company_entity_id("Daiichi Sankyo") == "co_daiichi_sankyo"

    def test_industry_words_are_not_stripped(self):
        """Only LEGAL forms are stripped. Dropping "Pharma" would merge companies
        that differ only by it."""
        assert company_entity_id("Jiangsu Hengrui Pharma") == "co_jiangsu_hengrui_pharma"

    def test_two_different_mercks_stay_distinct(self):
        """The known limit, asserted rather than hidden: a wrong merge puts two
        companies' histories in one record, which is worse than a duplicate."""
        assert company_entity_id("Merck & Co.") != company_entity_id("Merck KGaA")

    def test_a_legal_form_is_stripped_only_from_the_tail(self):
        assert company_entity_id("Limited Brands") == "co_limited_brands"

    def test_a_name_that_identifies_nothing_returns_none(self):
        assert company_entity_id("Ltd.") is None
        assert company_entity_id("  ") is None
        assert company_entity_id(None) is None


class TestHoldersMakeTheApertureReachable:
    """Without this path the planner ranges over companies, the roster holds only
    assets, and no cycle can ever plan a scan — built, tested, unreachable."""

    def _asset(self, holders):
        return {"asset_rc148": {"kind": "asset", "facts": {"holders": {"value": holders}}}}

    def test_an_assets_holders_become_company_candidates(self):
        ids = company_ids_from_holders(self._asset(["RemeGen Co., Ltd.", "AbbVie"]))
        assert ids == ["co_remegen", "co_abbvie"]

    def test_a_held_asset_queues_its_holders_first_scan(self):
        ids = company_ids_from_entities(
            self._asset(["RemeGen Co., Ltd."]),
            extra=company_ids_from_holders(self._asset(["RemeGen Co., Ltd."])),
        )
        planned = plan_dossier_scans(ids, dossiers={}, today=TODAY)
        assert [a.scope for a in planned] == ["co_remegen"]
        assert [a.trigger for a in planned] == [DOSSIER_TRIGGER_FIRST_SIGHTING]

    def test_a_resolved_held_by_link_is_taken_as_an_id_verbatim(self):
        """`held_by` already IS an entity_id. Re-slugging it would point at a
        different id than the record it links to."""
        entities = {"a": {"facts": {"held_by": {"value": "co_remegen"}}}}
        assert company_ids_from_holders(entities) == ["co_remegen"]

    def test_two_assets_with_one_holder_yield_one_candidate(self):
        entities = {
            "a1": {"facts": {"holders": {"value": ["AbbVie"]}}},
            "a2": {"facts": {"holders": {"value": ["AbbVie Inc."]}}},
        }
        assert company_ids_from_holders(entities) == ["co_abbvie"]

    def test_hostile_input_yields_no_candidates_rather_than_raising(self):
        for hostile in (None, "prose", {"a": "prose"}, {"a": {"facts": {"holders": 7}}}):
            assert company_ids_from_holders(hostile) == []


class TestTheCostCapReceipt:
    """The cap fires on the TRANSPORT ENVELOPE's accounting — turns and dollars
    the orchestrator parsed for itself — never on a `spend` the model reported.
    Spec/06 admission test 2: a degradation is exempt-able only when its trigger
    is mechanically detectable from facts the orchestrator holds, and the scan
    that blew its budget is the last witness to trust about it."""

    def test_staying_inside_the_cap_writes_no_receipt(self):
        scan = dossier_aperture("co_akeso", DOSSIER_TRIGGER_FIRST_SIGHTING)
        assert cap_receipt(scan, {"turns": 1, "usd": 0.10}) is None

    def test_exceeding_the_turn_cap_degrades_with_a_receipt_naming_the_numbers(self):
        scan = dossier_aperture(
            "co_akeso", DOSSIER_TRIGGER_FIRST_SIGHTING, cost_cap=CostCap(5, 5, 2.0)
        )
        receipt = cap_receipt(scan, {"turns": 9, "usd": 0.5})
        assert receipt["degradation"] == DOSSIER_SCAN_COST_CAPPED
        assert receipt["aperture"] == "dossier_scan:co_akeso"
        assert receipt["exceeded"] == ["turns"]
        assert receipt["cap"]["turns"] == 5
        assert receipt["spend"]["turns"] == 9

    def test_exceeding_the_dollar_cap_degrades_too(self):
        scan = dossier_aperture(
            "co_akeso", DOSSIER_TRIGGER_FIRST_SIGHTING, cost_cap=CostCap(5, 5, 2.0)
        )
        receipt = cap_receipt(scan, {"turns": 1, "usd": 7.25})
        assert receipt["exceeded"] == ["usd"]
        assert receipt["spend"]["usd"] == 7.25

    def test_a_model_reported_spend_can_never_fire_the_cap(self):
        """The defect this replaced: the cap read `findings["spend"]`, which
        nothing produced and nothing may produce. Model-authored keys are not
        an input here at all."""
        scan = dossier_aperture(
            "co_akeso", DOSSIER_TRIGGER_FIRST_SIGHTING, cost_cap=CostCap(5, 5, 2.0)
        )
        assert cap_receipt(scan, {"searches": 9_999, "sources": 9_999}) is None

    def test_an_uncapped_cycle_aperture_can_never_be_capped(self):
        house = plan_apertures(_pilot())[-1]
        assert cap_receipt(house, {"turns": 10_000, "usd": 10_000.0}) is None


class TestAdversarialShape:
    """A planner that crashes is strictly worse than one that misses."""

    @pytest.mark.parametrize(
        "companies",
        [
            None,
            "co_remegen",  # a bare string is ONE id, not eleven characters
            [None, 3, "", "  ", {"nested": "dict"}, ["deeper"]],
            {"co_remegen": {"prose": "not a list"}},
            42,
            object(),
        ],
    )
    def test_it_never_crashes_on_a_malformed_company_set(self, companies):
        planned = plan_dossier_scans(companies, dossiers={}, today=TODAY)
        assert isinstance(planned, list)
        assert all(isinstance(a, Aperture) for a in planned)

    def test_a_bare_string_reads_as_one_company(self):
        planned = plan_dossier_scans("co_remegen", dossiers={}, today=TODAY)
        assert [a.scope for a in planned] == ["co_remegen"]

    @pytest.mark.parametrize(
        "dossiers",
        [
            None,
            "we have a dossier, trust me",
            ["co_remegen"],
            {"co_remegen": None},
            {"co_remegen": "prose where a record was expected"},
            {"co_remegen": ["wrong", "container"]},
            42,
        ],
    )
    def test_an_unreadable_dossier_store_reads_as_first_sighting(self, dossiers):
        planned = plan_dossier_scans(["co_remegen"], dossiers=dossiers, today=TODAY)
        assert [a.trigger for a in planned] == [DOSSIER_TRIGGER_FIRST_SIGHTING]

    def test_a_record_nested_one_level_too_deep_still_plans_a_scan(self):
        # The `as_of` is there, but not where the contract says it is. The scan
        # must be planned anyway — a record we cannot date is never fresh.
        planned = plan_dossier_scans(
            ["co_remegen"],
            dossiers={"co_remegen": {"dossier": {"as_of": "2026-07-01"}}},
            today=TODAY,
        )
        assert [a.trigger for a in planned] == [DOSSIER_TRIGGER_REFRESH_DUE]

    @pytest.mark.parametrize(
        "as_of", [None, "", "not a date", "2026-13-45", 20260701, ["2026-07-01"], {}]
    )
    def test_an_unknowable_as_of_reads_as_stale_never_as_fresh(self, as_of):
        # Staleness is the safe direction: age must never be mistaken for
        # absence of activity (story 16).
        trigger = dossier_trigger(
            "co_remegen",
            dossiers={"co_remegen": {"kind": "company", "as_of": as_of}},
            today=TODAY,
        )
        assert trigger == DOSSIER_TRIGGER_REFRESH_DUE

    @pytest.mark.parametrize(
        "events", [None, "co_remegen", 42, [None, {}, {"entity_id": None}], object()]
    )
    def test_it_never_crashes_on_a_malformed_event_set(self, events):
        planned = plan_dossier_scans(
            ["co_remegen"],
            dossiers={"co_remegen": _dossier(FRESH)},
            today=TODAY,
            material_events=events,
        )
        assert isinstance(planned, list)

    @pytest.mark.parametrize("today", [None, "2026-07-19", 0, object()])
    def test_a_missing_clock_never_crashes_the_dial(self, today):
        planned = plan_dossier_scans(
            ["co_remegen"], dossiers={"co_remegen": _dossier(STALE)}, today=today
        )
        # No clock to judge against — the dial cannot fire blind, but the run lives.
        assert planned == []

    @pytest.mark.parametrize(
        "refresh_days", [None, "quarterly", -1, [91], float("nan")]
    )
    def test_a_malformed_dial_never_crashes(self, refresh_days):
        planned = plan_dossier_scans(
            ["co_remegen"],
            dossiers={"co_remegen": _dossier(FRESH)},
            today=TODAY,
            refresh_days=refresh_days,
        )
        assert isinstance(planned, list)

    @pytest.mark.parametrize(
        "spend",
        [
            None, "lots", ["turns"], {"turns": None}, {"turns": "9"}, 42, {},
            {"usd": None}, {"usd": "lots"}, {"usd": float("nan")},
            {"turns": {"nested": 1}, "usd": ["deeper"]},
        ],
    )
    def test_the_cap_receipt_never_crashes_on_a_malformed_spend(self, spend):
        scan = dossier_aperture(
            "co_akeso", DOSSIER_TRIGGER_FIRST_SIGHTING, cost_cap=CostCap(5, 5)
        )
        receipt = cap_receipt(scan, spend)
        assert receipt is None or receipt["degradation"] == DOSSIER_SCAN_COST_CAPPED

    @pytest.mark.parametrize(
        "entities", [None, "prose", ["co_remegen"], {None: {}}, {"co_x": "prose"}, 42]
    )
    def test_company_extraction_never_crashes(self, entities):
        assert isinstance(company_ids_from_entities(entities), list)

    @pytest.mark.parametrize("entity_id", [None, "", "   ", 42, ["co_x"], {}])
    def test_a_malformed_entity_id_plans_nothing_rather_than_crashing(self, entity_id):
        assert dossier_trigger(entity_id, dossiers={}, today=TODAY) is None
