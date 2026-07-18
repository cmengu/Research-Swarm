"""The v2 critic: the rubric, the renderer, the kind vocabulary, and the stage.

Three things are under test here, and the third is the reason the ticket exists:

1. `render_critic_prompt_v2` against the REAL pilot config and state — so a drift
   between the authored program/edges/thesis and the rubric fails here.
2. `run_critic_v2`'s kind vocabulary — `relation_miscast` may block,
   `weak_read_through` may NOT, and an invented kind is demoted rather than
   allowed to gate.
3. `run_critique_stage_v2` end to end with an injected fake codex runner AND an
   injected fake manager runner — the verdict → run.status map, the receipt rule,
   the retry loop.

Everything is deterministic and offline: no real model is called, the subprocess
runner is always injected, and the offline guard is asserted to raise rather than
reach the real binary. A LIVE Codex pass is the parent's job, not this file's.

Spec: docs/spec/06-validator-and-critic.md (stage 2), docs/spec/07-issue-schema.md
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchswarm.critic import (
    ADVISORY_KINDS_V2,
    BLOCKING_KINDS_V2,
    CriticOfflineViolation,
    run_critic_v2,
)
from researchswarm.critique import (
    PUBLISHED,
    PUBLISHED_UNCRITIQUED,
    PUBLISHED_WITH_UNRESOLVED,
    run_critique_stage_v2,
)
from researchswarm.programs import load_edges, load_entities, load_program
from researchswarm.prompts import (
    UnresolvedPlaceholder,
    load_template,
    render_critic_prompt_v2,
)

RUN_ID = "run_20260718_0700"
MODEL = "gpt-5-codex"
MANAGER_MODEL = "claude-opus-4-8"
THESIS_VERSION = 3

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "findings-v2"


@pytest.fixture(autouse=True)
def _offline_off(monkeypatch):
    monkeypatch.delenv("RESEARCHSWARM_OFFLINE", raising=False)


@pytest.fixture
def template(repo_root):
    return load_template(repo_root / "prompts" / "critic-v2.md")


@pytest.fixture
def retry_template(repo_root):
    return load_template(repo_root / "prompts" / "critic-retry.md")


@pytest.fixture
def program(repo_root):
    return load_program(repo_root / "config", "hmbd-001")


@pytest.fixture
def edges(repo_root):
    return load_edges(repo_root / "state", "hmbd-001")


@pytest.fixture
def entities(repo_root):
    return load_entities(repo_root / "state")


@pytest.fixture
def thesis(repo_root):
    return json.loads((repo_root / "state" / "thesis.json").read_text())


@pytest.fixture
def findings():
    """The hand-built v2 aperture corpus, keyed by aperture id — the same fixture
    the v2 manager prompt tests read, so the manager and the critic are judged
    against one corpus rather than two that can drift."""
    return {
        "biology_scan": json.loads((FIXTURES / "biology_scan.json").read_text()),
        "arena_scan:squamous-nsclc": json.loads(
            (FIXTURES / "arena_scan-squamous-nsclc.json").read_text()
        ),
        "house_sweep": json.loads((FIXTURES / "house_sweep.json").read_text()),
    }


def _source(url="https://x.example/a"):
    return {"url": url, "publisher": "Endpoints", "tier": "trade",
            "published_at": "2026-07-16"}


def _draft(**overrides):
    """A v2 draft: valid as the critic's input AND as what the retry manager
    re-emits (it must pass validate_issue_draft's v2 seam contract)."""
    draft = {
        "schema_version": "2.0.0",
        "issue": {
            "id": "2026-07-18",
            "program_id": "hmbd-001",
            "coverage_window": {"from": "2026-07-14", "to": "2026-07-18"},
            "run": {"run_id": RUN_ID, "thesis_version": THESIS_VERSION},
        },
        "program": {"id": "hmbd-001", "name": "HMBD-001",
                    "moa": "signalling_blockade", "target": "HER3 (ERBB3)"},
        "headline": {"title": "t", "so_what": "matters"},
        "stats": {},
        "tldr_bullets": [],
        "catalyst_queue": {},
        "competitors": [],
        "indications": [],
        "quiet_this_cycle": {"no_news": [], "critic_catches": [], "open_threads": []},
        "newly_discovered": [],
        "house_view": {"partnership_bd": [], "threat_financing": [],
                       "themes_and_signals": [], "blind_spots": {"cap": 5, "ranked": []}},
        "thesis_updates": [],
        "critic_report": {
            "validator_report": {"passed": True, "retries_used": 0, "findings": []}
        },
        "sources_and_method": {"apertures_run": [], "apertures_degraded": [],
                               "source_tier_counts": {}, "paywalled_flagged": []},
    }
    draft.update(overrides)
    return draft


def _codex_runner(*payloads):
    """Fake codex: writes each queued verdict payload to the -o file in order; a
    single payload repeats (the 'critic never changes its mind' case)."""
    queue = list(payloads)

    def runner(command, **kwargs):
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        out = command[command.index("-o") + 1]
        with open(out, "w") as fh:
            fh.write(payload if isinstance(payload, str) else json.dumps(payload))
        return SimpleNamespace(
            returncode=0, stdout='{"type":"turn.completed","usage":{}}', stderr=""
        )

    return runner


def _manager_runner(*drafts, calls=None):
    """Fake claude-family manager: returns each queued draft in an envelope; a
    single draft repeats (the manager that keeps handing back a blocked draft)."""
    queue = list(drafts)

    def runner(command, **kwargs):
        if calls is not None:
            calls.append(command)
        draft = queue.pop(0) if len(queue) > 1 else queue[0]
        envelope = json.dumps({"is_error": False, "result": json.dumps(draft),
                               "total_cost_usd": 0.1, "num_turns": 2})
        return SimpleNamespace(returncode=0, stdout=envelope, stderr="")

    return runner


def _no_manager(*a, **k):
    raise AssertionError("the manager must not be called")


def _verdict(verdict="pass", blocking=(), advisory=()):
    return {"verdict": verdict, "blocking_findings": list(blocking),
            "advisory_findings": list(advisory)}


def _weak(where="competitors.asset_her3_dxd"):
    return {"kind": "weak_read_through", "where": where,
            "note": "restates the ORR; never says what it means for HMBD-001"}


def _stage(root, ctx, runner, *, manager_runner=_no_manager, draft=None):
    program, edges, entities, thesis, template, retry_template, findings = ctx
    return run_critique_stage_v2(
        root,
        draft=draft if draft is not None else _draft(),
        program=program,
        edges=edges,
        entities=entities,
        thesis=thesis,
        findings_by_aperture=findings,
        run_id=RUN_ID,
        issues_dir=root / "issues" / "hmbd-001",
        critic_template=template,
        retry_template=retry_template,
        model=MODEL,
        manager_model=MANAGER_MODEL,
        draft_path=root / "runs" / RUN_ID / "issue-draft.json",
        thesis_version=THESIS_VERSION,
        runner=runner,
        manager_runner=manager_runner,
    )


@pytest.fixture
def ctx(program, edges, entities, thesis, template, retry_template, findings):
    return program, edges, entities, thesis, template, retry_template, findings


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "runs" / RUN_ID).mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# The rubric's vocabulary — blocking vs advisory must match spec/06 EXACTLY.
# ---------------------------------------------------------------------------


