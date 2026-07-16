"""The deterministic gate, check by check.

Each test is one sentence about what the validator decides, with a small fixture
issue built to trip exactly one check. The validator is free and deterministic —
that is the whole point — so there is no live variant: everything here runs
offline in milliseconds.
"""

import pytest

from researchswarm.state import State
from researchswarm.validator import (
    DEGRADATION_REGISTER,
    Finding,
    validate_issue,
)


def _state(*entity_ids, beliefs=None):
    """A minimal State: the validator only reads entity_ids and thesis."""
    return State(
        watchlist={"entities": [{"entity_id": e} for e in entity_ids]},
        thesis={"beliefs": beliefs if beliefs is not None else [{"id": "s", "stance": "x"}]},
        catalyst_queue={},
    )


def _source(**overrides):
    source = {"url": "https://x", "publisher": "Endpoints", "tier": "trade", "published_at": "2026-07-15"}
    source.update(overrides)
    return source


def _issue(**overrides):
    """A structurally valid issue for entities merck (news) + pfizer (quiet)."""
    issue = {
        "schema_version": "1.0.0",
        "headline": {"title": "t", "summary": "s", "so_what": "w",
                     "entity_refs": ["merck"], "sources": [_source()]},
        "stats": {},
        "tldr_bullets": [{"text": "b", "entity_refs": ["merck"], "priority": "high"}],
        "catalyst_queue": {"snapshot_of": "state/catalyst-queue.json", "recut_at": None, "items": []},
        "watchlist": [{"entity_id": "merck", "name": "Merck", "summary": "x", "sources": [_source()]}],
        "quiet_this_cycle": {
            "no_news": [{"entity_id": "pfizer", "name": "Pfizer", "cycles_quiet": 1}],
            "critic_catches": [],
            "open_threads": [],
        },
        "new_on_radar": [],
        "themes_and_signals": [],
        "elsewhere_on_frontier": [],
        "thesis_updates": [],
        "critic_report": {},
        "sources_and_method": {"beats_run": ["ma_dealmaking"], "beats_failed": [],
                               "source_tier_counts": {}, "paywalled_flagged": []},
    }
    issue.update(overrides)
    return issue


def _kinds(result):
    return {f.kind for f in result.blocking}


def _wheres(result, kind):
    return {f.where for f in result.blocking if f.kind == kind}


class TestAValidIssuePasses:
    def test_the_baseline_fixture_has_no_blocking_findings(self):
        result = validate_issue(_issue(), state=_state("merck", "pfizer"))
        assert result.passed
        assert result.blocking == ()


class TestUncitedClaim:
    def test_a_content_bearing_object_without_sources_blocks(self):
        issue = _issue()
        issue["watchlist"][0]["sources"] = []
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "uncited_claim" in _kinds(result)
        assert "watchlist[merck]" in _wheres(result, "uncited_claim")

    def test_a_headline_with_no_sources_blocks(self):
        issue = _issue()
        del issue["headline"]["sources"]
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "headline" in _wheres(result, "uncited_claim")


class TestMalformedSource:
    def test_a_source_missing_a_core_field_blocks(self):
        issue = _issue()
        issue["headline"]["sources"] = [_source(publisher="")]
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "malformed_source" in _kinds(result)

    def test_a_tier_outside_the_enum_blocks(self):
        issue = _issue()
        issue["headline"]["sources"] = [_source(tier="blog")]
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "malformed_source" in _kinds(result)

    def test_the_issue_side_source_does_not_require_paywalled(self):
        """paywalled is findings.json's extra, not one of the four core fields."""
        result = validate_issue(_issue(), state=_state("merck", "pfizer"))
        assert "malformed_source" not in _kinds(result)

    def test_it_walks_window_source_and_slip_log_sources_too(self):
        issue = _issue()
        issue["catalyst_queue"]["items"] = [{
            "id": "q1", "entity_ids": [], "first_expected_window": "2026-Q2",
            "expected_window": "2026-Q2", "status": "pending",
            "window_source": _source(url=""),  # malformed
            "slip_log": [], "sources": [_source()],
        }]
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert any(
            f.kind == "malformed_source" and "window_source" in f.where
            for f in result.blocking
        )


