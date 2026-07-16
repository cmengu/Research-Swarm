"""The three files the system maintains about itself.

watchlist.json  — what we watch (standing subscription addresses)
thesis.json     — what we believe (six falsifiable stances, human-seeded)
catalyst-queue.json — what we expect, when, and so what (rolling, dated)

They are version-controlled, so every self-edit is a diff someone can review
after the fact — which is what replaces an approval step.

This module only READS. The orchestrator is the sole machine writer, and state
writes land in the publish stage.

Spec: docs/spec/03-state-and-governance.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STATE_FILES = ("watchlist.json", "thesis.json", "catalyst-queue.json")


@dataclass(frozen=True)
class DanglingRef:
    """An entity_ids[] reference that resolves to no watchlist entity."""

    entity_id: str
    where: str


@dataclass(frozen=True)
class State:
    watchlist: dict
    thesis: dict
    catalyst_queue: dict

    @property
    def entity_ids(self) -> set[str]:
        """The spine. Stable slugs linking watchlist to issue to queue to findings.

        Note the roster mixes companies and assets — asset_daraxonrasib is a
        valid entity because tickers vanish on acquisition and assets don't.
        """
        return {e["entity_id"] for e in self.watchlist.get("entities", [])}


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"state file not found: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc


def load_state(state_dir: Path) -> State:
    """Load all three state files, or fail naming the one that broke."""
    state_dir = Path(state_dir)
    watchlist, thesis, queue = (_load_json(state_dir / name) for name in STATE_FILES)
    return State(watchlist=watchlist, thesis=thesis, catalyst_queue=queue)


def check_entity_refs(state: State) -> list[DanglingRef]:
    """Every entity_ids[] reference must resolve to a watchlist entity_id.

    This is the cross-file join check that stops the spine forking again: three
    assets once disagreed on the definition key (`id` vs `entity_id`) while
    agreeing on the reference key, and it went unnoticed until someone rendered
    a roster against fields no entity had.

    On `proposed_entity`: it is NOT an exemption, despite being easy to read as
    one. An off-roster find carries `entity_ids: []` AND a `proposed_entity`,
    so there is simply nothing to resolve — the empty list handles it, and no
    special case is needed. Treating the field as a blanket skip is actively
    harmful: an item carrying ["merck", "bogus"] plus a proposal would pass with
    "bogus" dangling, which defeats the one check this exists to perform. A
    named reference must resolve, whatever else the item also proposes.

    Returns every dangling ref, not just the first: a caller fixing these wants
    the whole list, not a game of whack-a-mole.
    """
    known = state.entity_ids

    return [
        DanglingRef(
            entity_id=entity_id,
            where=f"catalyst-queue.json:{item.get('id', '?')}",
        )
        for item in state.catalyst_queue.get("queue", [])
        for entity_id in item.get("entity_ids", [])
        if entity_id not in known
    ]
