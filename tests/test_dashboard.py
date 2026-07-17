"""Dashboard contract — the field paths dashboard/index.html reads must exist in
what the pipeline actually publishes.

The dashboard is JS, so it cannot be unit-tested in this suite. What CAN drift and
break it silently is the *shape* of the data: a renderer reads `issue.run.status`,
`catalyst_queue.items[].first_expected_window`, `critic_report.blocking_findings[]
.rebuttal.adjudication`, and if the manager or publish.py stops stamping those, the
page renders a blank where a marker should be. These tests pin the shape against
the real fixtures (two live artifacts) plus one synthetic surge/dispute issue, so a
schema change that would empty the dashboard fails here first.

They also document the two shapes the LIVE manager emits that DIVERGE from
docs/spec/07 — open_threads as strings, promotion_proposal as proposed_* — which
the dashboard renders defensively. If the manager is later fixed to match spec/07,
these asserts flip and this file is the record of why.
"""

import json
from pathlib import Path

import pytest

from researchswarm.publish import regenerate_manifest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dashboard"
REAL_PUBLISHED = FIXTURES / "real_published_2026-07-17.json"
REAL_STUB = FIXTURES / "real_failed_stub_2026-07-16.json"
SYNTHETIC_SURGE = FIXTURES / "synthetic_surge_2026-05-30.json"


def _load(path):
    return json.loads(path.read_text())


@pytest.fixture
def published():
    return _load(REAL_PUBLISHED)


@pytest.fixture
def stub():
    return _load(REAL_STUB)


@pytest.fixture
def surge():
    return _load(SYNTHETIC_SURGE)


# ── the fields the renderer dereferences on every published issue ──────────


def test_published_carries_the_run_status_the_banner_keys_on(published):
    run = published["issue"]["run"]
    assert run["status"] in {
        "published",
        "published_uncritiqued",
        "published_with_unresolved_findings",
    }
    # the uncritiqued banner prints WHY from critic_report.reason
    assert published["critic_report"]["reason"]


def test_headline_and_stats_paths_present(published):
    for key in ("title", "summary", "so_what", "confidence"):
        assert key in published["headline"]
    stats = published["stats"]
    for key in ("tracked_updates", "tracked_quiet", "new_on_radar",
                "frontier_items", "sources_cited", "critic_catches"):
        assert key in stats
    # previous_issue may be null (run #1) — the statline renders that as a state,
    # not a blank, so its mere presence is the contract.
    assert "previous_issue" in stats


def test_catalyst_queue_items_carry_the_slip_fields(published):
    items = published["catalyst_queue"]["items"]
    assert items, "the dashboard renders a catalyst section from these"
    for it in items:
        for key in ("asset", "catalyst", "status", "slip_log",
                    "first_expected_window", "expected_window",
                    "what_it_would_prove"):
            assert key in it, f"catalyst item missing {key}"


def test_watchlist_entries_have_the_stripe_and_absence_hooks(published):
    for e in published["watchlist"]:
        assert "thesis_impact" in e            # the stripe
        assert "research_angle" in e           # dormant marker renders here
        assert "degradation" in e              # beat_failed marker renders here
        assert isinstance(e.get("sources"), list)


def test_source_objects_carry_the_inline_tier(published):
    for s in published["headline"]["sources"]:
        assert s["tier"] in {"primary", "trade", "aggregator"}
        assert "publisher" in s and "published_at" in s


# ── the failed stub renders as a stub, not a blank ─────────────────────────


def test_stub_names_the_failure_stage(stub):
    assert stub["issue"]["run"]["status"] == "failed"
    assert stub["issue"]["failure"]["stage"]
    assert stub["issue"]["failure"]["detail"]
    assert stub["headline"] is None            # the renderer switches to stub view


# ── the synthetic issue exercises surge + dispute + markers ────────────────


def test_surge_and_dispute_shapes(surge):
    run = surge["issue"]["run"]
    assert run["surge"] == {"window": "ASCO 2026", "day": 2, "of": 5}
    assert run["status"] == "published_with_unresolved_findings"
    blocking = surge["critic_report"]["blocking_findings"]
    assert blocking, "the unresolved banner needs at least one blocking finding"
    reb = blocking[0]["rebuttal"]
    assert reb["adjudication"] in {"withdrawn", "reaffirmed"}   # critic-set, visible
    assert blocking[0]["source"]                                # the receipt
    # calendar_stale rides in advisory_findings — the persistent stale marker.
    kinds = {f["kind"] for f in surge["critic_report"]["advisory_findings"]}
    assert "calendar_stale" in kinds


# ── the manifest publish.py regenerates is what the dropdown reads ─────────


def test_regenerated_manifest_gives_the_dropdown_what_it_needs(tmp_path):
    issues = tmp_path / "issues"
    issues.mkdir()
    for src, name in (
        (REAL_PUBLISHED, "2026-07-17.json"),
        (REAL_STUB, "2026-07-16.json"),
        (SYNTHETIC_SURGE, "2026-05-30.json"),
    ):
        (issues / name).write_text(src.read_text())

    manifest = json.loads(regenerate_manifest(issues).read_text())
    rows = manifest["issues"]
    # newest first
    assert [r["id"] for r in rows] == ["2026-07-17", "2026-07-16", "2026-05-30"]
    # the stub is IN the dropdown, not an unexplained gap
    assert any(r["status"] == "failed" for r in rows)
    # surge rides only on the surge issue, and carries the window the dropdown groups by
    surge_rows = [r for r in rows if "surge" in r]
    assert len(surge_rows) == 1
    assert surge_rows[0]["surge"]["window"] == "ASCO 2026"


# ── documented divergences from spec/07 the dashboard renders defensively ──


def test_open_threads_diverge_from_spec_as_plain_strings(published):
    """spec/07 defines open_threads as objects {entity_id, thread, since,
    next_expected}; the live manager emits plain strings. The dashboard renders
    both. If this flips to objects, the manager was fixed — update the renderer's
    string branch note, not this assert blindly."""
    threads = published["quiet_this_cycle"]["open_threads"]
    assert threads and all(isinstance(t, str) for t in threads)


def test_promotion_proposal_diverges_from_spec_shape(published):
    """spec/07 defines promotion_proposal as {promote_to_watchlist, reason}; the
    live manager emits {proposed_entity_id, proposed_tier, proposed_priority,
    reason}. The dashboard handles both shapes."""
    pp = published["new_on_radar"][0]["promotion_proposal"]
    assert "promote_to_watchlist" not in pp
    assert "proposed_entity_id" in pp
