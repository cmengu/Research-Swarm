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

from researchswarm.apertures import Aperture
from researchswarm.beats import Beat
from researchswarm.programs import Edge, InterestList, Program
from researchswarm.prompts import (
    RunContext,
    render_researcher_prompt,
    render_researcher_prompt_v2,
)
from researchswarm.researcher import ResearcherFailed, run_researcher, run_researcher_v2
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


def load_findings(root: Path, run_id: str, beat_ids) -> dict[str, dict]:
    """Read the persisted findings corpus back — the read half of persist_findings.

    One loader for the two downstream stages that both need the raw corpus:
    synthesis hands it to the manager, critique hands it to the critic. run.py is
    the sole reader on disk exactly as it is the sole writer, so this lives beside
    persist_findings rather than being re-inlined in each stage. `beat_ids` is the
    surviving beats (stage.beats_run) — a failed beat wrote no file.
    """
    findings_dir = root / "runs" / run_id / "findings"
    return {
        beat_id: json.loads((findings_dir / f"{beat_id}.json").read_text())
        for beat_id in beat_ids
    }


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
    window = ctx.window

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


# ---------------------------------------------------------------------------
# The v2 fan-out — apertures, not beats.
#
# Additive alongside the v1 stage above; run.py's v2 orchestration chooses this
# one, so the two never branch inside a single function. Three shape differences
# flow from the pivot (spec/04):
#
#   - the unit is an APERTURE (`1 + N + 1`, derived from the program) not a beat,
#     and only ACTIVE apertures spend model budget;
#   - a DORMANT arena scan is a first-class outcome, not an error — it never ran
#     by design, and it must still reach the manager so the dormancy renders;
#   - the corpus is returned IN MEMORY as well as persisted, because the v2
#     synthesis stage takes `findings_by_aperture` rather than reading disk.
# ---------------------------------------------------------------------------

# The statuses an `apertures_run` entry can carry (docs/spec/07 sources_and_method).
# "ok" ran and validated; "dormant" was never run (a priority_indication's arena
# scan is event-triggered); "failed" ran and died at the seam twice. Dormant and
# failed are different FACTS with the same consequence — both also land in
# `apertures_degraded`, which is what the validator's mechanical-degradation
# exemption reads.
STATUS_OK = "ok"
STATUS_DORMANT = "dormant"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ResearchStageV2:
    """What the v2 stage 2 hands to the v2 stage 3.

    Two audit-trail fields whose SHAPES are load-bearing, because the validator
    and the manager read them (docs/spec/07 `sources_and_method`):

    - `apertures_run` — a list of `{"aperture", "scope", "status"}` OBJECTS, one
      per planned aperture including the dormant ones. `aperture` is the KIND
      (`arena_scan`), not the id, with the indication in `scope`; that split is
      what `validator._arena_mechanically_degraded` matches on.
    - `apertures_degraded` — a flat list of aperture-ID STRINGS
      (`"arena_scan:nrg1-fusion-solid-tumors"`), the same exemption's other key.

    Both keep roster order, so the artifact reads the same way every cycle
    regardless of which researcher finished first. `findings_by_aperture` is keyed
    by aperture id (the id, not the on-disk slug — disk naming is this module's
    private concern, see `aperture_slug`).
    """

    apertures_run: list[dict]
    apertures_degraded: list[str]
    findings_by_aperture: dict[str, dict]

    @property
    def all_failed(self) -> bool:
        """No aperture produced findings. As in v1, the stub decision is run.py's —
        this only reports. Note a program whose every arena is dormant can reach
        this state without a single failure, which is still a stub-worthy run."""
        return not self.findings_by_aperture


def aperture_slug(aperture_id: str) -> str:
    """The on-disk name for an aperture id: `arena_scan:sq-nsclc` → `arena_scan-sq-nsclc`.

    Aperture ids carry a colon, which is a path separator on some filesystems and
    an outright illegal filename character on others — so the id cannot be the
    filename. One documented helper, used by BOTH the v2 persist and the v2 load,
    so the two can never disagree about where a findings file lives. The mapping
    is one-way by design: the id stays the key everywhere in memory and in the
    published artifact, and the slug exists only between `open()` calls.
    """
    return aperture_id.replace(":", "-")


def persist_findings_v2(root: Path, run_id: str, aperture_id: str, findings: dict) -> Path:
    """Write one aperture's findings. The orchestrator remains the SOLE writer.

    Same contract as `persist_findings` — the researcher has no write tool, so
    persistence cannot be forgotten by an agent and the corpus is evidence, not
    scratch. The only difference is the slugified filename.
    """
    findings_dir = root / "runs" / run_id / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    path = findings_dir / f"{aperture_slug(aperture_id)}.json"
    path.write_text(json.dumps(findings, indent=2) + "\n")
    return path


