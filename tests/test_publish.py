"""Stage 6 — publish: per-step pure-function tests.

The five ordered steps of the publish recipe, each exercised on its own: derive
stats (incl. the previous_issue walk and the run-#1 null), the immutability
guard, manifest regeneration, and the three state edits. The end-to-end run.py
wiring lives in test_run_cli.py, where the whole pipeline lands as a real commit.
"""

import json
from datetime import datetime

import pytest

from researchswarm.publish import (
    apply_state_edits,
    derive_full_stats,
    git_commit_run,
    regenerate_manifest,
    run_publish_stage,
    stamp_run_fields,
    write_issue,
)
from researchswarm.state import load_state
from researchswarm.stub import PublishedIssueExists, write_failed_stub

NOW = datetime(2026, 7, 17, 0, 45, 3)
RUN_ID = "run_20260717_0045"


def _issue(**overrides):
    """A minimal but valid published-issue-shaped draft, stats still {} as the
    manager left it. Overrides splice in the arrays a given test needs."""
    base = {
        "schema_version": "1.0.0",
        "issue": {
            "id": "2026-07-17",
            "published_at": "2026-07-17T00:45:03+08:00",
            "coverage_window": {"from": "2026-07-16", "to": "2026-07-17"},
            "run": {
                "run_id": RUN_ID,
                "status": "published",
                "critic_verdict": "pass",
                "critic_retries": 0,
                "thesis_version": 2,
            },
        },
        "headline": {
            "title": "Merck resets ADC pricing", "summary": "s", "so_what": "matters",
            "entity_refs": ["merck"], "confidence": "high",
            "sources": [_src("a"), _src("b")],
        },
        "stats": {},
        "tldr_bullets": [{"text": "t", "entity_refs": ["merck"], "priority": "high"}],
        "catalyst_queue": {"snapshot_of": "state/catalyst-queue.json", "recut_at": None, "items": []},
        "watchlist": [],
        "quiet_this_cycle": {"no_news": [], "critic_catches": [], "open_threads": []},
        "new_on_radar": [],
        "themes_and_signals": [],
        "elsewhere_on_frontier": [],
        "thesis_updates": [],
        "critic_report": {"verdict": "not_run", "validator_report": {"passed": True, "retries_used": 0, "findings": []}},
        "sources_and_method": {"beats_run": [], "beats_failed": [], "source_tier_counts": {}, "paywalled_flagged": []},
    }
    base.update(overrides)
    return base


def _src(tag):
    return {"url": f"https://ex.com/{tag}", "publisher": "Endpoints", "tier": "primary", "published_at": "2026-07-16"}


def _fake_repo(tmp_path):
    """A repo skeleton with the real seeded state files and an issues/ dir."""
    import shutil
    root_src = __import__("pathlib").Path(__file__).resolve().parent.parent
    shutil.copytree(root_src / "state", tmp_path / "state")
    (tmp_path / "issues").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Step 1 — derive stats
# ---------------------------------------------------------------------------


