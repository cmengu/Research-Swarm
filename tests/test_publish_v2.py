"""The v2 publisher — the three files a detective emits.

Covers `publish.run_publish_stage_v2` and its parts against spec/08's three new
sections: "The program registry", "The issue manifest", and "The data layer".

The tests that matter here are the ones about the REGISTRY, because it is the
only genuinely new object: issues and manifests already existed in v1 and their
rules are inherited. The registry's rules are a join, and a join has two failure
modes worth pinning — a row that should exist and doesn't (the never-run
program), and a row that shouldn't exist and does (the stale row a patch would
leave). Both are asserted directly.

Fully deterministic: no network, no model call, no git. `publish.py` never shells
out on the v2 path, which is itself part of the sole-writer invariant these tests
are allowed to rely on.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from researchswarm.publish import (
    _manifest_flags_v2,
    regenerate_manifest_v2,
    run_publish_stage_v2,
    write_registry,
)
from researchswarm.stub import PublishedIssueExists

NOW = datetime(2026, 7, 18, 7, 41)
PROGRAM = "hmbd-001"


# ---------------------------------------------------------------------------
# Fixtures — a repo root with config on one side and issues on the other
# ---------------------------------------------------------------------------


def _write_program_toml(root: Path, program_id: str, *, name=None, sponsor="Acme Bio",
                        mechanism="HER3 signalling blockade") -> None:
    """A minimal but REAL program config — the left side of the registry's join."""
    path = root / "config" / "programs" / f"{program_id}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[program]\n"
        f'id = "{program_id}"\n'
        f'name = "{name or program_id.upper()}"\n'
        f'sponsor = "{sponsor}"\n'
        'modality = "antibody"\n'
        'target = "HER3 (ERBB3)"\n'
        'moa = "signalling_blockade"\n'
        f'mechanism = "{mechanism}"\n'
    )


def _issue(issue_id: str, *, program_id=PROGRAM, status="published", **extra) -> dict:
    """A v2 issue skeleton — only the fields the publisher actually reads."""
    issue = {
        "schema_version": "2.0.0",
        "issue": {
            "id": issue_id,
            "program_id": program_id,
            "published_at": f"{issue_id}T07:41:00+08:00",
            "coverage_window": {"from": "2026-07-14", "to": issue_id},
            "run": {"run_id": "run_x", "status": status},
        },
        "headline": {"title": f"headline for {issue_id}"},
        "stats": {
            "competitors_moved": 2,
            "competitors_quiet": 1,
            "newly_discovered": 1,
            "indications_covered": 2,
            "sources_cited": 11,
            "previous_issue": None,
        },
        "competitors": [],
        "indications": [],
        "critic_report": {},
        "sources_and_method": {},
    }
    issue.update(extra)
    return issue


def _stub(issue_id: str, program_id=PROGRAM) -> dict:
    """A failed-run stub: same envelope, status failed, no headline, no stats."""
    stub = _issue(issue_id, program_id=program_id, status="failed")
    stub["headline"] = None
    stub["stats"] = {}
    stub["issue"]["failure"] = {"stage": "research", "detail": "every aperture dead"}
    return stub


def _put(root: Path, issue: dict) -> Path:
    """Drop an issue straight onto disk, bypassing the publisher — how these
    tests set up a history the publisher then has to reconcile against."""
    path = root / "issues" / issue["issue"]["program_id"] / f"{issue['issue']['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(issue, indent=2) + "\n")
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    _write_program_toml(tmp_path, PROGRAM, name="HMBD-001", sponsor="Hummingbird Bioscience")
    return tmp_path


def _publish(root: Path, issue: dict):
    return run_publish_stage_v2(
        root, issue=issue, program_id=issue["issue"]["program_id"],
        run_id="run_20260718_0700", now=NOW,
    )


def _read(path: Path) -> dict:
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------------------
# The registry — config ⋈ state, config on the left
# ---------------------------------------------------------------------------


