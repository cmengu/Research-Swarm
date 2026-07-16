"""Stage 2 fan-out: six researchers in parallel, failure isolated per beat.

The interesting half is the failure behaviour: a beat that fails validation
twice lands in beats_failed and the run CONTINUES — a declared degradation,
not a halt. Only all-beats-dead escalates to a stub, and that decision belongs
to run.py, not this module.
"""

import json
import re
import threading
from types import SimpleNamespace

import pytest

from researchswarm.beats import Beat, load_beats
from researchswarm.prompts import RunContext, load_template
from researchswarm.research import render_all_prompts, run_research_stage
from researchswarm.state import load_state

RUN_ID = "run_20260716_0700"
WINDOW = {"from": "2026-07-13", "to": "2026-07-16"}

BEAT_IN_PROMPT = re.compile(r'"beat": "(\w+)"')


@pytest.fixture
def ctx():
    return RunContext(
        run_id=RUN_ID,
        coverage_window_from=WINDOW["from"],
        coverage_window_to=WINDOW["to"],
    )


@pytest.fixture
def beats(repo_root):
    return load_beats(repo_root / "config" / "beats.toml")


@pytest.fixture
def template(repo_root):
    return load_template(repo_root / "prompts" / "researcher.md")


@pytest.fixture
def state(repo_root):
    return load_state(repo_root / "state")


def valid_findings(beat_id: str, findings: list | None = None) -> dict:
    findings = findings or []
    return {
        "beat": beat_id,
        "run_id": RUN_ID,
        "coverage_window": WINDOW,
        "quiet": not findings,
        "findings": findings,
        "coverage_notes": {
            "angles_run": ["seed angle"],
            "entities_checked": ["merck"],
            "notes": "test coverage",
        },
        "errors": [],
    }


def envelope(payload: dict) -> str:
    return json.dumps(
        {"result": json.dumps(payload), "is_error": False, "total_cost_usd": 0.01, "num_turns": 3}
    )


def beat_of(command: list[str]) -> str:
    """Recover which beat a researcher invocation is for, from its own prompt.

    The prompt embeds `"beat": "<id>"` in the output contract, so the argv is
    self-identifying — no side channel needed.
    """
    match = BEAT_IN_PROMPT.search(command[2])
    assert match, "prompt does not name its beat"
    return match.group(1)


def make_runner(respond, calls=None):
    """A fake subprocess.run. `respond(beat_id)` returns the findings payload
    to emit, or raises to simulate a broken researcher."""

    def runner(command, capture_output, text, timeout):
        beat_id = beat_of(command)
        if calls is not None:
            calls.append(beat_id)
        payload = respond(beat_id)
        return SimpleNamespace(returncode=0, stdout=envelope(payload), stderr="")

    return runner