class TestDeriveStats:
    def test_counts_come_from_the_arrays(self, tmp_path):
        issue = _issue(
            watchlist=[{"entity_id": "merck", "sources": [_src("m")]}],
            new_on_radar=[{"entity_id": "callio", "sources": [_src("c")]}],
            elsewhere_on_frontier=[{"actor": "FDA", "sources": [_src("f")]}],
            quiet_this_cycle={
                "no_news": [{"entity_id": "pfizer"}, {"entity_id": "gsk"}],
                "critic_catches": [{"claim": "x", "sources": [_src("k")]}],
                "open_threads": [],
            },
        )
        stats = derive_full_stats(issue, tmp_path / "issues")
        assert stats["tracked_updates"] == 1
        assert stats["tracked_quiet"] == 2
        assert stats["new_on_radar"] == 1
        assert stats["frontier_items"] == 1
        assert stats["critic_catches"] == 1
        # headline 2 + watchlist 1 + radar 1 + frontier 1 + catch 1 = 6
        assert stats["sources_cited"] == 6

    def test_previous_issue_null_on_run_one(self, tmp_path):
        """No covering issue on disk — previous_issue is null. TRUE, not a flag."""
        (tmp_path / "issues").mkdir()
        stats = derive_full_stats(_issue(), tmp_path / "issues")
        assert stats["previous_issue"] is None

    def test_previous_issue_walks_past_a_stub(self, tmp_path):
        issues = tmp_path / "issues"
        issues.mkdir()
        (issues / "2026-07-13.json").write_text(json.dumps(
            {"issue": {"id": "2026-07-13", "run": {"status": "published"}}}
        ))
        (issues / "2026-07-16.json").write_text(json.dumps(
            {"issue": {"id": "2026-07-16", "run": {"status": "failed"}}}
        ))
        stats = derive_full_stats(_issue(), issues)
        # The stub is transparent — previous_issue is the last one that covered days.
        assert stats["previous_issue"] == "2026-07-13"

    def test_stamp_overwrites_manager_run_fields(self):
        issue = _issue()
        stamp_run_fields(issue, {"tracked_updates": 0})
        assert issue["stats"] == {"tracked_updates": 0}
        assert issue["issue"]["run"]["status"] == "published_uncritiqued"
        assert issue["issue"]["run"]["critic_verdict"] == "not_run"


# ---------------------------------------------------------------------------
# Step 2 — the immutable issue file
# ---------------------------------------------------------------------------


class TestWriteIssueImmutability:
    def test_writes_the_issue(self, tmp_path):
        (tmp_path / "issues").mkdir()
        path = write_issue(tmp_path, _issue())
        assert path == tmp_path / "issues" / "2026-07-17.json"
        assert json.loads(path.read_text())["issue"]["id"] == "2026-07-17"
        assert path.read_text().endswith("}\n")  # trailing newline preserved

    def test_publish_over_a_published_issue_raises(self, tmp_path):
        (tmp_path / "issues").mkdir()
        write_issue(tmp_path, _issue(stats={"tracked_updates": 0}))
        # Mark it published and try to overwrite — immutability blocks.
        published = json.loads((tmp_path / "issues" / "2026-07-17.json").read_text())
        published["issue"]["run"]["status"] = "published_uncritiqued"
        (tmp_path / "issues" / "2026-07-17.json").write_text(json.dumps(published))
        with pytest.raises(PublishedIssueExists):
            write_issue(tmp_path, _issue())

    def test_publish_over_its_own_stub_replaces_it(self, tmp_path):
        """A retried failure that now succeeds is the desired behaviour: the day's
        earlier FAILED stub is replaced by the real issue."""
        write_failed_stub(tmp_path, run_id=RUN_ID, now=NOW, window={"from": "a", "to": "b"},
                          stage="research", detail="died")
        path = write_issue(tmp_path, _issue())
        assert json.loads(path.read_text())["issue"]["run"]["status"] == "published"


# ---------------------------------------------------------------------------
# Step 3 — manifest regeneration
# ---------------------------------------------------------------------------


