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
    def _obj(container, key):
        """Sub-object as a mapping, or empty — the harness twin of `_mapping`.

        The malformation tests hand this helper issues whose sections have been
        replaced with null or prose ON PURPOSE. The harness must survive the same
        inputs the gate does, or a validator fix is masked by a crashing fixture.
        """
        value = container.get(key) if isinstance(container, dict) else None
        return value if isinstance(value, dict) else {}

    def _items(container, key):
        value = container.get(key) if isinstance(container, dict) else None
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    ids: set[str] = set()
    for c in _items(issue, "competitors"):
        if c.get("entity_id"):
            ids.add(c["entity_id"])
    for ind in _items(issue, "indications"):
        arena = _obj(ind, "arena")
        for key in ("setting_rivals", "benchmark_soc"):
            for item in _items(arena, key):
                if item.get("entity_id"):
                    ids.add(item["entity_id"])
    for key in ("partnership_bd", "threat_financing", "themes_and_signals"):
        for item in _items(_obj(issue, "house_view"), key):
            if item.get("entity_id"):
                ids.add(item["entity_id"])
    quiet = _obj(issue, "quiet_this_cycle")
    for key in ("no_news", "open_threads", "dropped_with_receipt"):
        for item in _items(quiet, key):
            if item.get("entity_id"):
                ids.add(item["entity_id"])
    # queue holders (e.g. hengrui) are roster entities referenced only there;
    # headline/thesis entity_refs are REFERENCES, not roster definitions, so they
    # are not harvested — a ref with no roster entry is exactly a dangling_entity.
    for item in _items(_obj(issue, "catalyst_queue"), "items"):
        ids.update(item.get("entity_ids", []) or [])
    # drop the self-introducing discovery slugs
    discovered = {
        c["entity_id"] for c in _items(issue, "newly_discovered") if c.get("entity_id")
    }
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


class TestTheGateNeverCrashes:
    """A gate that CRASHES is strictly worse than one that misses.

    `_mapping` exists because `x.get(k, {}).get(...)` supplies its default only
    when the key is ABSENT — a key present-but-null, or holding prose, raised
    AttributeError *inside* the check written to catch exactly that malformation.
    These are the two live shapes that took the gate down.
    """

    def test_a_null_intermediate_object_does_not_raise(self):
        issue = _load_sample()
        state = _state_for(issue)
        for section in (
            "quiet_this_cycle",
            "house_view",
            "sources_and_method",
            "catalyst_queue",
            "headline",
        ):
            broken = _load_sample()
            broken[section] = None
            result = validate_issue(broken, state=state)
            assert not result.passed, f"{section}: a null section must be FOUND, not tolerated"

    def test_prose_where_an_object_belongs_does_not_raise(self):
        issue = _load_sample()
        state = _state_for(issue)
        for section in (
            "quiet_this_cycle",
            "house_view",
            "sources_and_method",
            "catalyst_queue",
            "headline",
        ):
            broken = _load_sample()
            broken[section] = "the manager wrote a paragraph here"
            result = validate_issue(broken, state=state)
            assert not result.passed, f"{section}: prose must be FOUND, not tolerated"

    def test_nested_prose_intermediates_do_not_raise(self):
        issue = _load_sample()
        issue["indications"][0]["arena"] = "prose where the arena belongs"
        issue["indications"][0]["treatment_landscape"] = "prose where the landscape belongs"
        issue["competitors"][0]["read_through"] = "prose where the read-through belongs"
        issue["house_view"]["blind_spots"] = "prose"
        result = validate_issue(issue, state=_state_for(issue))
        assert {"malformed_treatment_landscape", "missing_read_through"} <= _kinds(result)

    def test_the_dropped_receipt_row_survives_its_own_failure_mode(self):
        """`quiet_this_cycle` itself malformed: the RECEIPT row must find nothing
        and raise nothing — the row that names `quiet_this_cycle` is what reports."""
        from researchswarm.validator import _check_issue_shape_v2

        for value in (None, "prose", 7, []):
            problems: list = []
            _check_issue_shape_v2({"quiet_this_cycle": value}, problems)
            assert [f for f in problems if f.kind == "malformed_dropped_receipt"] == []


