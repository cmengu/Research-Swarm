"""The beat roster.

Beats differ in SCOPE, never in RULES. Trust tiers, citation discipline,
read-only expectations and the output contract are identical for all six and
live once, in prompts/researcher.md. That is why adding a seventh beat is a
[[beat]] block and not a code change — and why this module is this short.

Spec: docs/spec/04-researchers.md
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Beat:
    id: str
    name: str
    charter: str
    seed_angles: list[str]
    notes: str
    model: str
    max_turns: int


def load_beats(path: Path) -> list[Beat]:
    """Load config/beats.toml. Per-beat model/max_turns override [defaults]."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"beats config not found: {path}")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    defaults = raw.get("defaults", {})
    blocks = raw.get("beat", [])
    if not blocks:
        raise ValueError(f"{path}: no [[beat]] blocks")

    beats = [
        Beat(
            id=block["id"],
            name=block["name"],
            charter=block["charter"].strip(),
            seed_angles=block.get("seed_angles", []),
            notes=block.get("notes", "").strip(),
            model=block.get("model", defaults.get("model", "sonnet")),
            max_turns=block.get("max_turns", defaults.get("max_turns", 30)),
        )
        for block in blocks
    ]

    ids = [b.id for b in beats]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        # Two beats sharing an id would silently overwrite each other's findings
        # file, and the loss would look like a quiet beat rather than a bug.
        raise ValueError(f"{path}: duplicate beat id(s): {sorted(duplicates)}")

    return beats
