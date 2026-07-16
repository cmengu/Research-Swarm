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

import json
import re
from dataclasses import dataclass
from pathlib import Path

from researchswarm.beats import Beat
from researchswarm.state import State

# The factual fields a catalyst-queue item carries into the published snapshot.
# what_it_would_prove is DELIBERATELY absent: the manager authors it (thesis-
# gated), so handing it the pre-existing value would invite a copy where an
# argument belongs. seed_note is internal scaffolding and never rendered.
QUEUE_SNAPSHOT_FIELDS = (
    "id", "asset", "entity_ids", "holders", "catalyst", "first_expected_window",
    "expected_window", "window_source", "status", "slip_log", "bears_on_thesis_slot",
    "sources",
)

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

    @property
    def window(self) -> dict:
        """The window in the {from, to} shape the findings contract echoes."""
        return {"from": self.coverage_window_from, "to": self.coverage_window_to}


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

    return _substitute(template, values)


def _substitute(template: str, values: dict[str, str]) -> str:
    """Fill every {{placeholder}}, or raise if one has no value.

    Shared by both renderers: a literal {{leftover}} reaching a model is a
    silent instruction to invent, and it must never happen for either role.
    """

    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise UnresolvedPlaceholder(
                f"template references {{{{{key}}}}}, which nothing renders"
            )
        return values[key]

    return PLACEHOLDER.sub(substitute, template)


def _manager_watchlist_roster(state: State) -> str:
    """Full roster: entity_id · name · tier · priority, one line each.

    Unlike the researcher roster, this keeps EVERY entity, not just the ones a
    beat touches: the manager's accounting duty is that every tracked entity
    lands in watchlist or quiet_this_cycle, so it needs the whole set in front
    of it. name and tier are what the manager authors each entry's name/type
    from — watch_for is dropped here because the manager is deciding placement,
    not running a coverage sweep.
    """
    return "\n".join(
        "- {entity_id} · {name} · {tier} · {priority}".format(
            entity_id=e["entity_id"],
            name=e["name"],
            tier=e["tier"],
            priority=e["priority"],
        )
        for e in state.watchlist.get("entities", [])
    )


def _catalyst_queue_snapshot(state: State) -> str:
    """The queue as indented JSON, not a table.

    The manager must reproduce the factual fields VERBATIM into the published
    snapshot, so it is handed JSON to copy rather than a compact line it would
    have to re-serialise (and could silently mangle). Every item is included
    regardless of status: the snapshot freezes the whole queue at publication,
    not just the active slice a researcher chases.
    """
    snapshot = {
        "snapshot_of": "state/catalyst-queue.json",
        "recut_at": state.catalyst_queue.get("last_recut_at"),
        "items": [
            {field: item.get(field) for field in QUEUE_SNAPSHOT_FIELDS}
            for item in state.catalyst_queue.get("queue", [])
        ],
    }
    # ensure_ascii=False keeps em-dashes and the like literal — the model reads
    # cleaner text, and the manager can reproduce the fields byte-for-byte.
    return json.dumps(snapshot, indent=2, ensure_ascii=False)


def _findings_corpus(findings_by_beat: dict[str, dict], beats_failed: list[str]) -> str:
    """Each surviving beat's findings.json as a labelled JSON block.

    Beat order is whatever the caller passes (run.py keeps roster order). The
    failed beats get an explicit line rather than a block, because they have no
    findings — and naming them here, next to the facts, is what lets the manager
    see the hole it must mark inline rather than reading a thin section as truth.
    """
    blocks = [
        f"=== findings from beat: {beat_id} ===\n"
        f"{json.dumps(findings, indent=2, ensure_ascii=False)}"
        for beat_id, findings in findings_by_beat.items()
    ]
    failed = ", ".join(beats_failed) if beats_failed else "(none)"
    blocks.append(f"=== beats that failed (no findings this cycle): {failed} ===")
    return "\n\n".join(blocks)


def _prior_quiet_counts(prior_quiet: dict[str, int]) -> str:
    """entity_id: cycles_quiet lines the manager increments from.

    An empty map renders as run #1's honest value: there is no previous issue to
    increment from, so every quiet entity this cycle starts at 1.
    """
    if not prior_quiet:
        return "(no previous issue)"
    return "\n".join(f"- {entity_id}: {count}" for entity_id, count in sorted(prior_quiet.items()))


def render_manager_prompt(
    template: str,
    ctx: RunContext,
    state: State,
    *,
    findings_by_beat: dict[str, dict],
    beats_failed: list[str],
    prior_quiet: dict[str, int],
    models: dict,
    issue_id: str,
    published_at: str,
) -> str:
    """Interpolate the manager prompt. Raises if any placeholder is left over.

    Stances arrive via _thesis_slots exactly as the researcher sees them — read
    fresh, dormant slots marked, provenance attached — because the propagation
    contract binds the manager as tightly as the researcher: an owner who edits
    a stance must see the next issue argue the new one, and a template that
    inlined stance text would break that silently.
    """
    values = {
        "run_id": ctx.run_id,
        "thesis_version": str(state.thesis.get("version", "?")),
        "issue_id": issue_id,
        "published_at": published_at,
        "coverage_window_from": ctx.coverage_window_from,
        "coverage_window_to": ctx.coverage_window_to,
        "models_json": json.dumps(models, indent=2),
        "watchlist_roster": _manager_watchlist_roster(state),
        "thesis_slots": _thesis_slots(state),
        "catalyst_queue_snapshot": _catalyst_queue_snapshot(state),
        "prior_quiet_counts": _prior_quiet_counts(prior_quiet),
        "beats_failed": ", ".join(beats_failed) if beats_failed else "(none)",
        "findings_corpus": _findings_corpus(findings_by_beat, beats_failed),
    }
    return _substitute(template, values)


def _blocking_findings_block(findings) -> str:
    """The validator's blocking findings as one `- kind at where: note` line each.

    Only blocking findings reach here — advisories are the record, not a to-do
    list, and including them would invite the manager to churn sections the gate
    never faulted. An empty list should never be rendered (the loop only retries
    on a block), so it surfaces as an explicit marker rather than a blank.
    """
    if not findings:
        return "(no blocking findings — nothing to fix)"
    return "\n".join(f"- {f.kind} at {f.where}: {f.note}" for f in findings)


def render_manager_retry_prompt(template: str, *, prior_draft: dict, blocking_findings) -> str:
    """Interpolate the validation-retry prompt. Raises if a placeholder is left.

    The manager receives exactly two things — its own prior draft and the
    blocking findings — because it EDITS that draft rather than regenerating it
    ([05](docs/spec/05-manager.md#in-the-retry-loop)). The same
    UnresolvedPlaceholder wall the other renderers use applies: a literal
    {{prior_draft_json}} reaching the model is an instruction to invent a draft.
    """
    values = {
        "prior_draft_json": json.dumps(prior_draft, indent=2, ensure_ascii=False),
        "blocking_findings": _blocking_findings_block(blocking_findings),
    }
    return _substitute(template, values)