def load_findings_v2(root: Path, run_id: str, aperture_ids) -> dict[str, dict]:
    """Read the persisted v2 corpus back, keyed by aperture ID (not by slug).

    The read half of `persist_findings_v2`. The v2 synthesis stage takes the corpus
    the research stage already holds in memory, so this exists for the paths that
    do NOT have it: the critic stage, and any re-run against an archived run
    directory. `aperture_ids` is the apertures that produced findings — a dormant
    or failed one wrote no file.
    """
    findings_dir = root / "runs" / run_id / "findings"
    return {
        aperture_id: json.loads(
            (findings_dir / f"{aperture_slug(aperture_id)}.json").read_text()
        )
        for aperture_id in aperture_ids
    }


def render_all_prompts_v2(
    apertures: list[Aperture],
    template: str,
    *,
    program: Program,
    interests: InterestList,
    edges: list[Edge],
    thesis: dict,
    ctx: RunContext,
) -> dict[str, str]:
    """Render every ACTIVE aperture's prompt up front, in the caller's thread.

    Same reasoning as v1's `render_all_prompts`: a render failure is a template or
    config problem that would hit every aperture identically, and letting it
    masquerade as N dead researchers would bury a loud config error inside the
    degradation machinery. Fail here, before anything is spawned.
    """
    return {
        aperture.id: render_researcher_prompt_v2(
            template,
            aperture,
            program=program,
            interests=interests,
            edges=edges,
            thesis=thesis,
            ctx=ctx,
        )
        for aperture in apertures
    }


def run_research_stage_v2(
    apertures: list[Aperture],
    template: str,
    ctx: RunContext,
    root: Path,
    *,
    program: Program,
    interests: InterestList,
    edges: list[Edge],
    thesis: dict,
    known_entity_ids,
    model: str,
    runner=subprocess.run,
) -> ResearchStageV2:
    """Fan out one researcher per ACTIVE aperture, persist each result as it lands.

    `apertures` is the FULL planned roster (`plan_apertures`), not the active
    subset: the dormant ones spend no model budget but must still be reported, or
    the manager cannot render the dormancy and the validator's mechanical
    exemption has nothing to match. Filtering happens here so the caller cannot
    accidentally hand over a roster the dormancy has already been dropped from.

    Threads, not processes, exactly as v1: a researcher is a subprocess blocked on
    the network for minutes, so one thread per active aperture is the whole
    concurrency story. Persistence happens on completion order so an aperture that
    lands in minute two is on disk in minute two.

    Failure is isolated per aperture — a scan that fails validation twice lands in
    `apertures_degraded` with status `failed` and the run CONTINUES. As in v1, the
    all-dead-means-stub decision belongs to run.py, not here.
    """
    active = [a for a in apertures if a.active]
    prompts = render_all_prompts_v2(
        active, template, program=program, interests=interests,
        edges=edges, thesis=thesis, ctx=ctx,
    )
    window = ctx.window

    findings_by_aperture: dict[str, dict] = {}
    failed: set[str] = set()

    # max_workers must be >= 1 even when every arena is dormant and the roster is
    # somehow empty — ThreadPoolExecutor rejects 0.
    with ThreadPoolExecutor(max_workers=max(len(active), 1)) as pool:
        futures = {
            pool.submit(
                run_researcher_v2,
                aperture,
                prompts[aperture.id],
                model=model,
                program_id=program.id,
                run_id=ctx.run_id,
                window=window,
                known_entity_ids=known_entity_ids,
                runner=runner,
            ): aperture
            for aperture in active
        }

        for future in as_completed(futures):
            aperture = futures[future]
            try:
                result = future.result()
            except ResearcherFailed as exc:
                # Dead aperture, live run: the manager marks every section it fed
                # with an inline marker, so a thin section reads as an absence
                # rather than as a quiet cycle.
                log.warning("%s: APERTURE FAILED — %s", aperture.id, exc)
                failed.add(aperture.id)
                continue

            path = persist_findings_v2(root, ctx.run_id, aperture.id, result.findings)
            findings_by_aperture[aperture.id] = result.findings
            log.info(
                "%s: %d finding(s), quiet=%s, %d turn(s), $%.4f, attempt %d → %s",
                aperture.id,
                len(result.findings.get("findings", [])),
                result.findings.get("quiet"),
                result.num_turns,
                result.cost_usd,
                result.attempts,
                path.relative_to(root),
            )

    # Rebuild both audit lists in ROSTER order, from the full roster — completion
    # order is nondeterministic, and the artifact must not be.
    apertures_run = []
    apertures_degraded = []
    for aperture in apertures:
        if aperture.dormant:
            status = STATUS_DORMANT
        elif aperture.id in failed:
            status = STATUS_FAILED
        else:
            status = STATUS_OK
        apertures_run.append(
            {"aperture": aperture.kind, "scope": aperture.scope, "status": status}
        )
        if status != STATUS_OK:
            apertures_degraded.append(aperture.id)

    return ResearchStageV2(
        apertures_run=apertures_run,
        apertures_degraded=apertures_degraded,
        # Roster order here too: this dict is rendered verbatim into the manager
        # prompt, so its iteration order is part of the artifact.
        findings_by_aperture={
            a.id: findings_by_aperture[a.id]
            for a in apertures
            if a.id in findings_by_aperture
        },
    )