class TestRegenerateManifest:
    def _seed(self, issues, name, *, status, headline=True, beats_failed=None, stats=None):
        payload = {
            "issue": {"id": name, "published_at": f"{name}T07:00:00+08:00",
                      "coverage_window": {"from": name, "to": name},
                      "run": {"status": status}},
            "headline": {"title": f"h-{name}"} if headline else None,
            "stats": stats if stats is not None else {},
            "sources_and_method": {"beats_failed": beats_failed or []},
            "critic_report": {},
        }
        (issues / f"{name}.json").write_text(json.dumps(payload))

    def test_newest_first_and_stubs_appear(self, tmp_path):
        issues = tmp_path / "issues"
        issues.mkdir()
        self._seed(issues, "2026-07-13", status="published", stats={"tracked_updates": 9, "sources_cited": 34})
        self._seed(issues, "2026-07-16", status="failed", headline=False)
        path = regenerate_manifest(issues, generated_at="2026-07-17T00:45:00")

        manifest = json.loads(path.read_text())
        ids = [e["id"] for e in manifest["issues"]]
        assert ids == ["2026-07-16", "2026-07-13"]  # newest first
        stub, real = manifest["issues"]
        assert stub["status"] == "failed"
        assert stub["headline_title"] is None  # a stub has no headline
        assert stub["stats"] == {}  # empty stats → empty subset, not a row of nulls
        assert real["headline_title"] == "h-2026-07-13"
        assert real["stats"] == {"tracked_updates": 9, "sources_cited": 34}

    def test_flags_carry_beats_failed_and_advisories(self, tmp_path):
        issues = tmp_path / "issues"
        issues.mkdir()
        payload = {
            "issue": {"id": "2026-07-17", "published_at": "x", "coverage_window": {},
                      "run": {"status": "published_uncritiqued"}},
            "headline": {"title": "h"},
            "stats": {"tracked_updates": 1, "sources_cited": 2},
            "sources_and_method": {"beats_failed": ["ma_dealmaking"]},
            "critic_report": {
                "advisory_findings": [{"kind": "calendar_stale"}],
                "validator_report": {"findings": [{"kind": "continuity_baseline_expired"}]},
            },
        }
        (issues / "2026-07-17.json").write_text(json.dumps(payload))
        manifest = json.loads(regenerate_manifest(issues).read_text())
        assert manifest["issues"][0]["flags"] == [
            "calendar_stale", "continuity_baseline_expired", "beats_failed"
        ]

    def test_surge_rides_only_when_present(self, tmp_path):
        issues = tmp_path / "issues"
        issues.mkdir()
        self._seed(issues, "2026-07-13", status="published")
        surge = {"issue": {"id": "2026-07-14", "published_at": "x", "coverage_window": {},
                           "run": {"status": "published", "surge": {"window": "ASCO 2026", "day": 2, "of": 5}}},
                 "headline": {"title": "h"}, "stats": {}, "sources_and_method": {}, "critic_report": {}}
        (issues / "2026-07-14.json").write_text(json.dumps(surge))
        manifest = json.loads(regenerate_manifest(issues).read_text())
        assert "surge" not in manifest["issues"][1]  # baseline run
        assert manifest["issues"][0]["surge"] == {"window": "ASCO 2026", "day": 2, "of": 5}


# ---------------------------------------------------------------------------
# Step 4 — state edits
# ---------------------------------------------------------------------------


class TestPromotions:
    def test_accepted_proposal_appends_entity_and_drift_log(self, tmp_path):
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        issue = _issue(new_on_radar=[{
            "entity_id": "callio_tx", "name": "Callio Therapeutics", "type": "startup",
            "priority": "medium", "categories": ["funding"], "sources": [_src("c")],
            "promotion_proposal": {"promote_to_watchlist": True, "reason": "Second dual-payload financing."},
        }])
        apply_state_edits(root, issue, state, RUN_ID, NOW)

        watchlist = json.loads((root / "state" / "watchlist.json").read_text())
        promoted = [e for e in watchlist["entities"] if e["entity_id"] == "callio_tx"]
        assert promoted and promoted[0]["tier"] == "frontier_asset"  # documented default
        assert promoted[0]["why_tracked"] == "Second dual-payload financing."
        assert promoted[0]["watch_for"] == ["funding"]
        drift = watchlist["drift_log"][-1]
        assert drift["action"] == "promoted" and drift["run_id"] == RUN_ID
        assert watchlist["last_edited_by"] == "loop"

    def test_duplicate_entity_id_is_skipped(self, tmp_path):
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        before = len(state.watchlist["entities"])
        issue = _issue(new_on_radar=[{
            "entity_id": "merck", "name": "Merck", "type": "big_pharma", "priority": "high",
            "categories": ["deal_ma"], "sources": [_src("m")],
            "promotion_proposal": {"promote_to_watchlist": True, "reason": "already here"},
        }])
        paths = apply_state_edits(root, issue, state, RUN_ID, NOW)
        # Nothing applied — merck already exists, so the file is not even rewritten.
        assert (root / "state" / "watchlist.json") not in paths
        assert len(state.watchlist["entities"]) == before

    def test_unaccepted_proposal_is_not_promoted(self, tmp_path):
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        issue = _issue(new_on_radar=[{
            "entity_id": "callio_tx", "name": "Callio", "type": "startup", "priority": "low",
            "categories": [], "sources": [_src("c")],
            "promotion_proposal": {"promote_to_watchlist": False, "reason": "not yet"},
        }])
        apply_state_edits(root, issue, state, RUN_ID, NOW)
        assert "callio_tx" not in load_state(root / "state").entity_ids