class TestTheKindVocabulary:
    def test_the_seven_blocking_kinds_are_the_specs(self):
        """spec/06 blocking findings: v1's six plus relation_miscast, no more."""
        assert BLOCKING_KINDS_V2 == {
            "provenance_stale", "overclaim", "aggregator_only", "unconfirmed_as_fact",
            "dropped_story", "thesis_impact_false", "relation_miscast",
        }

    def test_weak_read_through_is_advisory_and_never_blocking(self):
        """The centrepiece, and the one classification this ticket must not get
        wrong: the read-through's PRESENCE is a validator block, its QUALITY is
        this advisory (spec/06 the admission rule, spec/07)."""
        assert "weak_read_through" in ADVISORY_KINDS_V2
        assert "weak_read_through" not in BLOCKING_KINDS_V2

    def test_weak_angle_retired_in_favour_of_weak_read_through(self):
        assert "weak_angle" not in ADVISORY_KINDS_V2

    def test_the_advisory_table_matches_the_spec(self):
        assert ADVISORY_KINDS_V2 == {
            "thin_sourcing", "coverage_gap", "weak_read_through", "thesis_unseeded",
            "paywalled_primary", "unverifiable_claim", "stale_open_thread",
            "source_unreachable", "calendar_stale", "thread_dropped",
            "continuity_break", "continuity_baseline_expired",
        }