class TestTheRegistry:
    def test_a_program_that_has_never_run_still_appears(self, root):
        """Spec/08: *"A program exists because it has a `.toml`, not because it has
        published."* The switcher must show it, over a "no issues yet" empty
        state — a detective that exists but is invisible is exactly the silent
        absence this design refuses."""
        _write_program_toml(root, "never-run", name="NEVER-1", sponsor="Ghost Bio")
        write_registry(root, generated_at=NOW.isoformat())

        rows = {r["program_id"]: r for r in _read(root / "issues" / "index.json")["programs"]}
        assert set(rows) == {PROGRAM, "never-run"}
        assert rows["never-run"]["latest_issue"] is None
        assert rows["never-run"]["latest_published_at"] is None
        assert rows["never-run"]["issue_count"] == 0
        assert rows["never-run"]["flags"] == []

    def test_the_five_second_test_fields_ride_in_the_registry(self, root):
        """`sponsor` and `mechanism` are here on purpose (spec/08): they let the
        identity card paint before the issue is fetched."""
        write_registry(root, generated_at=NOW.isoformat())
        row = _read(root / "issues" / "index.json")["programs"][0]
        assert row["display_name"] == "HMBD-001"
        assert row["sponsor"] == "Hummingbird Bioscience"
        assert row["mechanism"] == "HER3 signalling blockade"

    def test_it_is_regenerated_wholesale_not_row_patched(self, root):
        """The load-bearing rule. A run touches ONE program but rewrites EVERY
        row, so a stale row is impossible BY CONSTRUCTION rather than by locking.

        Set up: a registry containing a row for a program whose `.toml` has since
        been deleted, and a row carrying stale counts for a program that has since
        published. A patching implementation preserves the ghost and updates only
        what it touched. A wholesale regeneration drops the ghost and refreshes
        the untouched program too."""
        _write_program_toml(root, "sibling", name="SIB-1")
        _put(root, _issue("2026-07-10", program_id="sibling"))
        (root / "issues").mkdir(parents=True, exist_ok=True)
        (root / "issues" / "index.json").write_text(json.dumps({
            "generated_at": "2026-01-01T00:00:00",
            "programs": [
                {"program_id": "deleted-program", "display_name": "GHOST",
                 "latest_issue": "2026-01-01", "issue_count": 9, "flags": []},
                {"program_id": "sibling", "display_name": "SIB-1",
                 "latest_issue": None, "issue_count": 0, "flags": []},
            ],
        }))

        _publish(root, _issue("2026-07-18"))  # touches hmbd-001 ONLY

        rows = {r["program_id"]: r for r in _read(root / "issues" / "index.json")["programs"]}
        # The ghost is gone: config is the left side, and it has no .toml.
        assert "deleted-program" not in rows
        # The UNTOUCHED sibling was refreshed anyway — that is "wholesale".
        assert rows["sibling"]["latest_issue"] == "2026-07-10"
        assert rows["sibling"]["issue_count"] == 1

    def test_latest_is_the_newest_issue_and_counts_the_whole_history(self, root):
        _put(root, _issue("2026-05-18"))
        _put(root, _issue("2026-06-18"))
        _publish(root, _issue("2026-07-18"))

        row = _read(root / "issues" / "index.json")["programs"][0]
        assert row["latest_issue"] == "2026-07-18"
        assert row["latest_published_at"] == "2026-07-18T07:41:00+08:00"
        assert row["issue_count"] == 3

    def test_the_per_program_manifest_is_not_counted_as_an_issue(self, root):
        """index.json lives in the same directory; it is a derived file, not a
        member of the history it describes."""
        _publish(root, _issue("2026-07-18"))
        assert (root / "issues" / PROGRAM / "index.json").exists()
        assert _read(root / "issues" / "index.json")["programs"][0]["issue_count"] == 1

    def test_a_broken_program_config_does_not_take_the_registry_down(self, root):
        """A registry failure is graded whole-page (spec/08 "The data layer"), so
        one unparseable `.toml` must not make every other detective unreachable."""
        (root / "config" / "programs" / "broken.toml").write_text("[program]\nname = 'no id'\n")
        write_registry(root, generated_at=NOW.isoformat())
        rows = [r["program_id"] for r in _read(root / "issues" / "index.json")["programs"]]
        assert rows == [PROGRAM]


class TestAStubReachesTheRegistry:
    """Spec/08: *stubs appear* — and at program altitude it should fall out FREE
    of the wholesale join, with no special case in the code. These tests pin that
    it does, so a future refactor cannot quietly reintroduce one."""

    def test_a_program_whose_only_run_failed_still_appears(self, root):
        _put(root, _stub("2026-07-18"))
        write_registry(root, generated_at=NOW.isoformat())

        row = _read(root / "issues" / "index.json")["programs"][0]
        assert row["latest_issue"] == "2026-07-18"
        assert row["issue_count"] == 1
        # The failure is visible in the switcher, not just in that program's list.
        assert "failed" in row["flags"]

    def test_the_stub_is_in_the_dropdown_with_status_failed(self, root):
        _put(root, _stub("2026-07-18"))
        regenerate_manifest_v2(root / "issues" / PROGRAM, PROGRAM, generated_at=NOW.isoformat())

        entry = _read(root / "issues" / PROGRAM / "index.json")["issues"][0]
        assert entry["status"] == "failed"
        # A stub has no headline, and null says so rather than faking one.
        assert entry["headline_title"] is None
        # A stub's stats is {}, so the subset is empty, not a row of nulls.
        assert entry["stats"] == {}