class TestDanglingEntity:
    def test_a_reference_to_an_unknown_slug_blocks(self):
        issue = _issue()
        issue["headline"]["entity_refs"] = ["ghost_pharma"]
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "dangling_entity" in _kinds(result)

    def test_a_new_on_radar_entry_introduces_its_own_slug(self):
        """A new_on_radar entry IS the proposal, so its slug is self-introducing —
        referencing it elsewhere resolves, it does not dangle."""
        issue = _issue()
        issue["new_on_radar"] = [{"entity_id": "callio_tx", "name": "Callio",
                                  "what_they_do": "x", "sources": [_source()]}]
        issue["themes_and_signals"] = [{"theme": "t", "evidence_refs": ["callio_tx"],
                                        "argument": "a", "thesis_impact": "neutral"}]
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "dangling_entity" not in _kinds(result)


class TestUnaccountedWatchlistEntity:
    def test_an_entity_in_neither_section_blocks(self):
        # roche is tracked but appears in neither watchlist nor no_news.
        result = validate_issue(_issue(), state=_state("merck", "pfizer", "roche"))
        assert "unaccounted_watchlist_entity" in _kinds(result)
        assert "roche" in _wheres(result, "unaccounted_watchlist_entity")

    def test_an_entity_in_both_sections_is_double_accounted(self):
        issue = _issue()
        issue["quiet_this_cycle"]["no_news"].append(
            {"entity_id": "merck", "name": "Merck", "cycles_quiet": 1}
        )
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        doubles = [f for f in result.blocking
                   if f.kind == "unaccounted_watchlist_entity" and f.where == "merck"]
        assert doubles and "double-accounted" in doubles[0].note


class TestEmptySection:
    def test_an_undeclared_empty_required_section_blocks(self):
        issue = _issue(tldr_bullets=[])
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "tldr_bullets" in _wheres(result, "empty_section")

    def test_an_empty_watchlist_passes_under_a_true_quiet_cycle(self):
        """Every tracked entity quiet is decidable from the issue alone — an empty
        watchlist is then the honest render, no declaration needed."""
        issue = _issue(watchlist=[])
        issue["quiet_this_cycle"]["no_news"] = [
            {"entity_id": "merck", "cycles_quiet": 1},
            {"entity_id": "pfizer", "cycles_quiet": 2},
        ]
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "empty_section" not in _kinds(result)

    def test_a_beat_failed_declaration_exempts_the_section_it_sits_in(self):
        # merck is NOT quiet (only pfizer is), so the implicit quiet_cycle route
        # does NOT fire — the empty watchlist passes ONLY via the beat_failed
        # declaration, which is what is under test.
        issue = _issue(watchlist=[])
        issue["degradations"] = {"watchlist": [
            {"kind": "beat_failed", "beats": ["ma_dealmaking"], "marker": "M&A unavailable"}
        ]}
        result = validate_issue(
            issue, state=_state("merck", "pfizer"), beats_failed=["ma_dealmaking"]
        )
        assert "empty_section" not in _kinds(result)

    def test_a_beat_failed_declaration_whose_beat_did_not_fail_still_blocks(self):
        issue = _issue(watchlist=[])
        issue["degradations"] = {"watchlist": [
            {"kind": "beat_failed", "beats": ["ma_dealmaking"], "marker": "M&A unavailable"}
        ]}
        # ma_dealmaking is NOT in beats_failed → the trigger is not mechanically
        # true; merck is not quiet so the implicit route is off too.
        result = validate_issue(issue, state=_state("merck", "pfizer"), beats_failed=[])
        assert "watchlist" in _wheres(result, "empty_section")

    def test_an_unregistered_degradation_kind_grants_no_exemption(self):
        issue = _issue(tldr_bullets=[])
        issue["degradations"] = {"tldr_bullets": [{"kind": "source_unreachable", "marker": "x"}]}
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "tldr_bullets" in _wheres(result, "empty_section")

    def test_a_researcher_self_report_is_not_a_trigger_and_blocks(self):
        """An empty section explained only by a researcher's errors[] self-report
        blocks — 'we don't know why this is empty' is exactly when blocking is
        right. errors[] rides in findings, never earns an issue-side exemption."""
        issue = _issue(tldr_bullets=[])
        issue["sources_and_method"]["errors"] = ["FDA newsroom timed out"]
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "tldr_bullets" in _wheres(result, "empty_section")

    def test_an_exemption_is_scoped_to_the_section_it_sits_in(self):
        """A declaration on tldr_bullets does not exempt an empty headline."""
        issue = _issue(tldr_bullets=[], headline=None)
        issue["degradations"] = {"tldr_bullets": [
            {"kind": "beat_failed", "beats": ["ma_dealmaking"]}
        ]}
        result = validate_issue(
            issue, state=_state("merck", "pfizer"), beats_failed=["ma_dealmaking"]
        )
        wheres = _wheres(result, "empty_section")
        assert "headline" in wheres  # not exempted
        assert "tldr_bullets" not in wheres  # exempted