class TestSortingOneCriticPass:
    def test_relation_miscast_is_allowed_to_block(self):
        """New in v2 — and it must NOT be demoted by the v1 blocking set."""
        payload = _verdict("blocked", blocking=[
            {"kind": "relation_miscast", "where": "competitors.asset_her3_dxd",
             "note": "an ADC typed mechanism_twin"}
        ])
        result = run_critic_v2("P", model=MODEL, runner=_codex_runner(payload))
        assert result.verdict == "blocked"
        assert [f["kind"] for f in result.blocking_findings] == ["relation_miscast"]

    def test_a_weak_read_through_filed_as_blocking_is_demoted(self):
        """The failure this rubric invites: the critic feels strongly about an
        empty read-through and files it as blocking. It is advisory by spec, so it
        is demoted with an audit crumb — it can never gate the line."""
        result = run_critic_v2(
            "P", model=MODEL,
            runner=_codex_runner(_verdict("blocked", blocking=[_weak()])),
        )
        assert result.blocking_findings == ()
        assert result.advisory_findings[0]["kind"] == "weak_read_through"
        assert "unknown kind" in result.advisory_findings[0]["note"]

    def test_advisories_ride_through_untouched(self):
        result = run_critic_v2(
            "P", model=MODEL,
            runner=_codex_runner(_verdict("pass_with_advisories", advisory=[_weak()])),
        )
        assert result.verdict == "pass_with_advisories"
        assert result.advisory_findings == (_weak(),)

    def test_unparseable_output_is_not_run_never_a_pass(self):
        result = run_critic_v2("P", model=MODEL, runner=_codex_runner("not json"))
        assert result.verdict == "not_run"
        assert "unparseable" in result.reason

    def test_an_invalid_verdict_is_not_run(self):
        result = run_critic_v2(
            "P", model=MODEL, runner=_codex_runner({"verdict": "looks_fine"})
        )
        assert result.verdict == "not_run"

    def test_the_v2_schema_file_is_passed_to_codex(self, repo_root, tmp_path):
        seen = {}

        def runner(command, **kwargs):
            seen["command"] = command
            out = command[command.index("-o") + 1]
            Path(out).write_text(json.dumps(_verdict()))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        schema = repo_root / "prompts" / "critic-output-schema-v2.json"
        run_critic_v2("P", model=MODEL, schema_file=schema, runner=runner)
        assert "--output-schema" in seen["command"]
        assert str(schema) in seen["command"]
        assert "--sandbox" in seen["command"]  # the no-web wall

    def test_the_offline_guard_raises_before_spending_quota(self, monkeypatch):
        monkeypatch.setenv("RESEARCHSWARM_OFFLINE", "1")
        with pytest.raises(CriticOfflineViolation):
            run_critic_v2("P", model=MODEL, runner=subprocess.run)


class TestTheOutputSchemaV2:
    def test_it_enumerates_every_v2_kind_and_no_others(self, repo_root):
        schema = json.loads(
            (repo_root / "prompts" / "critic-output-schema-v2.json").read_text()
        )
        kinds = set(schema["$defs"]["finding"]["properties"]["kind"]["enum"])
        assert kinds == BLOCKING_KINDS_V2 | ADVISORY_KINDS_V2

    def test_not_run_is_never_emittable(self, repo_root):
        schema = json.loads(
            (repo_root / "prompts" / "critic-output-schema-v2.json").read_text()
        )
        assert schema["properties"]["verdict"]["enum"] == [
            "pass", "pass_with_advisories", "blocked"
        ]


