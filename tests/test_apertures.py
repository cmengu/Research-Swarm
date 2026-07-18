"""The aperture roster planner (spec/04).

Derived from the REAL pilot program config, so a drift between the config and the
`1 + N + 1` roster fails here.
"""

from __future__ import annotations

from pathlib import Path

from researchswarm.apertures import (
    ARENA_SCAN,
    BIOLOGY_SCAN,
    HOUSE_SWEEP,
    active_apertures,
    plan_apertures,
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
