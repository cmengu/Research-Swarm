"""Stage 2 — the fan-out to six beats.

All beats run at once, and failure is isolated per beat: a researcher that
fails validation twice lands in beats_failed and the run CONTINUES without it.
That is a declared degradation (`beat_failed` in the register), not a halt —
one dead researcher must not kill the Monday issue. Deciding that ALL beats
dead means a stub is escalation, and escalation belongs to run.py.

Duplicates across beats are preserved, never deduplicated here: beats overlap
by design, a duplicate costs the manager one merge, and the overlap is what
makes this corpus useful as the critic's receipt pool.

Threads, not processes: a researcher is a subprocess blocked on the network
for minutes, so the GIL is irrelevant and one thread per beat is the whole
concurrency story.

Spec: docs/spec/04-researchers.md, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from researchswarm.beats import Beat
from researchswarm.prompts import RunContext, render_researcher_prompt
from researchswarm.researcher import ResearcherFailed, run_researcher
from researchswarm.state import State

log = logging.getLogger("researchswarm.research")


@dataclass(frozen=True)
class ResearchStage:
    """What stage 2 hands to stage 3.

    beats_failed is destined for sources_and_method.beats_failed in the
    published issue (build 04); until the manager exists it also feeds the
    all-dead stub. Both lists keep roster order so logs and artifacts are
    deterministic regardless of which researcher finished first.
    """

    beats_run: list[str]
    beats_failed: list[str]

    @property
    def all_failed(self) -> bool:
        return bool(self.beats_failed) and not self.beats_run


def render_all_prompts(
    beats: list[Beat], template: str, ctx: RunContext, state: State
) -> dict[str, str]:
    """Render every beat's prompt up front, in the caller's thread.

    A render failure is a config or template problem — it would hit all six
    beats identically, so letting it masquerade as six dead researchers would
    bury a loud config error inside the degradation machinery. Fail here,
    before anything is spawned.
    """
    return {beat.id: render_researcher_prompt(template, beat, ctx, state) for beat in beats}


def persist_findings(root: Path, run_id: str, beat_id: str, findings: dict) -> Path:
    """The orchestrator is the SOLE writer of the findings corpus.

    The researcher physically cannot do this — it has no write tool — which is
    the point: persistence can't be forgotten by an agent, and the corpus is
    evidence rather than scratch. The critic reads it to catch what the manager
    found and then dropped.
    """
    findings_dir = root / "runs" / run_id / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    path = findings_dir / f"{beat_id}.json"
    path.write_text(json.dumps(findings, indent=2) + "\n")
    return path


def run_research_stage(
    beats: list[Beat],
    template: str,
    ctx: RunContext,
    state: State,
    root: Path,
    *,
    runner=subprocess.run,
) -> ResearchStage:
    """Fan out one researcher per beat, persist each result as it lands.

    Persistence happens here, on completion order, so a beat that finishes in
    minute two is on disk in minute two — a crash in minute forty loses only
    the beats still in flight, and the operator watching the log sees progress
    rather than silence.
    """
    prompts = render_all_prompts(beats, template, ctx, state)
    window = {"from": ctx.coverage_window_from, "to": ctx.coverage_window_to}

    succeeded: set[str] = set()
    failed: set[str] = set()

    with ThreadPoolExecutor(max_workers=len(beats)) as pool:
        futures = {
            pool.submit(
                run_researcher,
                beat,
                prompts[beat.id],
                run_id=ctx.run_id,
                window=window,
                known_entity_ids=state.entity_ids,
                runner=runner,
            ): beat
            for beat in beats
        }

        for future in as_completed(futures):
            beat = futures[future]
            try:
                result = future.result()
            except ResearcherFailed as exc:
                # Dead beat, live run: the manager will mark the sections it
                # fed with an inline marker so a thin section reads as an
                # absence, not as a quiet week.
                log.warning("%s: BEAT FAILED — %s", beat.id, exc)
                failed.add(beat.id)
                continue

            path = persist_findings(root, ctx.run_id, beat.id, result.findings)
            succeeded.add(beat.id)
            log.info(
                "%s: %d finding(s), quiet=%s, %d turn(s), $%.4f, attempt %d → %s",
                beat.id,
                len(result.findings.get("findings", [])),
                result.findings.get("quiet"),
                result.num_turns,
                result.cost_usd,
                result.attempts,
                path.relative_to(root),
            )

    return ResearchStage(
        beats_run=[b.id for b in beats if b.id in succeeded],
        beats_failed=[b.id for b in beats if b.id in failed],
    )