# ---------------------------------------------------------------------------
# The rubric itself — the template is the contract with the model.
# ---------------------------------------------------------------------------


class TestTheRubricText:
    def test_it_states_the_presence_quality_split_unmistakably(self, template):
        """The one thing the prompt must make impossible to misread (spec/06 the
        admission rule): the validator blocks a MISSING read-through; the critic
        judges whether a PRESENT one argues."""
        assert "weak_read_through" in template
        assert "It is NOT a presence check" in template
        assert "the validator blocked on it already" in template

    def test_it_forbids_re_checking_the_deterministic_gate(self, template):
        for mechanical in (
            "missing_read_through", "untyped_competitor", "blind_spot_overflow",
            "landscape_number_unsourced", "derived_stats_mismatch", "queue_tamper",
            "malformed_source", "unaccounted_competitor", "empty_section",
        ):
            assert mechanical in template, mechanical
        assert "you are doing the validator's job" in template

    def test_every_blocking_and_advisory_kind_appears(self, template):
        for kind in BLOCKING_KINDS_V2 | ADVISORY_KINDS_V2:
            assert kind in template, kind

    def test_it_carries_the_receipt_rule_verbatim_in_all_five_clauses(self, template):
        assert "url, publisher, tier, published_at" in template
        assert "APPEARS in the raw findings corpus" in template
        assert "primary or trade" in template
        assert "INSIDE issue.coverage_window" in template
        assert "cited NOWHERE in the issue" in template

    def test_it_keeps_the_rebuttal_channel_and_the_no_web_wall(self, template):
        assert "rebuttal" in template
        assert "do NOT have web access" in template

    def test_it_never_emits_not_run_itself(self, template):
        assert "the orchestrator owns not_run; never" in template


# ---------------------------------------------------------------------------
# The renderer — real config, real state, no leftovers.
# ---------------------------------------------------------------------------


class TestRenderCriticPromptV2:
    def _render(self, ctx, **overrides):
        program, edges, entities, thesis, template, _retry, findings = ctx
        kwargs = dict(
            issue=_draft(),
            findings_by_aperture=findings,
            previous_issue=None,
            program=program,
            edges=edges,
            entities=entities,
            thesis=thesis,
        )
        kwargs.update(overrides)
        return render_critic_prompt_v2(template, **kwargs)

    def test_no_placeholder_survives(self, ctx):
        assert "{{" not in self._render(ctx)

    def test_a_template_with_an_unknown_placeholder_raises(self, ctx):
        program, edges, entities, thesis, _t, _r, findings = ctx
        with pytest.raises(UnresolvedPlaceholder):
            render_critic_prompt_v2(
                "judge this: {{nothing_renders_this}}",
                issue=_draft(), findings_by_aperture=findings, previous_issue=None,
                program=program, edges=edges, entities=entities, thesis=thesis,
            )

    def test_the_program_and_its_moa_are_in_front_of_the_critic(self, ctx):
        """relation_miscast and weak_read_through both turn on the program's
        target and moa — a critic that does not know them cannot judge either."""
        rendered = self._render(ctx)
        assert "hmbd-001" in rendered
        assert "signalling_blockade" in rendered

    def test_the_findings_corpus_is_keyed_by_aperture_not_beat(self, ctx):
        rendered = self._render(ctx)
        assert "findings from aperture: biology_scan" in rendered
        assert "findings from aperture: arena_scan:squamous-nsclc" in rendered
        assert "findings from beat" not in rendered

    def test_run_one_says_so_rather_than_inventing_a_previous_issue(self, ctx):
        assert "(no previous issue)" in self._render(ctx, previous_issue=None)

    def test_a_previous_issue_is_embedded_whole(self, ctx):
        rendered = self._render(ctx, previous_issue={"issue": {"id": "2026-07-11"}})
        assert "2026-07-11" in rendered

    def test_the_thesis_arrives_interpolated_fresh(self, ctx):
        """The propagation contract: stance text is never baked into the file, so
        an owner edit reaches the critic on the next run (spec/03)."""
        _p, _e, _en, thesis, _t, _r, _f = ctx
        rendered = self._render(ctx)
        for belief in thesis.get("beliefs", []):
            assert belief["id"] in rendered

    def test_the_surge_window_is_the_provenance_stale_reference(self, ctx):
        from researchswarm.calendar import SurgeState

        surge = SurgeState(window="ESMO 2026", window_id="esmo-2026", day=2, of=5,
                           starts="2026-09-18", ends="2026-09-22")
        rendered = self._render(ctx, surge=surge)
        assert "2026-09-18" in rendered and "2026-09-22" in rendered
        assert "no surge this cycle" in self._render(ctx)


