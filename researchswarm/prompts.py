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
from researchswarm.calendar import SurgeState
from researchswarm.critic import REAFFIRMED
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
    surge: SurgeState | None = None

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


def _surge_block(surge: SurgeState | None) -> str:
    """The surge line in a researcher's run context — empty outside a window.

    Inside a window it names the conference and the day, matching the placeholder
    contract in prompts/researcher.md exactly (`- surge: … conference window …`)."""
    if surge is None:
        return ""
    return (
        f"- surge: {surge.window} day {surge.day} of {surge.of}, "
        f"conference window {surge.starts} → {surge.ends}"
    )


def _window_carveout(surge: SurgeState | None) -> str:
    """Sourcing rule 4's carve-out — so a researcher does not self-censor an
    in-window story that lands outside the narrowed one-day coverage window.

    Outside surge there is nothing to carve out. Inside, anything published within
    the conference window is fair game even if outside this run's coverage window
    ([04](docs/spec/04-researchers.md)) — the same reference-window shift the
    critic's provenance_stale check gets, handed to the researcher so the two
    never disagree about what counts as in-window."""
    if surge is None:
        return NO_CARVE_OUT
    return (
        f"Carve-out: during the current {surge.window} window, anything published "
        f"within the conference window ({surge.starts} → {surge.ends}) is fair game "
        "even if outside this run's one-day coverage window."
    )


