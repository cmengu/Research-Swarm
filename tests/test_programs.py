"""The v2 program layer loaders (spec/03).

These load the REAL pilot config (`config/programs/hmbd-001.toml`,
`config/interests.toml`) so a drift between the authored config and the loader's
expectations fails here, and exercise the state-layer loaders + roster derivation
against a temp fixture. The v1 `state.py` loaders are untouched and their tests
(`test_state.py`) still hold — the two shapes run side by side.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from researchswarm.programs import (
    INTEREST_ROT_MONTHS,
    Edge,
    load_edges,
    load_entities,
    load_interests,
    load_program,
    program_roster,
)

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"
STATE = REPO / "state"


class TestLoadTheRealProgramConfig:
    def test_the_pilot_program_loads_with_its_aperture(self):
        program = load_program(CONFIG, "hmbd-001")
        assert program.id == "hmbd-001"
        assert program.name == "HMBD-001"
        assert program.sponsor == "Hummingbird Bioscience"
        assert program.target == "HER3 (ERBB3)"
        # the load-bearing scan field that separates target twins from mechanism twins
        assert program.moa == "signalling_blockade"

    def test_indications_are_first_class_with_roles(self):
        program = load_program(CONFIG, "hmbd-001")
        by_id = {i.id: i.role for i in program.indications}
        assert by_id["squamous-nsclc"] == "active_arena"
        assert by_id["nrg1-fusion-solid-tumors"] == "priority_indication"

    def test_active_arena_ids_are_the_scan_targets(self):
        program = load_program(CONFIG, "hmbd-001")
        assert program.active_arena_ids == ("squamous-nsclc",)

    def test_seed_competitors_is_the_cold_start_typing_path(self):
        program = load_program(CONFIG, "hmbd-001")
        assert set(program.seed_competitors) == {"asset_her3_dxd", "asset_ivonescimab"}

    def test_cadence_is_the_per_program_monthly_dial(self):
        program = load_program(CONFIG, "hmbd-001")
        assert program.cadence_baseline == "monthly"
        assert program.cold_start_lookback_days == 7

    def test_a_missing_program_fails_naming_the_file(self):
        with pytest.raises(FileNotFoundError):
            load_program(CONFIG, "does-not-exist")


class TestLoadTheRealInterestList:
    def test_the_steering_wheel_loads(self):
        interests = load_interests(CONFIG)
        assert interests.last_edited_by == "owner"
        assert interests.version >= 1
        tiers = {i.tier for i in interests.interests}
        assert tiers <= {"strong", "watching"}
        assert all(i.note for i in interests.interests)

    def test_a_fresh_list_is_not_stale(self):
        interests = load_interests(CONFIG)
        edited = date.fromisoformat(interests.last_edited)
        # one day after it was edited, it is fresh
        assert not interests.is_stale(edited)

    def test_rot_fires_after_the_default_window(self):
        interests = load_interests(CONFIG)
        edited = date.fromisoformat(interests.last_edited)
        # a year on, a 6-month list is stale
        assert interests.is_stale(date(edited.year + 1, edited.month, edited.day))

    def test_a_malformed_edit_date_reads_as_stale(self, tmp_path):
        (tmp_path / "interests.toml").write_text(
            'version = 1\nlast_edited = "not-a-date"\nlast_edited_by = "owner"\n'
        )
        interests = load_interests(tmp_path)
        assert interests.is_stale(date(2026, 7, 18))


class TestTheSeedStateLayer:
    def test_the_pilot_edges_seed_empty(self):
        assert load_edges(STATE, "hmbd-001") == []

    def test_the_entities_layer_seeds_empty(self):
        # only the .gitkeep is present; no records materialized yet
        assert load_entities(STATE) == {}

    def test_roster_at_seed_is_exactly_the_seed_competitors(self):
        program = load_program(CONFIG, "hmbd-001")
        edges = load_edges(STATE, "hmbd-001")
        assert program_roster(program, edges) == {"asset_her3_dxd", "asset_ivonescimab"}


class TestTheStateLayerOncePopulated:
    """The loaders read whatever the deferred curation session writes — proven
    against a temp fixture so the seed files can stay empty."""

    def _write(self, tmp_path):
        (tmp_path / "entities").mkdir()
        (tmp_path / "entities" / "asset_her3_dxd.json").write_text(
            json.dumps({"entity_id": "asset_her3_dxd", "name": "HER3-DXd"})
        )
        prog_dir = tmp_path / "programs" / "hmbd-001"
        prog_dir.mkdir(parents=True)
        prog_dir.joinpath("edges.json").write_text(
            json.dumps(
                {
                    "program_id": "hmbd-001",
                    "edges": [
                        {
                            "entity_id": "asset_her3_dxd",
                            "relation": "target_twin",
                            "read_through": {"text": "..."},
                            "promoted_by": "run_x",
                            "drift_log": [],
                        }
                    ],
                }
            )
        )
        return tmp_path

    def test_edges_load_with_their_relation_and_read_through(self, tmp_path):
        state = self._write(tmp_path)
        edges = load_edges(state, "hmbd-001")
        assert edges == [
            Edge(
                entity_id="asset_her3_dxd",
                relation="target_twin",
                read_through={"text": "..."},
                promoted_by="run_x",
                drift_log=(),
            )
        ]

    def test_entities_key_by_their_entity_id(self, tmp_path):
        state = self._write(tmp_path)
        entities = load_entities(state)
        assert set(entities) == {"asset_her3_dxd"}
        assert entities["asset_her3_dxd"]["name"] == "HER3-DXd"

    def test_roster_unions_promoted_edges_with_unpromoted_seeds(self, tmp_path):
        state = self._write(tmp_path)
        program = load_program(CONFIG, "hmbd-001")
        edges = load_edges(state, "hmbd-001")
        # her3_dxd is now a promoted edge; ivonescimab is still only a seed
        assert program_roster(program, edges) == {"asset_her3_dxd", "asset_ivonescimab"}

    def test_rot_uses_the_documented_default(self):
        assert INTEREST_ROT_MONTHS == 6
