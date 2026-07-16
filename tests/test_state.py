"""State loading and the entity_id join check.

entity_id is the spine: it links watchlist to issue to queue to findings, and
it is what makes entity history queryable. Three assets disagreed on the
definition key before the spec ruled it; the join check is what stops the spine
forking again.
"""

import json

import pytest

from researchswarm.state import DanglingRef, check_entity_refs, load_state


def _write_state(dir_, *, entities=None, queue=None, beliefs=None):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "watchlist.json").write_text(
        json.dumps({"version": 1, "entities": entities or []})
    )
    (dir_ / "catalyst-queue.json").write_text(
        json.dumps({"version": 1, "queue": queue or []})
    )
    (dir_ / "thesis.json").write_text(
        json.dumps({"version": 1, "beliefs": beliefs or []})
    )
    return dir_


class TestLoadState:
    def test_loads_the_real_seeded_state(self, repo_root):
        """The state committed to this repo must parse and carry the spine."""
        state = load_state(repo_root / "state")
        assert len(state.watchlist["entities"]) == 22
        assert len(state.thesis["beliefs"]) == 6
        assert state.thesis["version"] == 2
        # The rename landed with the spec: entities define entity_id, never id.
        for entity in state.watchlist["entities"]:
            assert "entity_id" in entity
            assert "id" not in entity

    def test_entity_ids_returns_the_spine(self, tmp_path):
        _write_state(
            tmp_path / "state",
            entities=[{"entity_id": "merck"}, {"entity_id": "asset_daraxonrasib"}],
        )
        state = load_state(tmp_path / "state")
        assert state.entity_ids == {"merck", "asset_daraxonrasib"}

    def test_missing_state_file_is_an_error(self, tmp_path):
        (tmp_path / "state").mkdir()
        with pytest.raises(FileNotFoundError):
            load_state(tmp_path / "state")

    def test_malformed_json_names_the_file(self, tmp_path):
        state_dir = _write_state(tmp_path / "state")
        (state_dir / "thesis.json").write_text("{not json")
        with pytest.raises(ValueError, match="thesis.json"):
            load_state(state_dir)


class TestCheckEntityRefs:
    def test_the_real_seeded_state_has_no_dangling_refs(self, repo_root):
        """The seeded queue references the seeded roster. If this breaks, the
        spine has forked and everything downstream is unsound."""
        state = load_state(repo_root / "state")
        assert check_entity_refs(state) == []

    def test_resolving_ref_passes(self, tmp_path):
        _write_state(
            tmp_path / "state",
            entities=[{"entity_id": "merck"}],
            queue=[{"id": "cat_x", "entity_ids": ["merck"]}],
        )
        assert check_entity_refs(load_state(tmp_path / "state")) == []

    def test_dangling_ref_is_reported(self, tmp_path):
        _write_state(
            tmp_path / "state",
            entities=[{"entity_id": "merck"}],
            queue=[{"id": "cat_x", "entity_ids": ["pfizer"]}],
        )
        dangling = check_entity_refs(load_state(tmp_path / "state"))
        assert dangling == [
            DanglingRef(entity_id="pfizer", where="catalyst-queue.json:cat_x")
        ]

    def test_reports_every_dangling_ref_not_just_the_first(self, tmp_path):
        _write_state(
            tmp_path / "state",
            entities=[{"entity_id": "merck"}],
            queue=[
                {"id": "cat_x", "entity_ids": ["pfizer", "merck"]},
                {"id": "cat_y", "entity_ids": ["gsk"]},
            ],
        )
        dangling = check_entity_refs(load_state(tmp_path / "state"))
        assert {d.entity_id for d in dangling} == {"pfizer", "gsk"}

    def test_off_roster_find_carries_no_refs_and_so_cannot_dangle(self, tmp_path):
        """An off-roster find carries entity_ids: [] and a proposed_entity — the
        manager decides whether it becomes a radar entry. There is nothing to
        resolve, so it needs no exemption."""
        _write_state(
            tmp_path / "state",
            entities=[{"entity_id": "merck"}],
            queue=[
                {
                    "id": "cat_x",
                    "entity_ids": [],
                    "proposed_entity": {"name": "Callio Therapeutics"},
                }
            ],
        )
        assert check_entity_refs(load_state(tmp_path / "state")) == []

    def test_proposed_entity_does_not_excuse_a_named_dangling_ref(self, tmp_path):
        """The regression this check exists for. A proposal alongside real refs
        must not blanket-excuse them — otherwise one proposal smuggles any
        number of dangling references past the only check guarding the spine."""
        _write_state(
            tmp_path / "state",
            entities=[{"entity_id": "merck"}],
            queue=[
                {
                    "id": "cat_x",
                    "entity_ids": ["merck", "bogus_pharma"],
                    "proposed_entity": {"name": "NewCo"},
                }
            ],
        )
        dangling = check_entity_refs(load_state(tmp_path / "state"))
        assert dangling == [
            DanglingRef(entity_id="bogus_pharma", where="catalyst-queue.json:cat_x")
        ]

    def test_empty_refs_are_fine(self, tmp_path):
        _write_state(
            tmp_path / "state",
            entities=[{"entity_id": "merck"}],
            queue=[{"id": "cat_x", "entity_ids": []}],
        )
        assert check_entity_refs(load_state(tmp_path / "state")) == []