# ---------------------------------------------------------------------------
# The per-program manifest
# ---------------------------------------------------------------------------


class TestTheManifest:
    def test_newest_first(self, root):
        for issue_id in ("2026-05-18", "2026-07-18", "2026-06-18"):
            _put(root, _issue(issue_id))
        regenerate_manifest_v2(root / "issues" / PROGRAM, PROGRAM, generated_at=NOW.isoformat())

        manifest = _read(root / "issues" / PROGRAM / "index.json")
        assert manifest["program_id"] == PROGRAM
        assert [e["id"] for e in manifest["issues"]] == [
            "2026-07-18", "2026-06-18", "2026-05-18",
        ]

    def test_the_issues_on_disk_win_and_the_manifest_regenerates(self, root):
        """Spec/08: *"If it disagrees with the issues on disk, the issues win and
        the manifest is regenerated."* Inherited from v1 unchanged."""
        _put(root, _issue("2026-07-18"))
        (root / "issues" / PROGRAM / "index.json").write_text(json.dumps({
            "program_id": PROGRAM, "generated_at": "2026-01-01T00:00:00",
            "issues": [{"id": "2026-01-01", "status": "published"}],
        }))
        regenerate_manifest_v2(root / "issues" / PROGRAM, PROGRAM, generated_at=NOW.isoformat())

        assert [e["id"] for e in _read(root / "issues" / PROGRAM / "index.json")["issues"]] == [
            "2026-07-18"
        ]

    def test_the_stats_subset_is_the_v2_triage_counts(self, root):
        _publish(root, _issue("2026-07-18"))
        entry = _read(root / "issues" / PROGRAM / "index.json")["issues"][0]
        assert entry["stats"] == {
            "competitors_moved": 2,
            "sources_cited": 11,
            "indications_covered": 2,
            "newly_discovered": 1,
        }
        # `previous_issue` and the rest stay in the issue — the manifest is a
        # triage row, not a second copy of the stats block.
        assert "previous_issue" not in entry["stats"]

    def test_surge_rides_only_when_present(self, root):
        baseline = _issue("2026-07-17")
        surged = _issue("2026-07-18")
        surged["issue"]["run"]["surge"] = {"window": "ESMO 2026", "day": 2, "of": 5}
        _put(root, baseline)
        _put(root, surged)
        regenerate_manifest_v2(root / "issues" / PROGRAM, PROGRAM, generated_at=NOW.isoformat())

        entries = {e["id"]: e for e in _read(root / "issues" / PROGRAM / "index.json")["issues"]}
        assert entries["2026-07-18"]["surge"]["window"] == "ESMO 2026"
        assert "surge" not in entries["2026-07-17"]  # absent, never null

    def test_an_unreadable_issue_is_skipped_not_fatal(self, root):
        _put(root, _issue("2026-07-18"))
        (root / "issues" / PROGRAM / "2026-07-17.json").write_text("{ not json")
        regenerate_manifest_v2(root / "issues" / PROGRAM, PROGRAM, generated_at=NOW.isoformat())
        assert [e["id"] for e in _read(root / "issues" / PROGRAM / "index.json")["issues"]] == [
            "2026-07-18"
        ]


# ---------------------------------------------------------------------------
# Flags — typed fields, never a regex over prose
# ---------------------------------------------------------------------------


class TestFlagsAreTyped:
    def test_degradation_kinds_are_collected_wherever_they_hang(self, root):
        """Spec/08 "Vocabulary homes": the chrome keys on `degradation.kind`, and
        spec/07 hangs a degradation off whatever object the absence belongs to."""
        issue = _issue("2026-07-18")
        issue["competitors"] = [
            {"entity_id": "a", "degradation": None},
            {"entity_id": "b", "degradation": {"kind": "china_feed_partial", "marker": "..."}},
        ]
        issue["indications"] = [
            {"id": "nrg1", "treatment_landscape": {
                "degradation": {"kind": "arena_scan_dormant", "marker": "..."}}},
        ]
        assert _manifest_flags_v2(issue) == ["arena_scan_dormant", "china_feed_partial"]

    def test_findings_rot_and_run_status_all_contribute(self, root):
        issue = _issue("2026-07-18", status="published_uncritiqued")
        issue["critic_report"] = {
            "advisory_findings": [{"kind": "calendar_stale", "where": "sources_and_method"}],
            "validator_report": {"findings": [{"kind": "arena_scan_failed"}]},
        }
        issue["sources_and_method"] = {"interest_list": {"rot_status": "stale"}}
        assert _manifest_flags_v2(issue) == [
            "calendar_stale", "arena_scan_failed", "interest_list_stale",
            "published_uncritiqued",
        ]

    def test_an_unknown_kind_is_emitted_not_dropped(self, root):
        """Spec/08: an unknown kind must render VISIBLY — a marker the page does
        not recognise is exactly when the reader most needs to know one was
        raised. Filtering it out in Python would defeat that before the page ever
        saw it."""
        issue = _issue("2026-07-18")
        issue["competitors"] = [{"degradation": {"kind": "some_future_kind"}}]
        assert _manifest_flags_v2(issue) == ["some_future_kind"]

    def test_a_clean_published_issue_raises_nothing(self, root):
        assert _manifest_flags_v2(_issue("2026-07-18")) == []

    def test_no_flag_comes_from_the_marker_prose(self, root):
        """The retired regex, pinned: a `marker` string naming a kind, with no
        typed `kind` beside it, must raise nothing. v3 conceded its regex was
        heuristic and that a reworded marker silently degraded to prose."""
        issue = _issue("2026-07-18")
        issue["competitors"] = [{"degradation": {"marker": "china_feed_partial happened here"}}]
        assert _manifest_flags_v2(issue) == []


