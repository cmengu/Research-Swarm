"""The v2 program layer — config the detective reads about its subject.

The pivot split the old flat `watchlist.json` into layers so cost scales with
apertures, not programs (spec/03, spec/09 scaling):

    config/programs/<id>.toml     one detective per drug — target, moa, aperture
    config/interests.toml         the one steering wheel — human-owned
    state/entities/<id>.json      shared FACTS about a competitor, program-agnostic
    state/programs/<id>/edges.json   why entity X is a competitor TO this program
    state/programs/<id>/catalyst-queue.json   per-program predictions

This module only READS, and only the two CONFIG surfaces plus the program's
state layer. It is deliberately separate from `state.py` (which still loads the
v1 flat state): the two shapes run side by side while the engine migrates, and
nothing here touches the v1 loaders. The orchestrator remains the sole writer;
the two config surfaces (the aperture and the interest list) are NEVER
machine-written (governance clause 4).

What this module does NOT do: populate `state/entities/` or the relation edges.
That is the deferred roster-migration curation session (spec/03 "Migrating the
seeded roster"), a human call, not a compile step. The loaders read whatever is
there — empty at seed — and derive the cold-start roster from `seed_competitors`.

Spec: docs/spec/03-state-and-governance.md
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# ⚑ The interest-list rot default (spec/03 the interest list, #55): a list not
# edited in this many months renders a whole-list stale marker on the digest.
# Config-shaped as a constant here rather than a magic number at the call site.
INTEREST_ROT_MONTHS = 6

# Roughly a month in days — the interest list rots on a coarse 6-month clock, so
# a calendar-exact month subtraction would be false precision. Kept explicit so
# the approximation is a decision, not an accident.
_DAYS_PER_MONTH = 30.44


# ---------------------------------------------------------------------------
# config/programs/<id>.toml — the program instance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Indication:
    """A first-class indication (spec/03, #50): line is a property of a
    benchmark, not of an indication, so an indication carries a `role`, not a
    line. `active_arena` runs an arena scan this cycle; `priority_indication` is
    tracked but its arena scan is event-triggered."""

    id: str
    role: str


@dataclass(frozen=True)
class Program:
    """What the pipeline reads from `config/programs/<id>.toml`.

    `moa` is a load-bearing scan field, not description — it is what separates a
    target_twin (same target, different MOA) from a mechanism_twin (same target
    AND MOA), the distinction the whole competitor model turns on. `seed_competitors`
    is the cold-start typing path, NOT the migrated v1 roster.
    """

    id: str
    name: str
    sponsor: str
    modality: str
    target: str
    moa: str
    indications: tuple[Indication, ...]
    cadence_baseline: str
    cold_start_lookback_days: int
    seed_competitors: tuple[str, ...]

    @property
    def active_arena_ids(self) -> tuple[str, ...]:
        """Indications whose arena scan runs this cycle — the `1 + N + 1` in the
        aperture count is one arena scan per active_arena indication ([04])."""
        return tuple(i.id for i in self.indications if i.role == "active_arena")


def load_program(config_dir: Path, program_id: str) -> Program:
    """Load one program's aperture config, or fail naming the file that broke."""
    path = Path(config_dir) / "programs" / f"{program_id}.toml"
    raw = _load_toml(path)
    program = raw.get("program", {})
    cadence = raw.get("cadence", {})
    indications = tuple(
        Indication(id=i["id"], role=i["role"]) for i in raw.get("indication", [])
    )
    return Program(
        id=program["id"],
        name=program["name"],
        sponsor=program["sponsor"],
        modality=program["modality"],
        target=program["target"],
        moa=program["moa"],
        indications=indications,
        cadence_baseline=cadence.get("baseline", "monthly"),
        cold_start_lookback_days=cadence.get("cold_start_lookback_days", 7),
        seed_competitors=tuple(program.get("seed_competitors", [])),
    )


# ---------------------------------------------------------------------------
# config/interests.toml — the one steering wheel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Interest:
    """One steering instruction (spec/03, #55): an enum `tier` (a sort key + a
    default admission bar, not a score) plus a free-text `note` injected into the
    manager prompt to steer attention, interpretation and the bar."""

    tier: str
    note: str


@dataclass(frozen=True)
class InterestList:
    """`config/interests.toml` — human-owned, version-stamped, read fresh.

    `version` stamps which list steered a run (`issue.run.interest_list_version`);
    the propagation contract applies (owner edit → version bump, no carry-over).
    """

    version: int
    last_edited: str
    last_edited_by: str
    interests: tuple[Interest, ...]

    def is_stale(self, today: date, *, months: int = INTEREST_ROT_MONTHS) -> bool:
        """True when the list has not been edited within `months` (spec/03 rot).

        The trigger is a date the orchestrator holds, so `interest_list_stale`
        passes admission test 2 — a fail-visible degradation, not a silent one.
        A malformed or missing `last_edited` reads as stale: an unknowable edit
        date is exactly the case the marker exists to surface, never to hide.
        """
        try:
            edited = date.fromisoformat(self.last_edited)
        except (TypeError, ValueError):
            return True
        return (today - edited).days > round(months * _DAYS_PER_MONTH)


def load_interests(config_dir: Path) -> InterestList:
    """Load the interest list, or fail naming the file that broke."""
    raw = _load_toml(Path(config_dir) / "interests.toml")
    return InterestList(
        version=raw.get("version", 1),
        last_edited=raw.get("last_edited", ""),
        last_edited_by=raw.get("last_edited_by", "owner"),
        interests=tuple(
            Interest(tier=i["tier"], note=i["note"]) for i in raw.get("interest", [])
        ),
    )


# ---------------------------------------------------------------------------
# state/programs/<id>/ and state/entities/ — the machine-maintained layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    """A relation edge: `(program_id x entity_id) -> relation + read_through`.

    The shared FACT about the entity lives once in `state/entities/`; the
    read-through — what that fact means for THIS program — is the edge. `drift_log`
    is append-only (every retype/refine), the tamper-evidence for a typing change.
    """

    entity_id: str
    relation: str
    read_through: dict
    promoted_by: str
    drift_log: tuple[dict, ...]


def load_edges(state_dir: Path, program_id: str) -> list[Edge]:
    """Load a program's relation edges. Empty at seed — the roster migration is a
    deferred HITL session, and edges are promoted at run time."""
    path = Path(state_dir) / "programs" / program_id / "edges.json"
    raw = _load_json(path)
    return [
        Edge(
            entity_id=e["entity_id"],
            relation=e["relation"],
            read_through=e.get("read_through", {}),
            promoted_by=e.get("promoted_by", ""),
            drift_log=tuple(e.get("drift_log", [])),
        )
        for e in raw.get("edges", [])
    ]


def load_entities(state_dir: Path) -> dict[str, dict]:
    """Load the shared fact layer — one record per `entity_id`, program-agnostic.

    Keyed by the record's own `entity_id` (falling back to the filename stem), so
    a mismatch between the two does not silently fork the spine. Empty at seed:
    records are materialized from the issue archive at run time.
    """
    entities_dir = Path(state_dir) / "entities"
    records: dict[str, dict] = {}
    if not entities_dir.exists():
        return records
    for path in sorted(entities_dir.glob("*.json")):
        record = _load_json(path)
        records[record.get("entity_id", path.stem)] = record
    return records


def program_roster(program: Program, edges: list[Edge]) -> set[str]:
    """The entities tracked for this program: every promoted edge, plus the
    cold-start `seed_competitors` not yet promoted.

    This is the v2 replacement for v1's flat `state.entity_ids` — the roster the
    coverage check will hold every competitor accountable against. At seed, with
    no edges written, it is exactly `seed_competitors`.
    """
    return {e.entity_id for e in edges} | set(program.seed_competitors)


# ---------------------------------------------------------------------------
# Loading primitives — fail naming the file that broke
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path.name} is not valid TOML: {exc}") from exc


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"state file not found: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