class TestDerivedStatsMismatch:
    def test_an_empty_stats_is_a_draft_and_skips_silently(self):
        result = validate_issue(_issue(stats={}), state=_state("merck", "pfizer"))
        assert "derived_stats_mismatch" not in _kinds(result)

    def test_stats_disagreeing_with_the_arrays_blocks(self):
        issue = _issue(stats={
            "tracked_updates": 9,  # arrays say 1
            "tracked_quiet": 1, "new_on_radar": 0, "frontier_items": 0,
            "sources_cited": 2, "critic_catches": 0,
        })
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "stats.tracked_updates" in _wheres(result, "derived_stats_mismatch")

    def test_stats_matching_the_arrays_passes(self):
        issue = _issue(stats={
            "tracked_updates": 1, "tracked_quiet": 1, "new_on_radar": 0,
            "frontier_items": 0, "sources_cited": 2, "critic_catches": 0,
        })
        result = validate_issue(issue, state=_state("merck", "pfizer"))
        assert "derived_stats_mismatch" not in _kinds(result)


def _queue_item(**overrides):
    item = {
        "id": "q1", "asset": "drugX", "entity_ids": [],
        "first_expected_window": "2026-Q2", "expected_window": "2026-Q2",
        "window_source": _source(), "status": "pending", "slip_log": [],
        "sources": [_source()],
    }
    item.update(overrides)
    return item


