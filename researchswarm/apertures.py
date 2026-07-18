"""The aperture roster — what replaced the six beats (spec/04).

The pivot replaced the six fixed beats with **apertures**: scans defined by
`relation-tier × scope`, DERIVED from the program rather than listed in a config
file. `beats.toml` is gone; a program's `config/programs/<id>.toml` plus this
planner produce the run's scans. The template pattern survives — apertures differ
in SCOPE, never in RULES, so trust tiers, citation discipline, the read-only wall
and the findings contract still live once in `prompts/researcher.md`.

The roster is `1 + N + 1` (spec/04 aperture roster), bounded by config, not by
the competitor list:

    biology_scan            1 per program   target + moa, indication-blind
                                            (carries mechanism + target twins)
    arena_scan:<indication> N per program   one per indication
                                            (carries setting rivals + benchmark/SOC)
    house_sweep             1, fixed        the wider board, aimed
                                            (interest-steering, BD, threat, blind spots)

Cost is `FIXED + N × (one sonnet arena scan)`. A priority_indication's arena scan
is DORMANT — event-triggered, slow (SOC moves in years), rendered as a dormancy
degradation rather than run every cycle; only `active_arena` indications scan.

This module is pure: `plan_apertures(program)` is a total function of the program
config. The findings shape and the researcher prompt (which need a live run to
verify) are separate — this is the deterministic skeleton they hang on.

Spec: docs/spec/04-researchers.md, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

from dataclasses import dataclass

from researchswarm.programs import Program

BIOLOGY_SCAN = "biology_scan"
ARENA_SCAN = "arena_scan"
HOUSE_SWEEP = "house_sweep"

# The role that makes an indication's arena scan run this cycle. A
# priority_indication is tracked but its arena scan is dormant (event-triggered).
ACTIVE_ARENA_ROLE = "active_arena"


@dataclass(frozen=True)
class Aperture:
    """One scan the run will fan out (or skip, when dormant).

    `id` is the stable slug the findings file is keyed by
    (`runs/<run_id>/findings/<id>.json`) and the value `sources_and_method.apertures_run`
    records. `kind` is the template family (one of the three); `scope` is the
    human-readable scope string interpolated into the shared researcher prompt and
    echoed into `apertures_run[].scope`. `active` is False only for a dormant
    arena scan — it stays in the roster (so the dormancy renders) but is not run.
    """

    id: str
    kind: str
    scope: str
    active: bool

    @property
    def dormant(self) -> bool:
        return not self.active


def plan_apertures(program: Program) -> list[Aperture]:
    """The `1 + N + 1` aperture roster for a program — a total function of config.

    One biology scan (target + moa, indication-blind), one arena scan per
    indication (active only for `active_arena` roles), and one house sweep. The
    ordering is stable — biology, then arenas in config order, then house — so the
    run's fan-out and the audit trail read the same way every cycle.
    """
    apertures = [
        Aperture(
            id=BIOLOGY_SCAN,
            kind=BIOLOGY_SCAN,
            scope=f"target={program.target}, moa={program.moa}",
            active=True,
        )
    ]
    for indication in program.indications:
        apertures.append(
            Aperture(
                id=f"{ARENA_SCAN}:{indication.id}",
                kind=ARENA_SCAN,
                scope=indication.id,
                active=indication.role == ACTIVE_ARENA_ROLE,
            )
        )
    apertures.append(
        Aperture(
            id=HOUSE_SWEEP,
            kind=HOUSE_SWEEP,
            scope="partnership_bd + threat_financing + blind_spots",
            active=True,
        )
    )
    return apertures


def active_apertures(program: Program) -> list[Aperture]:
    """Just the apertures that actually fan out this cycle — the cost driver.

    Dormant arena scans are excluded: they render a dormancy degradation in the
    section they would have fed, but spend no model budget. So `len(active_apertures)`
    is the run's real agent count, `1 + (active arenas) + 1`.
    """
    return [a for a in plan_apertures(program) if a.active]