# ---------------------------------------------------------------------------
# Immutability, and the recipe as a whole
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_a_published_issue_is_never_overwritten(self, root):
        """Spec/08: *"Published issues are immutable. A later run never edits an
        earlier issue."* The guard is `stub.check_overwritable`, shared with v1
        and the stub writer so "already published" means one thing everywhere."""
        first = _issue("2026-07-18")
        first["headline"]["title"] = "the real issue"
        _publish(root, first)

        with pytest.raises(PublishedIssueExists):
            _publish(root, _issue("2026-07-18", headline={"title": "an impostor"}))

        on_disk = _read(root / "issues" / PROGRAM / "2026-07-18.json")
        assert on_disk["headline"]["title"] == "the real issue"

    def test_a_same_day_rerun_may_replace_its_own_stub(self, root):
        """The single carve-out: retrying a failure that then succeeds is the
        desired behaviour, and a stub is not a published issue."""
        _put(root, _stub("2026-07-18"))
        _publish(root, _issue("2026-07-18"))

        assert _read(root / "issues" / PROGRAM / "2026-07-18.json")["issue"]["run"]["status"] == (
            "published"
        )
        entry = _read(root / "issues" / PROGRAM / "index.json")["issues"][0]
        assert entry["status"] == "published"

    def test_a_refused_overwrite_leaves_the_derived_files_untouched(self, root):
        """The issue is written FIRST, so a refusal raises before anything lands —
        a clean fail, not a half-published one."""
        _publish(root, _issue("2026-07-18"))
        before = (root / "issues" / "index.json").read_text()

        with pytest.raises(PublishedIssueExists):
            _publish(root, _issue("2026-07-18"))

        assert (root / "issues" / "index.json").read_text() == before


class TestTheRecipe:
    def test_it_writes_all_three_files_and_returns_them(self, root):
        result = _publish(root, _issue("2026-07-18"))
        assert result.issue_path == root / "issues" / PROGRAM / "2026-07-18.json"
        assert result.manifest_path == root / "issues" / PROGRAM / "index.json"
        assert result.registry_path == root / "issues" / "index.json"
        assert all(p.exists() for p in result.paths)

    def test_the_derived_files_include_the_issue_just_written(self, root):
        """Order is load-bearing: the issue lands first so both derived files are
        projections of a disk state that already contains it."""
        _publish(root, _issue("2026-07-18"))
        assert _read(root / "issues" / PROGRAM / "index.json")["issues"][0]["id"] == "2026-07-18"
        assert _read(root / "issues" / "index.json")["programs"][0]["latest_issue"] == "2026-07-18"

    def test_all_three_artifacts_share_one_generated_at(self, root):
        """The run's own clock is threaded through, so the artifacts agree on when
        the run happened instead of drifting across three now() calls."""
        _publish(root, _issue("2026-07-18"))
        assert _read(root / "issues" / PROGRAM / "index.json")["generated_at"] == NOW.isoformat()
        assert _read(root / "issues" / "index.json")["generated_at"] == NOW.isoformat()

    def test_publishing_one_program_does_not_disturb_anothers_manifest(self, root):
        """The registry is cross-program; the MANIFEST is not. A run rewrites every
        registry row but only its own program's dropdown."""
        _write_program_toml(root, "sibling")
        _put(root, _issue("2026-07-10", program_id="sibling"))
        regenerate_manifest_v2(root / "issues" / "sibling", "sibling", generated_at="2026-07-10T00:00:00")
        before = (root / "issues" / "sibling" / "index.json").read_text()

        _publish(root, _issue("2026-07-18"))

        assert (root / "issues" / "sibling" / "index.json").read_text() == before