class TestQueueTamper:
    def test_a_changed_first_expected_window_blocks(self):
        baseline = {"items": [_queue_item()]}
        issue = _issue()
        issue["catalyst_queue"]["items"] = [_queue_item(first_expected_window="2026-Q3")]
        result = validate_issue(issue, state=_state("merck", "pfizer"), queue_baseline=baseline)
        assert "queue_tamper" in _kinds(result)
        assert any("first_expected_window" in f.note for f in result.blocking)

    def test_a_changed_expected_window_without_a_slip_entry_blocks(self):
        baseline = {"items": [_queue_item()]}
        issue = _issue()
        issue["catalyst_queue"]["items"] = [_queue_item(expected_window="2026-Q4", slip_log=[])]
        result = validate_issue(issue, state=_state("merck", "pfizer"), queue_baseline=baseline)
        assert any("expected_window changed" in f.note for f in result.blocking)

    def test_a_changed_expected_window_with_a_matching_slip_entry_passes(self):
        baseline = {"items": [_queue_item()]}
        issue = _issue()
        issue["catalyst_queue"]["items"] = [_queue_item(
            expected_window="2026-Q4",
            slip_log=[{"from": "2026-Q2", "to": "2026-Q4", "date": "2026-07-01", "source": _source()}],
        )]
        result = validate_issue(issue, state=_state("merck", "pfizer"), queue_baseline=baseline)
        assert "queue_tamper" not in _kinds(result)

    def test_a_status_transition_with_no_source_blocks(self):
        baseline = {"items": [_queue_item()]}
        issue = _issue()
        issue["catalyst_queue"]["items"] = [_queue_item(
            status="delivered", sources=[], window_source=None, slip_log=[]
        )]
        result = validate_issue(issue, state=_state("merck", "pfizer"), queue_baseline=baseline)
        assert any("status changed" in f.note for f in result.blocking)

    def test_a_new_item_is_only_checked_for_internal_coherence(self):
        # No baseline entry for q2; first==expected so it is coherent.
        issue = _issue()
        issue["catalyst_queue"]["items"] = [_queue_item(id="q2")]
        result = validate_issue(issue, state=_state("merck", "pfizer"), queue_baseline={"items": []})
        assert "queue_tamper" not in _kinds(result)

    def test_a_new_item_with_a_silent_window_gap_blocks(self):
        issue = _issue()
        issue["catalyst_queue"]["items"] = [_queue_item(
            id="q2", first_expected_window="2026-Q1", expected_window="2026-Q3", slip_log=[]
        )]
        result = validate_issue(issue, state=_state("merck", "pfizer"), queue_baseline={"items": []})
        assert "queue_tamper" in _kinds(result)

    def test_no_baseline_means_the_cross_issue_check_skips(self):
        """Run #1 creates every value the cross-issue check guards — with no
        baseline the item is treated as new and only checked for internal
        coherence, so a coherent item passes with nothing to compare against."""
        issue = _issue()
        issue["catalyst_queue"]["items"] = [_queue_item()]  # first == expected: coherent
        result = validate_issue(issue, state=_state("merck", "pfizer"), queue_baseline=None)
        assert "queue_tamper" not in _kinds(result)


class TestContinuityBaselineExpired:
    def test_hitting_the_floor_files_an_advisory_never_a_block(self):
        result = validate_issue(
            _issue(), state=_state("merck", "pfizer"),
            queue_baseline=None, baseline_expired=True,
        )
        assert result.passed  # advisory does not block
        assert any(f.kind == "continuity_baseline_expired" for f in result.advisory)

    def test_run_one_empty_walk_is_silent(self):
        result = validate_issue(
            _issue(), state=_state("merck", "pfizer"),
            queue_baseline=None, baseline_expired=False,
        )
        assert result.advisory == ()


class TestTheRegister:
    def test_calendar_stale_is_registered_but_cannot_yet_earn_an_exemption(self):
        """Registered so the list and its enforcer stay in lockstep, but its
        trigger returns False until build 10 gives it a fact to read."""
        assert "calendar_stale" in DEGRADATION_REGISTER
        trigger = DEGRADATION_REGISTER["calendar_stale"]
        assert trigger({}, issue=_issue(), state=_state("merck"), beats_failed=[]) is False

    def test_all_four_kinds_are_registered(self):
        assert set(DEGRADATION_REGISTER) == {
            "thesis_unseeded", "beat_failed", "quiet_cycle", "calendar_stale"
        }

    def test_thesis_unseeded_trigger_reads_a_null_stance(self):
        trigger = DEGRADATION_REGISTER["thesis_unseeded"]
        dormant = _state("merck", beliefs=[{"id": "s", "stance": None}])
        seeded = _state("merck", beliefs=[{"id": "s", "stance": "x"}])
        assert trigger({}, issue=_issue(), state=dormant, beats_failed=[]) is True
        assert trigger({}, issue=_issue(), state=seeded, beats_failed=[]) is False


class TestCollectsEveryProblem:
    def test_every_check_runs_before_reporting(self):
        """The seam contract: all problems at once, so one retry fixes everything
        rather than peeling an onion."""
        issue = _issue(tldr_bullets=[])
        issue["headline"]["entity_refs"] = ["ghost"]
        issue["headline"]["sources"] = []
        result = validate_issue(issue, state=_state("merck", "pfizer", "roche"))
        assert {"uncited_claim", "dangling_entity", "unaccounted_watchlist_entity",
                "empty_section"} <= _kinds(result)