class TestMalformedOpenThread:
    def test_a_bare_string_thread_blocks(self):
        issue = _load_sample()
        issue["quiet_this_cycle"]["open_threads"] = ["a paragraph of prose"]
        result = validate_issue(issue, state=_state_for(issue))
        assert "malformed_open_thread" in _kinds(result)

    def test_a_thread_missing_since_blocks(self):
        issue = _load_sample()
        del issue["quiet_this_cycle"]["open_threads"][0]["since"]
        result = validate_issue(issue, state=_state_for(issue))
        assert "malformed_open_thread" in _kinds(result)

    def test_the_sample_threads_pass(self):
        issue = _load_sample()
        result = validate_issue(issue, state=_state_for(issue))
        assert "malformed_open_thread" not in _kinds(result)


class TestMalformedTreatmentLandscape:
    def test_the_live_managers_envelope_blocks(self):
        """The exact shape the first live run emitted ([07] §treatment_landscape)."""
        issue = _load_sample()
        issue["indications"][0]["treatment_landscape"] = {
            "indication": "Squamous NSCLC",
            "entries": [],
            "bar_direction": "rising",
            "emerging_therapies_note": "…",
        }
        result = validate_issue(issue, state=_state_for(issue))
        assert "malformed_treatment_landscape" in _kinds(result)

    def test_lines_that_is_not_a_list_blocks(self):
        issue = _load_sample()
        issue["indications"][0]["treatment_landscape"]["lines"] = {"1L": "…"}
        result = validate_issue(issue, state=_state_for(issue))
        assert "malformed_treatment_landscape" in _kinds(result)

    def test_an_indication_with_no_landscape_at_all_is_untouched(self):
        issue = _load_sample()
        issue["indications"][0].pop("treatment_landscape", None)
        result = validate_issue(issue, state=_state_for(issue))
        assert "malformed_treatment_landscape" not in _kinds(result)

    def test_the_envelope_check_is_what_makes_the_landscape_rule_reachable(self):
        """The vacuity chain: a wrong envelope hides `lines`, so the primary-only
        efficacy rule and the sources_cited count both go silently blind ([07] #57)."""
        issue = _load_sample()
        issue["indications"][0]["treatment_landscape"] = {
            "indication": "Squamous NSCLC",
            "entries": [{"efficacy_source": {"tier": "trade_press"}}],
        }
        result = validate_issue(issue, state=_state_for(issue))
        assert "landscape_number_unsourced" not in _kinds(result)
        assert "malformed_treatment_landscape" in _kinds(result)


class TestAgainstTheRealPublishedIssue:
    """The published artifact the review was found against — issues/hmbd-001/2026-07-18.json.

    It PASSED the gate as it stood. The two new checks are what put the gate back
    in contact with it: the landscape envelope and the bare-string open threads.
    """

    PUBLISHED = Path(__file__).resolve().parents[1] / "issues" / "hmbd-001" / "2026-07-18.json"

    def test_the_new_checks_fire_on_the_real_issue(self):
        if not self.PUBLISHED.exists():  # pragma: no cover - artifact may be pruned
            import pytest

            pytest.skip("no published issue on disk")
        from researchswarm.validator import _check_issue_shape_v2

        issue = json.loads(self.PUBLISHED.read_text())
        problems: list = []
        _check_issue_shape_v2(issue, problems)
        by_kind: dict[str, list] = {}
        for finding in problems:
            by_kind.setdefault(finding.kind, []).append(finding.where)

        # The four shapes the live run broke, all now caught by rows in the table
        # rather than by four hand-written functions.
        assert len(by_kind.get("malformed_treatment_landscape", [])) == 2, by_kind
        assert len(by_kind.get("malformed_open_thread", [])) == 3, by_kind
        assert len(by_kind.get("malformed_dropped_receipt", [])) == 5, by_kind
        assert len(by_kind.get("malformed_promotion_proposal", [])) == 2, by_kind

        # And two the hand-written set never covered at all: `registry_watch`
        # emitted as a bare list of diffs instead of [07]'s input-class object,
        # and an `interest_list` with no `source`. Neither was ever *decided*
        # against — no one had written the function yet, which is exactly the
        # drift a table makes impossible.
        assert sorted(by_kind.get("malformed_shape", [])) == [
            "sources_and_method.interest_list",
            "sources_and_method.registry_watch",
        ], by_kind