class TestFanOut:
    def test_all_six_beats_run_and_persist(self, beats, template, ctx, state, tmp_path):
        runner = make_runner(valid_findings)
        stage = run_research_stage(
            beats, template, ctx, state, tmp_path, runner=runner
        )

        assert stage.beats_run == [b.id for b in beats]
        assert stage.beats_failed == []
        assert not stage.all_failed
        for beat in beats:
            path = tmp_path / "runs" / RUN_ID / "findings" / f"{beat.id}.json"
            assert path.exists(), f"{beat.id} findings not persisted"
            assert json.loads(path.read_text())["beat"] == beat.id

    def test_beats_run_concurrently_not_sequentially(self, beats, template, ctx, state, tmp_path):
        """Every researcher must be in flight at once. Each fake blocks on a
        barrier sized to the roster: run sequentially, the first call would
        wait forever for peers that never start."""
        barrier = threading.Barrier(len(beats), timeout=15)

        def respond(beat_id):
            barrier.wait()
            return valid_findings(beat_id)

        stage = run_research_stage(
            beats, template, ctx, state, tmp_path, runner=make_runner(respond)
        )
        assert stage.beats_run == [b.id for b in beats]

    def test_one_dead_beat_does_not_kill_the_run(self, beats, template, ctx, state, tmp_path):
        """The failure lands in beats_failed — destined for
        sources_and_method.beats_failed — and the other five persist."""

        def respond(beat_id):
            if beat_id == "policy_regulation":
                return {"beat": beat_id, "garbage": True}  # fails validation, twice
            return valid_findings(beat_id)

        calls = []
        stage = run_research_stage(
            beats, template, ctx, state, tmp_path, runner=make_runner(respond, calls)
        )

        assert stage.beats_failed == ["policy_regulation"]
        assert stage.beats_run == [b.id for b in beats if b.id != "policy_regulation"]
        assert not stage.all_failed
        # The dead beat got its one retry — two attempts, no more.
        assert calls.count("policy_regulation") == 2
        assert not (
            tmp_path / "runs" / RUN_ID / "findings" / "policy_regulation.json"
        ).exists()

    def test_all_beats_failing_is_reported_not_raised(self, beats, template, ctx, state, tmp_path):
        """The module reports; the stub decision is run.py's."""
        stage = run_research_stage(
            beats, template, ctx, state, tmp_path,
            runner=make_runner(lambda beat_id: {"nonsense": True}),
        )
        assert stage.beats_run == []
        assert stage.beats_failed == [b.id for b in beats]
        assert stage.all_failed

    def test_duplicate_findings_across_beats_are_preserved(
        self, beats, template, ctx, state, tmp_path
    ):
        """Beats overlap by design. A duplicate costs the manager one merge; a
        dropped story costs a missed repricing — so nothing dedups here."""
        shared = {
            "summary": "Merck acquires Verastem for $9B. Confirmed by press release.",
            "entity_ids": ["merck"],
            "proposed_entity": None,
            "sources": [
                {
                    "url": "https://example.com/deal",
                    "publisher": "Endpoints News",
                    "tier": "trade",
                    "published_at": "2026-07-15",
                    "paywalled": False,
                }
            ],
            "catalyst_refs": [],
            "beat_priority": "high",
            "unconfirmed": False,
        }
        stage = run_research_stage(
            beats, template, ctx, state, tmp_path,
            runner=make_runner(lambda beat_id: valid_findings(beat_id, [dict(shared)])),
        )
        assert stage.beats_run == [b.id for b in beats]
        summaries = [
            json.loads(
                (tmp_path / "runs" / RUN_ID / "findings" / f"{b.id}.json").read_text()
            )["findings"][0]["summary"]
            for b in beats
        ]
        assert summaries == [shared["summary"]] * len(beats)


class TestConfigIsTheRoster:
    def test_a_seventh_beat_is_a_toml_block_not_a_code_change(
        self, repo_root, template, ctx, state, tmp_path
    ):
        beats_toml = (repo_root / "config" / "beats.toml").read_text()
        beats_toml += (
            "\n[[beat]]\n"
            'id = "seventh"\n'
            'name = "Seventh beat"\n'
            'charter = "Prove the roster is config."\n'
            'seed_angles = ["one angle"]\n'
        )
        config = tmp_path / "beats.toml"
        config.write_text(beats_toml)

        beats = load_beats(config)
        assert len(beats) == 7

        stage = run_research_stage(
            beats, template, ctx, state, tmp_path, runner=make_runner(valid_findings)
        )
        assert "seventh" in stage.beats_run
        assert (tmp_path / "runs" / RUN_ID / "findings" / "seventh.json").exists()

    def test_charter_and_max_turns_come_from_config(self, beats, template, ctx, state):
        """Same template, per-beat values interpolated: the rules are shared,
        the scope is config."""
        prompts = render_all_prompts(beats, template, ctx, state)
        for beat in beats:
            assert beat.charter.splitlines()[0] in prompts[beat.id]
            assert f"hard cap of {beat.max_turns} tool turns" in prompts[beat.id]

    def test_rules_are_identical_across_beats(self, beats, template, ctx, state):
        """Sourcing discipline lives once in the template — every beat sees the
        exact same rules text."""
        prompts = render_all_prompts(beats, template, ctx, state)

        def rules_section(prompt: str) -> str:
            start = prompt.index("# Sourcing rules")
            end = prompt.index("# Budget")
            return prompt[start:end]

        sections = {rules_section(p) for p in prompts.values()}
        assert len(sections) == 1