def render_researcher_prompt(
    template: str, beat: Beat, ctx: RunContext, state: State
) -> str:
    """Interpolate one beat's prompt. Raises if any placeholder is left over.

    surge_block and window_carveout come from ctx.surge — empty / "no carve-outs"
    on a baseline run, the conference window and its carve-out inside a verified
    surge window, so an in-window story that lands outside the narrowed one-day
    coverage window is not self-censored (spec/02, spec/04).
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
        "surge_block": _surge_block(ctx.surge),
        "window_carveout": _window_carveout(ctx.surge),
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


def _findings_corpus(
    findings_by_beat: dict[str, dict], beats_failed: list[str] | None = None
) -> str:
    """Each beat's findings.json as a labelled JSON block, in caller order.

    One corpus renderer, shared by the manager prompt and the critic prompt, so
    the two never drift on how a finding is presented. `beats_failed` is the sole
    difference: the manager passes it (a list, possibly empty) and gets an explicit
    dead-beats line next to the facts — what lets it mark the hole inline rather
    than read a thin section as truth. The critic passes None and gets only the
    surviving findings; on an empty run that leaves nothing, so it renders an
    explicit marker rather than a blank.

    Beat order is whatever the caller passes (run.py keeps roster order).
    """
    blocks = [
        f"=== findings from beat: {beat_id} ===\n"
        f"{json.dumps(findings, indent=2, ensure_ascii=False)}"
        for beat_id, findings in findings_by_beat.items()
    ]
    if beats_failed is not None:
        failed = ", ".join(beats_failed) if beats_failed else "(none)"
        blocks.append(f"=== beats that failed (no findings this cycle): {failed} ===")
    if not blocks:
        return "(no findings on disk this run)"
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


def _surge_window_block(surge: SurgeState | None) -> str:
    """The conference window the critic compares provenance_stale against in surge.

    `run.surge` in the issue carries only {window, day, of} (the dashboard's
    shape), so the critic cannot read the dates from the issue — it gets them here.
    Outside surge this says so, and the critic falls back to issue.coverage_window
    exactly as always (spec/02 the critic's bar does not move — with one fix)."""
    if surge is None:
        return "(no surge this cycle — compare provenance_stale against issue.coverage_window)"
    return (
        f"run.surge is present: {surge.window}. Compare provenance_stale against this "
        f"CONFERENCE window — published_at from {surge.starts} to {surge.ends} inclusive "
        "is in-window — NOT the run's narrowed one-day coverage_window."
    )


def render_critic_prompt(
    template: str,
    *,
    issue: dict,
    findings_by_beat: dict[str, dict],
    previous_issue: dict | None,
    watchlist: dict,
    thesis: dict,
    surge: SurgeState | None = None,
) -> str:
    """Interpolate the critic rubric with its five inputs. Raises on a leftover.

    The load-bearing decision of the whole rubric (spec/06): the critic sees FIVE
    things, not just the finished issue. A critic holding only the digest cannot
    audit an ABSENCE, because the absence was removed from the artifact it is
    reading — so it also gets the raw findings (the receipt source), the previous
    issue (continuity), the watchlist (entity accounting), and the thesis
    (thesis_impact honesty and dormant-slot exemptions). Widening the input set is
    what turns "you missed a story" from unanswerable into a diff.

    The same UnresolvedPlaceholder wall the other renderers use applies: a literal
    {{issue_json}} reaching Codex is an instruction to invent the thing it should
    be judging.
    """
    values = {
        "issue_json": json.dumps(issue, indent=2, ensure_ascii=False),
        "findings_corpus": _findings_corpus(findings_by_beat),
        "previous_issue_json": (
            json.dumps(previous_issue, indent=2, ensure_ascii=False)
            if previous_issue is not None
            else "(no previous issue)"
        ),
        "watchlist_json": json.dumps(watchlist, indent=2, ensure_ascii=False),
        "thesis_json": json.dumps(thesis, indent=2, ensure_ascii=False),
        "surge_window": _surge_window_block(surge),
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


def _critic_findings_block(findings) -> str:
    """The critic's blocking findings as retry instructions, one per finding.

    Unlike the validator's findings (Finding objects with .kind/.where/.note),
    these are the critic's dicts, and each may carry a `rebuttal` the critic has
    already REAFFIRMED. A reaffirmed finding is marked so the manager COMPLIES
    (retry 2) rather than rebutting a second time — the critic had final say. A
    fresh finding is open to a fix OR a sourced rebuttal; the template states that
    rule, this block only flags which findings have already been through it. An
    empty list should never render (the loop only retries on a block), so it
    surfaces as an explicit marker rather than a blank."""
    if not findings:
        return "(no blocking findings — nothing to fix)"
    lines = []
    for finding in findings:
        lines.append(
            f"- {finding.get('kind')} at {finding.get('where')}: {finding.get('note', '')}"
        )
        rebuttal = finding.get("rebuttal") or {}
        if rebuttal.get("adjudication") == REAFFIRMED:
            lines.append(
                "  REAFFIRMED by the critic — it weighed your rebuttal and stood by "
                "this finding. COMPLY now: edit the draft to fix it. Do not rebut again."
            )
    return "\n".join(lines)


# The per-round directive, filled into {{round_directive}}. Retry 1 opens the
# rebuttal channel; retry 2 (the final round) closes it — comply-only, so a
# reaffirmed finding cannot be rebutted a second time (spec/06 rebut-once).
_ROUND_REBUT = (
    "- For each FRESH finding you have a choice:\n"
    "    1. FIX it — edit the draft so the claim no longer outruns its sources. If\n"
    "       you fix a finding by removing a claim, record it in\n"
    "       quiet_this_cycle.critic_catches so the cut leaves a trace.\n"
    "    2. REBUT it — if you believe the finding is wrong, attach a `rebuttal` to\n"
    "       that finding inside critic_report.blocking_findings, of the shape\n"
    '       {"text": "...", "sources": [ <source objects> ]}: a sourced argument,\n'
    "       not an assertion. You may NOT silently ignore a finding.\n"
    "- For a finding marked REAFFIRMED, the critic already overruled your rebuttal\n"
    "  — COMPLY: fix it, do not rebut it again."
)
_ROUND_COMPLY = (
    "- This is your FINAL retry: the rebuttal channel is CLOSED. COMPLY with EVERY\n"
    "  finding below by editing the draft to fix it — the critic has had its say,\n"
    "  and any rebuttal you file now is ignored. A finding you do not fix publishes\n"
    "  with the dispute printed under a reader-visible banner."
)


def render_critic_retry_prompt(
    template: str, *, prior_draft: dict, blocking_findings, final_round: bool
) -> str:
    """Interpolate the critic-retry prompt. Raises if a placeholder is left.

    The manager receives exactly two things — its own prior draft and the critic's
    blocking findings — and EDITS the draft ([05](docs/spec/05-manager.md#the-rebuttal-channel)).
    `final_round` swaps the directive: retry 1 lets it fix OR file a sourced
    rebuttal; retry 2 is comply-only, so a reaffirmed finding cannot be rebutted
    twice. The same UnresolvedPlaceholder wall the other renderers use applies."""
    values = {
        "prior_draft_json": json.dumps(prior_draft, indent=2, ensure_ascii=False),
        "blocking_findings": _critic_findings_block(blocking_findings),
        "round_directive": _ROUND_COMPLY if final_round else _ROUND_REBUT,
    }
    return _substitute(template, values)