class TestTheShapeTableDoesNotDriftFromTheContract:
    """THE point of the table: coverage that cannot silently fall behind [07].

    The gate's first v2 pass hand-wrote ~14 shape checks against a spec stating
    ~20 required shapes. The missing six were never *decided* against — nobody
    had written the function — and the system discovered them one at a time by
    crashing into them after publishing. Prose coverage drifts because nothing
    fails when it does. These tests are what fails.
    """

    def _table_findings(self, issue):
        from researchswarm.validator import _check_issue_shape_v2

        problems: list = []
        _check_issue_shape_v2(issue, problems)
        return problems

    def test_every_row_resolves_in_the_schema_correct_sample(self):
        """Table → spec. A row naming a path the reference sample does not have
        is a row written against a misremembered contract — a typo'd key, or a
        field that moved. It would then silently police nothing forever."""
        from researchswarm.validator import ISSUE_SHAPE_V2, _MISSING, _walk_path

        sample = _load_sample()
        unresolved = [
            shape.path
            for shape in ISSUE_SHAPE_V2
            if not [
                value
                for value, _ in _walk_path(sample, shape.path)
                if value is not _MISSING and value is not None
            ]
        ]
        assert unresolved == [], f"rows naming paths absent from the sample: {unresolved}"

    def test_the_schema_correct_sample_passes_the_table_cleanly(self):
        """Spec → table. The worked example IS the contract made concrete, so a
        finding against it means the table is wrong (or the sample is)."""
        assert self._table_findings(_load_sample()) == []

    # Paths the sample carries that carry NO shape duty in the table, each with
    # the reason it is somebody else's job. This is the list a reviewer argues
    # with — which is the point: an exemption is a decision on the record, not
    # an absence nobody noticed.
    EXEMPT_LEAF_KEYS = {
        "sources": "the source object's four required fields are `malformed_source`'s",
        "source": "same — the receipt's source object is `malformed_source`'s",
        "window_source": "same",
        "efficacy_source": "shape is `malformed_source`'s; its TIER is `landscape_number_unsourced`'s",
        "degradation": "the degradation register owns the kind vocabulary ([06])",
        "proposes_interest": "[07]: an item MAY propose an interest — optional by contract",
        "holders": "[07] shows it; it is not marked required, and a solo-developed asset has none",
        "categories": "optional taxonomy, not a required shape",
        "entity_ids": "queue-level convenience refs; the dangling check reads them",
        "emerging": "a read-only VIEW over the queue ([07] #57), never an authored list",
        "paywalled_flagged": "unchanged-from-v1 open shape",
        "advisory_findings": "critic-authored, open kind set — machinery [06] does not re-open",
    }

    def test_no_sample_path_is_missing_from_the_table_without_a_reason(self):
        """The 'spec grew, table didn't' direction.

        Walks the sample's own structure and demands that every container path
        either has a row or appears in `EXEMPT_LEAF_KEYS` with a stated reason.
        Add a section to [07] and the sample and forget the table, and this is
        the test that turns a 3am AttributeError into a red build.
        """
        from researchswarm.validator import ISSUE_SHAPE_V2

        sample = _load_sample()
        paths: set[str] = set()

        def walk(node, path):
            if isinstance(node, dict):
                if path:
                    paths.add(path)
                for key, value in node.items():
                    walk(value, f"{path}.{key}" if path else key)
            elif isinstance(node, list):
                if path:
                    paths.add(path)
                for element in node:
                    walk(element, path + "[]")

        walk(sample, "")
        covered = {shape.path for shape in ISSUE_SHAPE_V2}
        uncovered = sorted(
            path
            for path in paths - covered
            if path.split(".")[-1].removesuffix("[]") not in self.EXEMPT_LEAF_KEYS
        )
        assert uncovered == [], (
            "sample paths with neither a table row nor a stated exemption — "
            f"the contract grew and the gate did not: {uncovered}"
        )

    def test_the_retired_hand_written_checks_are_gone(self):
        """They are rows now. Leaving a shim behind would restore exactly the
        two-homes-for-one-rule drift this build removes."""
        import researchswarm.validator as validator

        for retired in (
            "_check_malformed_open_thread",
            "_check_malformed_dropped_receipt",
            "_check_malformed_treatment_landscape",
            "_check_malformed_promotion_proposal",
        ):
            assert not hasattr(validator, retired), f"{retired} should be a table row now"

    def test_the_judgment_checks_are_kept(self):
        """Shape compresses to a row; JUDGMENT does not. These stay hand-written
        because each encodes a rule about MEANING, not about type."""
        import researchswarm.validator as validator

        for kept in (
            "_check_missing_read_through",  # the admission rule
            "_check_untyped_competitor",  # platform_threat belongs in the house view
            "_check_blind_spot_overflow",  # capped, and overflow is never silent
            "_check_landscape_number_unsourced",  # benchmark numbers are primary-only
            "_check_empty_arena_v2",  # dormancy must be mechanically corroborated
        ):
            assert hasattr(validator, kept)


