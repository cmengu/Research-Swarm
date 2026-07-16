"""Rendering the shared researcher template.

prompts/researcher.md is a document ABOUT the template, with the template itself
fenced inside it. The fence is what we render; the surrounding design notes stay
out of the model's context.

The rule this module exists to enforce: state is interpolated FRESH at run time
and never baked into the template file. Stance text especially — a template that
inlines a stance means an owner can change their worldview and the next issue
still argues the old one, with nothing to show for it. That is the single
failure the thesis propagation contract exists to prevent.

Spec: docs/spec/04-researchers.md, docs/spec/03-state-and-governance.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from researchswarm.beats import Beat
from researchswarm.state import State

TEMPLATE_FENCE = re.compile(r"```text\n(.*?)```", re.DOTALL)
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

DORMANT_SLOT = "(no stance seeded)"
NO_CARVE_OUT = "No carve-outs."


class UnresolvedPlaceholder(ValueError):
    """A {{placeholder}} survived rendering.

    Never let this reach the model: a literal {{watchlist_roster}} in a prompt
    is an invitation to invent one.
    """


@dataclass(frozen=True)
class RunContext:
    run_id: str
    coverage_window_from: str
    coverage_window_to: str


def load_template(path: Path) -> str:
    """Extract the fenced template from the prompt document."""
    path = Path(path)
    match = TEMPLATE_FENCE.search(path.read_text())
    if not match:
        raise ValueError(f"{path}: no fenced ```text template block found")
    return match.group(1).strip()


def _watchlist_roster(state: State) -> str:
    """Compact roster: entity_id · name · tier · priority · watch_for.

    why_tracked is deliberately excluded. It is a summary, and summaries are the
    manager's job — handing one to a researcher hands it an interpretation.
    """
    return "\n".join(
        "- {entity_id} · {name} · {tier} · {priority} · {watch_for}".format(
            entity_id=e["entity_id"],
            name=e["name"],
            tier=e["tier"],
            priority=e["priority"],
            watch_for=", ".join(e.get("watch_for", [])) or "—",
        )
        for e in state.watchlist.get("entities", [])
    )


def _thesis_slots(state: State) -> str:
    """Per slot: id · title · [provenance], then the stance on its own line.

    A dormant slot renders a marker rather than an invention. Provenance rides
    along because four of six stances are provisional, and a lens the reader
    knows is provisional is safer than one presented as settled.

    This is the one deliberate departure from the placeholder-notes table, which
    reads `id · title · stance`. A stance is a paragraph — the seeded ones run to
    ~400 characters — and inlining that after a `·` produces a wall the model has
    to parse a delimiter out of. The table's shorthand doesn't survive the real
    field, so the stance gets its own line. Roster and queue follow the table
    exactly, because there the fields are short enough that it works.
    """
    lines = []
    for belief in state.thesis.get("beliefs", []):
        stance = belief.get("stance") or DORMANT_SLOT
        provenance = belief.get("stance_provenance", "unknown")
        lines.append(f"- {belief['id']} · {belief['title']} [{provenance}]\n  {stance}")
    return "\n".join(lines)


def _catalyst_queue_active(state: State) -> str:
    """Active items only: pending or slipped. delivered and dead are terminal."""
    lines = [
        "- {id} · {asset} · {entity_ids} · {catalyst} · {window} · {status}".format(
            id=item["id"],
            asset=item.get("asset", "—"),
            entity_ids=", ".join(item.get("entity_ids", [])) or "—",
            catalyst=item.get("catalyst", "—"),
            window=item.get("expected_window") or "window unscheduled",
            status=item["status"],
        )
        for item in state.catalyst_queue.get("queue", [])
        if item.get("status") in ("pending", "slipped")
    ]
    return "\n".join(lines) if lines else "- (no active catalysts)"


def render_researcher_prompt(
    template: str, beat: Beat, ctx: RunContext, state: State
) -> str:
    """Interpolate one beat's prompt. Raises if any placeholder is left over.

    surge_block and window_carveout resolve to their outside-a-window values.
    Surge mode fills them in when it lands; the placeholders still have to
    resolve to something now, and "no carve-outs" is the honest value.
    """
    values = {
        "beat_id": beat.id,
        "beat_name": beat.name,
        "beat_charter": beat.charter,
        "beat_seed_angles": "\n".join(f"- {angle}" for angle in beat.seed_angles),
        "beat_notes": beat.notes,
        "max_turns": str(beat.max_turns),
        "run_id": ctx.run_id,
        "coverage_window_from": ctx.coverage_window_from,
        "coverage_window_to": ctx.coverage_window_to,
        "surge_block": "",
        "window_carveout": NO_CARVE_OUT,
        "watchlist_roster": _watchlist_roster(state),
        "thesis_version": str(state.thesis.get("version", "?")),
        "thesis_slots": _thesis_slots(state),
        "queue_snapshot_date": state.catalyst_queue.get("last_recut_at") or "never re-cut",
        "catalyst_queue_active": _catalyst_queue_active(state),
    }

    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise UnresolvedPlaceholder(
                f"template references {{{{{key}}}}}, which nothing renders"
            )
        return values[key]

    return PLACEHOLDER.sub(substitute, template)