class TestThesisRevisions:
    def test_active_slot_revises_with_drift_log_and_version_bump(self, tmp_path):
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        v0 = state.thesis["version"]
        issue = _issue(thesis_updates=[{
            "change": "amended", "field": "pharma-ma-appetite",
            "before": "old", "after": "Acquirers are paying pre-readout premiums.",
            "triggered_by": ["merck", "callio_tx"],
        }])
        apply_state_edits(root, issue, state, RUN_ID, NOW)

        thesis = json.loads((root / "state" / "thesis.json").read_text())
        slot = next(b for b in thesis["beliefs"] if b["id"] == "pharma-ma-appetite")
        assert slot["stance"] == "Acquirers are paying pre-readout premiums."
        entry = slot["drift_log"][-1]
        assert entry["to_stance"] == slot["stance"] and entry["cycle_id"] == RUN_ID
        assert entry["trigger"] == ["merck", "callio_tx"]
        assert thesis["version"] == v0 + 1
        assert thesis["last_edited_by"] == "loop" and thesis["last_evolved_at"] == "2026-07-17"

    def test_dormant_slot_is_refused(self, tmp_path):
        """The loop must NEVER author a stance into a dormant (null-stance) slot —
        that is a contract violation upstream, skipped loudly, not applied."""
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        # Force a slot dormant.
        state.thesis["beliefs"][0]["stance"] = None
        dormant_id = state.thesis["beliefs"][0]["id"]
        v0 = state.thesis["version"]
        issue = _issue(thesis_updates=[{
            "change": "added", "field": dormant_id, "before": None,
            "after": "an improvised opinion", "triggered_by": [],
        }])
        paths = apply_state_edits(root, issue, state, RUN_ID, NOW)

        assert state.thesis["beliefs"][0]["stance"] is None  # untouched
        assert state.thesis["version"] == v0  # no bump
        assert (root / "state" / "thesis.json") not in paths


class TestQueueTransitions:
    def _state_item(self, state):
        return state.catalyst_queue["queue"][0]

    def test_status_transition_needs_a_new_source(self, tmp_path):
        """No source new to state → no transition. The validator's own rule."""
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        item = self._state_item(state)
        snap = dict(item)
        snap["status"] = "delivered"  # a flip, but no new citation
        issue = _issue(catalyst_queue={"items": [snap]})
        paths = apply_state_edits(root, issue, state, RUN_ID, NOW)
        assert self._state_item(state)["status"] == "pending"  # unchanged
        assert (root / "state" / "catalyst-queue.json") not in paths

    def test_status_transition_with_new_source_applies(self, tmp_path):
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        item = self._state_item(state)
        snap = dict(item)
        snap["status"] = "delivered"
        snap["sources"] = [_src("readout")]  # a citation state did not carry
        issue = _issue(catalyst_queue={"items": [snap]})
        apply_state_edits(root, issue, state, RUN_ID, NOW)

        queue = json.loads((root / "state" / "catalyst-queue.json").read_text())
        assert queue["queue"][0]["status"] == "delivered"
        drift = queue["drift_log"][-1]
        assert drift["action"] == "transition" and drift["run_id"] == RUN_ID
        assert queue["version"] == state.catalyst_queue["version"]  # written value == in-memory bump

    def test_first_expected_window_is_never_propagated(self, tmp_path):
        """An issue whose snapshot changed first_expected_window is upstream
        tamper — publish skips the whole item, never launders the immutable field."""
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        item = self._state_item(state)
        snap = dict(item)
        snap["first_expected_window"] = "2026-Q4"  # was null
        snap["status"] = "slipped"
        snap["sources"] = [_src("new")]
        issue = _issue(catalyst_queue={"items": [snap]})
        paths = apply_state_edits(root, issue, state, RUN_ID, NOW)

        assert self._state_item(state)["first_expected_window"] is None
        assert self._state_item(state)["status"] == "pending"  # item skipped entirely
        assert (root / "state" / "catalyst-queue.json") not in paths

    def test_window_slip_appends_to_slip_log(self, tmp_path):
        root = _fake_repo(tmp_path)
        state = load_state(root / "state")
        item = self._state_item(state)
        # Give the state item an established window so a slip is a real transition.
        item["first_expected_window"] = "2026-Q2"
        item["expected_window"] = "2026-Q2"
        snap = dict(item)
        snap["expected_window"] = "2026-Q4"
        snap["sources"] = [_src("slip-evidence")]
        snap["slip_log"] = [{"from": "2026-Q2", "to": "2026-Q4", "date": "2026-07-17", "source": _src("slip-evidence")}]
        issue = _issue(catalyst_queue={"items": [snap]})
        apply_state_edits(root, issue, state, RUN_ID, NOW)

        queue = json.loads((root / "state" / "catalyst-queue.json").read_text())
        it = queue["queue"][0]
        assert it["expected_window"] == "2026-Q4"
        assert it["slip_log"][-1] == {
            "date": "2026-07-17", "from_window": "2026-Q2", "to_window": "2026-Q4",
            "reason": None, "source": _src("slip-evidence"),
        }


