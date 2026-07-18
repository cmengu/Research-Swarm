"""Stage 1 — the deterministic validator, issue.json v2.0.0 path.

These exercise the per-program detective schema ([07] v2.0.0): the four new
blocking checks (`missing_read_through`, `untyped_competitor`,
`blind_spot_overflow`, `landscape_number_unsourced`) and the ported spine
checks (uncited_claim, malformed_source, dangling_entity, empty_section,
derived_stats_mismatch, queue_tamper) running against the *v2* vocabulary.

The happy path is the real hand-built HMBD-001 sample — a full v2 issue
assembled from public facts. If the validator's own derivation of `stats` does
not reproduce the human-authored count, the fixture fails here, which is the
point: the derived-stats check is the guard that the two never drift.

The v1 path is untouched — `validate_issue` dispatches on `schema_version`, so
`test_validator.py` (v1) and this file (v2) both hold.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from researchswarm.state import State
from researchswarm.validator import derive_stats, validate_issue

SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "schema"
    / "sample-issue-hmbd-001-2026-07-18.json"
)


def _load_sample() -> dict:
    issue = json.loads(SAMPLE.read_text())
    issue.pop("_comment", None)
    return issue


def _known_entity_ids(issue: dict) -> set[str]:
    """Every entity the issue references, minus the self-introducing discoveries.

    The happy-path state must resolve every entity_id the issue names, or the
    dangling check fires. `newly_discovered` slugs introduce themselves, exactly
    as v1's `new_on_radar` did, so they are excluded from the roster.
    """
    ids: set[str] = set()
    for c in issue.get("competitors", []):
        ids.add(c["entity_id"])
    for ind in issue.get("indications", []):
        arena = ind.get("arena", {})
        for key in ("setting_rivals", "benchmark_soc"):
            for item in arena.get(key, []) or []:
                if item.get("entity_id"):
                    ids.add(item["entity_id"])
    for key in ("partnership_bd", "threat_financing", "themes_and_signals"):
        for item in issue.get("house_view", {}).get(key, []) or []:
            if item.get("entity_id"):
                ids.add(item["entity_id"])
    quiet = issue.get("quiet_this_cycle", {})
    for key in ("no_news", "open_threads", "dropped_with_receipt"):
        for item in quiet.get(key, []) or []:
            if item.get("entity_id"):
                ids.add(item["entity_id"])
    # queue holders (e.g. hengrui) are roster entities referenced only there;
    # headline/thesis entity_refs are REFERENCES, not roster definitions, so they
    # are not harvested — a ref with no roster entry is exactly a dangling_entity.
    for item in issue.get("catalyst_queue", {}).get("items", []) or []:
        ids.update(item.get("entity_ids", []) or [])
    # drop the self-introducing discovery slugs
    discovered = {c["entity_id"] for c in issue.get("newly_discovered", []) or []}
    return ids - discovered


def _state_for(issue: dict) -> State:
    return State(
        watchlist={"entities": [{"entity_id": e} for e in _known_entity_ids(issue)]},
        thesis={"beliefs": [{"id": "her3-target-vs-mechanism", "stance": "seeded"}]},
        catalyst_queue={},
    )


def _kinds(result) -> set[str]:
    return {f.kind for f in result.blocking}


class TestTheRealSamplePasses:
    def test_the_hand_built_hmbd001_issue_is_structurally_valid(self):
        issue = _load_sample()
        result = validate_issue(issue, state=_state_for(issue))
        assert result.passed, [f"{f.kind}@{f.where}: {f.note}" for f in result.blocking]

    def test_the_validator_reproduces_the_authored_stats(self):
        issue = _load_sample()
        derived = derive_stats(issue)
        for key, want in derived.items():
            assert issue["stats"][key] == want, f"{key}: authored {issue['stats'][key]} vs derived {want}"

    def test_sources_cited_totals_the_tier_counts(self):
        issue = _load_sample()
        tiers = issue["sources_and_method"]["source_tier_counts"]
        assert derive_stats(issue)["sources_cited"] == sum(tiers.values())


class TestMissingReadThrough:
    def test_a_competitor_with_no_read_through_blocks(self):
        issue = _load_sample()
        del issue["competitors"][0]["read_through"]
        result = validate_issue(issue, state=_state_for(issue))
        assert "missing_read_through" in _kinds(result)

    def test_an_empty_read_through_text_blocks(self):
        issue = _load_sample()
        issue["competitors"][0]["read_through"]["text"] = "   "
        result = validate_issue(issue, state=_state_for(issue))
        assert "missing_read_through" in _kinds(result)

    def test_a_house_item_with_a_lens_outside_the_enum_blocks(self):
        issue = _load_sample()
        issue["house_view"]["partnership_bd"][0]["read_through"]["lens"] = "made_up"
        result = validate_issue(issue, state=_state_for(issue))
        assert "missing_read_through" in _kinds(result)

    def test_an_arena_rival_missing_read_through_blocks(self):
        issue = _load_sample()
        issue["indications"][0]["arena"]["setting_rivals"][0].pop("read_through")
        result = validate_issue(issue, state=_state_for(issue))
        assert "missing_read_through" in _kinds(result)


class TestUntypedCompetitor:
    def test_a_competitor_with_a_relation_outside_the_four_blocks(self):
        issue = _load_sample()
        issue["competitors"][0]["read_through"]["relation"] = "not_a_relation"
        result = validate_issue(issue, state=_state_for(issue))
        assert "untyped_competitor" in _kinds(result)

    def test_a_platform_threat_placed_in_competitors_blocks(self):
        issue = _load_sample()
        issue["competitors"][0]["read_through"]["relation"] = "platform_threat"
        result = validate_issue(issue, state=_state_for(issue))
        assert "untyped_competitor" in _kinds(result)

    def test_a_valid_program_relation_does_not_block(self):
        issue = _load_sample()
        issue["competitors"][0]["read_through"]["relation"] = "mechanism_twin"
        result = validate_issue(issue, state=_state_for(issue))
        assert "untyped_competitor" not in _kinds(result)


class TestBlindSpotOverflow:
    def test_ranked_over_cap_with_no_overflow_receipt_blocks(self):
        issue = _load_sample()
        issue["house_view"]["blind_spots"]["cap"] = 1
        result = validate_issue(issue, state=_state_for(issue))
        assert "blind_spot_overflow" in _kinds(result)

    def test_ranked_over_cap_with_an_overflow_receipt_passes(self):
        issue = _load_sample()
        issue["house_view"]["blind_spots"]["cap"] = 1
        issue["house_view"]["blind_spots"]["overflow"] = "3 further blind spots not ranked"
        result = validate_issue(issue, state=_state_for(issue))
        assert "blind_spot_overflow" not in _kinds(result)

    def test_ranked_within_cap_passes(self):
        issue = _load_sample()
        result = validate_issue(issue, state=_state_for(issue))
        assert "blind_spot_overflow" not in _kinds(result)


class TestLandscapeNumberUnsourced:
    def test_an_efficacy_number_sourced_to_trade_press_blocks(self):
        issue = _load_sample()
        issue["indications"][0]["treatment_landscape"]["lines"][0]["efficacy_source"]["tier"] = "trade"
        result = validate_issue(issue, state=_state_for(issue))
        assert "landscape_number_unsourced" in _kinds(result)

    def test_a_line_with_no_efficacy_source_does_not_block(self):
        issue = _load_sample()
        # the 2L+ line already carries efficacy_source: null — assert it is quiet
        result = validate_issue(issue, state=_state_for(issue))
        assert "landscape_number_unsourced" not in _kinds(result)


class TestUnaccountedEntity:
    # the pilot roster: the two seed_competitors (config/programs/hmbd-001.toml)
    ROSTER = {"asset_her3_dxd", "asset_ivonescimab"}

    def test_a_fully_accounted_roster_passes(self):
        issue = _load_sample()
        # her3_dxd moved in competitors[]; ivonescimab moved in the arena
        result = validate_issue(issue, state=_state_for(issue), roster=self.ROSTER)
        assert "unaccounted_entity" not in _kinds(result)

    def test_a_rostered_entity_in_neither_place_blocks(self):
        issue = _load_sample()
        roster = self.ROSTER | {"asset_never_mentioned"}
        result = validate_issue(issue, state=_state_for(issue), roster=roster)
        assert "unaccounted_entity" in _kinds(result)

    def test_a_double_accounted_entity_blocks(self):
        issue = _load_sample()
        # her3_dxd is already in competitors[]; also drop it into no_news
        issue["quiet_this_cycle"]["no_news"].append(
            {"entity_id": "asset_her3_dxd", "name": "HER3-DXd", "cycles_quiet": 1}
        )
        result = validate_issue(issue, state=_state_for(issue), roster=self.ROSTER)
        kinds = [(f.kind, f.where) for f in result.blocking]
        assert ("unaccounted_entity", "asset_her3_dxd") in kinds

    def test_a_quiet_rostered_entity_is_accounted(self):
        issue = _load_sample()
        # zeno_her3 sits only in no_news — putting it on the roster must be fine
        result = validate_issue(
            issue, state=_state_for(issue), roster=self.ROSTER | {"asset_zeno_her3"}
        )
        assert "unaccounted_entity" not in _kinds(result)

    def test_no_roster_skips_the_check(self):
        issue = _load_sample()
        # without a roster there is nothing to hold accountable — never blocks
        result = validate_issue(issue, state=_state_for(issue))
        assert "unaccounted_entity" not in _kinds(result)

    def test_house_and_queue_entities_carry_no_coverage_duty(self):
        issue = _load_sample()
        # merck_co lives only in the house view; hengrui only in a queue item —
        # neither is a typed program competitor, so neither is on the roster and
        # neither triggers the coverage check even though they are known entities
        result = validate_issue(issue, state=_state_for(issue), roster=self.ROSTER)
        assert "unaccounted_entity" not in _kinds(result)


class TestArenaDormancyAndTheV2Register:
    def test_the_samples_dormant_nrg1_arena_is_exempted(self):
        # nrg1 arena is empty but carries arena_scan_dormant, and apertures_run /
        # apertures_degraded confirm it — no empty_section for that arena
        issue = _load_sample()
        result = validate_issue(issue, state=_state_for(issue))
        arenas = [f.where for f in result.blocking if f.kind == "empty_section"]
        assert not any("nrg1" in w for w in arenas)

    def test_an_empty_arena_with_no_degradation_blocks(self):
        issue = _load_sample()
        # strip nrg1's dormancy marker — now the empty arena is unexplained
        nrg1 = next(i for i in issue["indications"] if i["indication_id"] == "nrg1-fusion-solid-tumors")
        nrg1["treatment_landscape"].pop("degradation", None)
        result = validate_issue(issue, state=_state_for(issue))
        assert any(
            f.kind == "empty_section" and "nrg1" in f.where for f in result.blocking
        )

    def test_an_off_topic_degradation_kind_does_not_explain_an_empty_arena(self):
        issue = _load_sample()
        nrg1 = next(i for i in issue["indications"] if i["indication_id"] == "nrg1-fusion-solid-tumors")
        nrg1["treatment_landscape"]["degradation"] = {"kind": "calendar_stale", "marker": "x"}
        result = validate_issue(issue, state=_state_for(issue))
        assert any(
            f.kind == "empty_section" and "nrg1" in f.where for f in result.blocking
        )

    def test_a_dormancy_marker_the_apertures_do_not_confirm_blocks(self):
        issue = _load_sample()
        # keep the marker but scrub the mechanical evidence: no apertures_degraded
        # entry and the apertures_run status flipped to ok
        method = issue["sources_and_method"]
        method["apertures_degraded"] = []
        for entry in method.get("apertures_run", []):
            if entry.get("aperture") == "arena_scan" and entry.get("scope") == "nrg1-fusion-solid-tumors":
                entry["status"] = "ok"
        result = validate_issue(issue, state=_state_for(issue))
        assert any(
            f.kind == "empty_section" and "nrg1" in f.where for f in result.blocking
        )

    def test_interest_list_stale_files_a_visible_advisory(self):
        issue = _load_sample()
        issue["sources_and_method"]["interest_list"]["rot_status"] = "stale"
        result = validate_issue(issue, state=_state_for(issue))
        advisory_kinds = {f.kind for f in result.advisory}
        assert "interest_list_stale" in advisory_kinds
        # and it is advisory, never blocking
        assert "interest_list_stale" not in _kinds(result)

    def test_a_fresh_interest_list_files_no_advisory(self):
        issue = _load_sample()  # sample rot_status is "fresh"
        result = validate_issue(issue, state=_state_for(issue))
        assert "interest_list_stale" not in {f.kind for f in result.advisory}

    def test_the_v2_register_vocabulary_matches_the_spec_table(self):
        from researchswarm.validator import DEGRADATION_REGISTER_V2

        assert DEGRADATION_REGISTER_V2 == {
            "thesis_unseeded",
            "quiet_cycle",
            "calendar_stale",
            "arena_scan_failed",
            "arena_scan_dormant",
            "china_feed_partial",
            "interest_list_stale",
        }


class TestPortedSpineChecks:
    def test_a_dangling_entity_ref_blocks(self):
        issue = _load_sample()
        issue["headline"]["entity_refs"].append("asset_does_not_exist")
        result = validate_issue(issue, state=_state_for(issue))
        assert "dangling_entity" in _kinds(result)

    def test_an_uncited_competitor_blocks(self):
        issue = _load_sample()
        issue["competitors"][0]["sources"] = []
        result = validate_issue(issue, state=_state_for(issue))
        assert "uncited_claim" in _kinds(result)

    def test_a_source_as_a_string_blocks(self):
        issue = _load_sample()
        issue["competitors"][0]["sources"] = ["https://example.com/bare-url"]
        result = validate_issue(issue, state=_state_for(issue))
        assert "malformed_source" in _kinds(result)

    def test_a_stats_count_that_disagrees_with_the_arrays_blocks(self):
        issue = _load_sample()
        issue["stats"]["competitors_moved"] = 99
        result = validate_issue(issue, state=_state_for(issue))
        assert "derived_stats_mismatch" in _kinds(result)

    def test_an_empty_required_section_blocks(self):
        issue = _load_sample()
        issue["tldr_bullets"] = []
        result = validate_issue(issue, state=_state_for(issue))
        assert "empty_section" in _kinds(result)