class TestTheWalkerNeverCrashes:
    """It validates adversarial input BY DEFINITION — that is its whole job.

    A gate that raises is strictly worse than one that misses: it takes the run
    down at the moment the issue is most malformed, and it does so AFTER
    publishing. That exact bug shipped twice on the first live night. So the walk
    is total at every depth, for every kind of garbage.
    """

    GARBAGE = (None, "prose the manager wrote instead", 7, 0, True, [], {}, [None], ["x"], {"k": None})

    def _run(self, issue):
        from researchswarm.validator import _check_issue_shape_v2

        problems: list = []
        _check_issue_shape_v2(issue, problems)  # must not raise
        return problems

    def test_every_top_level_section_survives_every_kind_of_garbage(self):
        from researchswarm.validator import ISSUE_SHAPE_V2

        roots = sorted({shape.path.split(".")[0].removesuffix("[]") for shape in ISSUE_SHAPE_V2})
        for root in roots:
            for value in self.GARBAGE:
                issue = _load_sample()
                issue[root] = value
                self._run(issue)

    def test_every_row_survives_garbage_at_its_own_depth(self):
        """Not just the top level: a null two levels down is the shape the live
        manager actually emitted, and the walk must step over it."""
        from researchswarm.validator import ISSUE_SHAPE_V2, _walk_path

        for shape in ISSUE_SHAPE_V2:
            for value in self.GARBAGE:
                issue = _load_sample()
                # Plant the garbage AT the row's own path by walking the parents
                # in the live object, which is the only way to hit `[]` segments.
                self._plant(issue, shape.path.split("."), value)
                self._run(issue)
                assert _walk_path(issue, shape.path) is not None

    @staticmethod
    def _plant(node, segments, value):
        segment, rest = segments[0], segments[1:]
        key, is_list = segment.removesuffix("[]"), segment.endswith("[]")
        if not isinstance(node, dict):
            return
        if not rest:
            if is_list and isinstance(node.get(key), list):
                node[key] = [value for _ in node[key]] or [value]
            else:
                node[key] = value
            return
        target = node.get(key)
        if is_list and isinstance(target, list):
            for element in target:
                TestTheWalkerNeverCrashes._plant(element, rest, value)
        else:
            TestTheWalkerNeverCrashes._plant(target, rest, value)

    def test_the_whole_gate_survives_a_totally_hostile_issue(self):
        """Every section replaced with prose at once, through the real entry
        point — the gate must return findings, not a traceback."""
        from researchswarm.validator import validate_issue

        issue = _load_sample()
        hostile = {key: "the manager wrote a paragraph here" for key in issue}
        hostile["schema_version"] = "2.0.0"  # the dispatch key, kept honest
        result = validate_issue(hostile, state=_state_for(_load_sample()))
        assert not result.passed
        assert len(result.blocking) > 5

    def test_an_unhashable_value_where_an_enum_belongs_is_a_finding_not_a_typeerror(self):
        issue = _load_sample()
        issue["issue"]["run"]["status"] = ["published"]  # a list is unhashable
        issue["headline"]["confidence"] = {"level": "high"}
        problems = self._run(issue)
        wheres = {finding.where for finding in problems}
        assert "issue.run.status" in wheres
        assert "headline.confidence" in wheres


class TestTheGateNeverRaises:
    """`validate_issue`'s docstring says "never raise". Until this test it lied.

    Not reachable in production — the manager seam rejects a non-object draft
    first — but the gate is the component that decides whether a malformed draft
    is caught, so it must not depend on a caller upstream staying careful.
    """

    def _validate(self, issue):
        from researchswarm.validator import validate_issue

        return validate_issue(
            issue, state=None, queue_baseline=None, baseline_expired=False, calendar_stale=False
        )

    def test_a_null_issue_is_a_finding_not_a_traceback(self):
        result = self._validate(None)
        assert not result.passed
        assert result.blocking[0].kind == "malformed_shape"
        assert "got NoneType" in result.blocking[0].note

    def test_prose_where_an_issue_belongs_is_a_finding(self):
        result = self._validate("the manager wrote an essay")
        assert not result.passed
        assert "got str" in result.blocking[0].note

    def test_a_list_where_an_issue_belongs_is_a_finding(self):
        assert not self._validate([{"schema_version": "2.0.0"}]).passed