# ---------------------------------------------------------------------------
# Step 5 — the git commit
# ---------------------------------------------------------------------------


class TestGitCommit:
    def test_commit_lands_in_a_real_repo(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], check=True)
        issue = tmp_path / "issues" / "2026-07-17.json"
        issue.parent.mkdir()
        issue.write_text("{}\n")

        assert git_commit_run(tmp_path, RUN_ID, [issue], message=f"run {RUN_ID}: publish") is True
        log = subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"],
                             capture_output=True, text=True).stdout
        assert RUN_ID in log

    def test_git_failure_does_not_raise(self):
        """git failure is a warning, not a run failure — the issue is on disk."""
        from types import SimpleNamespace

        def failing(cmd, **kw):
            return SimpleNamespace(returncode=1, stdout="", stderr="fatal: not a repo")

        # A path that exists so we get past the nothing-to-stage guard.
        import pathlib
        here = pathlib.Path(__file__)
        assert git_commit_run(here.parent.parent, RUN_ID, [here], message="m", runner=failing) is False

    def test_injected_runner_that_raises_is_swallowed(self):
        import pathlib
        here = pathlib.Path(__file__)

        def exploding(cmd, **kw):
            raise OSError("git not found")

        assert git_commit_run(here.parent.parent, RUN_ID, [here], message="m", runner=exploding) is False


# ---------------------------------------------------------------------------
# The recipe, end to end (offline, real tmp git repo)
# ---------------------------------------------------------------------------


class TestRunPublishStage:
    def test_publishes_derives_commits(self, tmp_path):
        import subprocess
        root = _fake_repo(tmp_path)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
        state = load_state(root / "state")

        draft = _issue(
            watchlist=[{"entity_id": "merck", "sources": [_src("m")]}],
            quiet_this_cycle={"no_news": [{"entity_id": "pfizer"}], "critic_catches": [], "open_threads": []},
        )
        result = run_publish_stage(root, draft=draft, state=state, run_id=RUN_ID, now=NOW)

        assert result.status == "published_uncritiqued"
        assert result.committed is True
        issue = json.loads(result.issue_path.read_text())
        assert issue["issue"]["run"]["status"] == "published_uncritiqued"
        assert issue["stats"]["tracked_updates"] == 1
        assert issue["stats"]["previous_issue"] is None
        # The manifest was regenerated and includes the just-written issue.
        manifest = json.loads((root / "issues" / "index.json").read_text())
        assert manifest["issues"][0]["id"] == "2026-07-17"