# ---------------------------------------------------------------------------
# The stage — verdict → run.status, the receipt rule, the retry loop.
# ---------------------------------------------------------------------------


class TestTheStage:
    def test_pass_publishes(self, run_dir, ctx):
        result = _stage(run_dir, ctx, _codex_runner(_verdict("pass")))
        assert result.status == PUBLISHED
        assert result.verdict == "pass"
        assert result.retries_used == 0

    def test_advisories_publish_and_never_retry(self, run_dir, ctx):
        """The whole point of weak_read_through being advisory: an issue full of
        weak read-throughs still publishes, in one pass, with them printed."""
        result = _stage(
            run_dir, ctx,
            _codex_runner(_verdict("pass_with_advisories", advisory=[_weak(), _weak("house_view.partnership_bd.merck")])),
        )
        assert result.status == PUBLISHED
        assert result.retries_used == 0
        assert len(result.advisory_findings) == 2

    def test_a_broken_critic_publishes_uncritiqued_not_failed(self, run_dir, ctx):
        result = _stage(run_dir, ctx, _codex_runner("not json"))
        assert result.status == PUBLISHED_UNCRITIQUED
        assert result.verdict == "not_run"
        assert "unparseable" in result.reason

    def test_a_dropped_story_without_a_receipt_evaporates(self, run_dir, ctx):
        blocking = [{"kind": "dropped_story", "where": "competitors.x", "note": "cut",
                     "source": _source("https://not-in-the-corpus.example/x")}]
        result = _stage(run_dir, ctx, _codex_runner(_verdict("blocked", blocking)))
        assert result.status == PUBLISHED
        assert result.verdict == "pass_with_advisories"
        assert result.blocking_findings == ()
        assert any("receipt downgrade" in f["note"] for f in result.advisory_findings)

    def test_a_real_block_retries_then_publishes_when_fixed(self, run_dir, ctx):
        runner = _codex_runner(
            _verdict("blocked", [{"kind": "relation_miscast",
                                  "where": "competitors.asset_her3_dxd",
                                  "note": "ADC typed mechanism_twin"}]),
            _verdict("pass"),
        )
        result = _stage(run_dir, ctx, runner, manager_runner=_manager_runner(_draft()))
        assert result.status == PUBLISHED
        assert result.retries_used == 1

    def test_an_unmoved_critic_exhausts_into_a_visible_dispute(self, run_dir, ctx):
        blocking = [{"kind": "overclaim", "where": "headline", "note": "too strong"}]
        result = _stage(
            run_dir, ctx, _codex_runner(_verdict("blocked", blocking)),
            manager_runner=_manager_runner(_draft()),
        )
        assert result.status == PUBLISHED_WITH_UNRESOLVED
        assert result.retries_used == 2
        assert [f["kind"] for f in result.blocking_findings] == ["overclaim"]

    def test_the_draft_the_loop_produces_is_the_one_returned(self, run_dir, ctx):
        edited = _draft(headline={"title": "softened", "so_what": "matters"})
        runner = _codex_runner(
            _verdict("blocked", [{"kind": "overclaim", "where": "headline", "note": "n"}]),
            _verdict("pass"),
        )
        result = _stage(run_dir, ctx, runner, manager_runner=_manager_runner(edited))
        assert result.draft["headline"]["title"] == "softened"
        on_disk = json.loads((run_dir / "runs" / RUN_ID / "issue-draft.json").read_text())
        assert on_disk["headline"]["title"] == "softened"
