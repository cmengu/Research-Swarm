"""The governance contract made real — the three state files editing themselves.

Publish (publish.py) owns the *publication* recipe: the issue reaches disk, the
manifest regenerates, the run commits. THIS module owns the other half of stage
6 — the self-edits to state/watchlist.json, state/thesis.json and
state/catalyst-queue.json. They are separated because they answer different
questions: publication is "how does the digest become a readable, committed
artifact", governance is "how does the loop change its own memory without a human
in the loop, and leave a trail that a human could review after the fact" ([03]).

Every write here obeys the one contract stated once in spec/03:

  - the orchestrator is the sole machine writer, and only during a run;
  - every write cites its run_id and appends to that file's own log;
  - facts are machine-authored, interpretation is not — the loop may revise an
    ACTIVE stance but must never author one into a dormant slot;
  - the per-file immutability carve-outs (first_expected_window never changes;
    expected_window changes only with a slip_log entry) are defended HERE too,
    not only in the validator — a state writer that trusted the gate to have
    caught everything would be asymmetric defense-in-depth.

Each of the three writers returns `(path, changed)` and rewrites its file only
when `changed`, so a quiet cycle stages nothing and the diff is exactly the edit.

Spec: docs/spec/03-state-and-governance.md, docs/spec/09-orchestrator.md
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from researchswarm.state import State
from researchswarm.validator import transition_brings_new_evidence

log = logging.getLogger("researchswarm.publish")

# new_on_radar.type → watchlist tier, for accepted promotions. The radar surfaces
# emerging stories; a fresh promotion has NOT earned a market-structural claim, so
# the honest default is `frontier_asset` — "tracked as an asset, not a ticker,
# because the tickers keep disappearing" ([03] tiers). china_supply and platform
# are deliberately absent from the default: each asserts a price-setting or
# direction-setting role a radar item cannot have demonstrated in one cycle, and
# stamping one would launder an unproven claim into the roster's spine. A type
# that DOES carry its role (a big-pharma acquirer, a regulator) maps to it.
TYPE_TO_TIER = {
    "big_pharma": "acquirer",
    "acquirer": "acquirer",
    "regulator": "regulator",
    "china_pharma": "china_supply",
    "china_supply": "china_supply",
    "platform": "platform",
    "frontier_asset": "frontier_asset",
    "asset": "frontier_asset",
}
DEFAULT_TIER = "frontier_asset"


def write_json(path: Path, data) -> None:
    """Write `data` as JSON with 2-space indent and a trailing newline.

    Matches the formatting of the seeded state files exactly, so a state edit
    shows in `git diff` as the lines that changed and nothing else — the diff is
    the review, and a reformatting churn would drown the one line that matters.
    Shared with publish.py, which writes the issue and manifest to the same
    formatting contract so every file the run touches reads identically.
    """
    path.write_text(json.dumps(data, indent=2) + "\n")


def apply_state_edits(root: Path, issue, state: State, run_id: str, now: datetime) -> list[Path]:
    """Apply promotions, thesis revisions and queue transitions to state/.

    Each of the three writers is independent and self-contained; each cites the
    run_id and appends to its file's own log; each writes its file back only if it
    actually changed something. Returns the paths that were rewritten — what the
    commit stages. The order is arbitrary (they touch different files); listing
    them here keeps the recipe legible.
    """
    date = now.date().isoformat()
    touched: list[Path] = []
    for path, changed in (
        _apply_promotions(root, issue, state, run_id, date),
        _apply_thesis_updates(root, issue, state, run_id, date),
        _apply_queue_transitions(root, issue, state, run_id, date),
    ):
        if changed:
            touched.append(path)
    return touched


def _apply_promotions(root: Path, issue, state: State, run_id: str, date: str) -> tuple[Path, bool]:
    """Accepted promotion_proposals become watchlist entities, with a drift_log.

    Self-maintaining watchlist, no human approval — but the reason is written down
    so drift is auditable ([03] auto_promote). For each new_on_radar entry whose
    proposal says promote_to_watchlist, a new entity is appended: tier mapped from
    its type (TYPE_TO_TIER, defaulting to frontier_asset), priority and categories
    carried across, why_tracked taken from the proposal's reason. A proposal whose
    entity_id already exists is skipped with a log line — a promotion is an add,
    never an edit of a standing entity.
    """
    path = root / "state" / "watchlist.json"
    watchlist = state.watchlist
    existing = state.entity_ids
    entities = watchlist.setdefault("entities", [])
    drift_log = watchlist.setdefault("drift_log", [])
    changed = False

    for entry in issue.get("new_on_radar") or []:
        proposal = (entry or {}).get("promotion_proposal") or {}
        if not proposal.get("promote_to_watchlist"):
            continue
        entity_id = entry.get("entity_id")
        if not entity_id:
            log.warning("publish: promotion with no entity_id — skipped")
            continue
        if entity_id in existing:
            log.info("publish: %s already on the watchlist — promotion skipped", entity_id)
            continue

        tier = TYPE_TO_TIER.get(entry.get("type"), DEFAULT_TIER)
        entities.append({
            "entity_id": entity_id,
            "name": entry.get("name"),
            "tier": tier,
            "priority": entry.get("priority"),
            "why_tracked": proposal.get("reason"),
            "watch_for": entry.get("categories") or [],
        })
        drift_log.append({
            "date": date,
            "action": "promoted",
            "entity_id": entity_id,
            "reason": proposal.get("reason"),
            "run_id": run_id,
        })
        existing = existing | {entity_id}
        changed = True
        log.info("publish: promoted %s to the watchlist (tier %s)", entity_id, tier)

    if changed:
        _bump(watchlist)
        write_json(path, watchlist)
    return path, changed


def _apply_thesis_updates(root: Path, issue, state: State, run_id: str, date: str) -> tuple[Path, bool]:
    """Thesis revisions become stance changes, with a per-slot drift_log entry.

    The loop may revise an ACTIVE belief (non-null stance) and must log every
    revision ([03]). It may NEVER author a stance into a DORMANT slot — a
    thesis_updates entry targeting a null-stance slot is a contract violation
    upstream (the manager must not improvise an opinion), so it is skipped LOUDLY
    rather than applied. Symmetrically, it may never null an active stance BACK to
    dormancy: a null/empty `after` is an owner-only transition ([03] dormancy is
    human territory), also refused loudly. On apply: stance becomes `after`, the
    slot's drift_log gains {date, from_stance, to_stance, trigger, cycle_id}, and
    the file's version bumps with last_evolved_at / last_edited_by recording that
    the loop, not the owner, moved it — the distinction the contract turns on.
    """
    path = root / "state" / "thesis.json"
    thesis = state.thesis
    beliefs = {b.get("id"): b for b in thesis.get("beliefs", []) if isinstance(b, dict)}
    changed = False

    for update in issue.get("thesis_updates") or []:
        slot_id = (update or {}).get("field")
        belief = beliefs.get(slot_id)
        if belief is None:
            log.warning("publish: thesis_update targets unknown slot %r — skipped", slot_id)
            continue
        if belief.get("stance") is None:
            # Authoring a stance into a dormant slot is a contract violation, not
            # a judgment call: the loop must never fill an unowned opinion.
            log.warning(
                "publish: thesis_update targets DORMANT slot %r (null stance) — "
                "refusing to author a stance the owner never seeded",
                slot_id,
            )
            continue

        to_stance = update.get("after")
        if not to_stance:
            # Nulling an active stance back to dormancy is an owner-only act — the
            # loop may revise a belief, never retire it to unowned.
            log.warning(
                "publish: thesis_update for slot %r has null/empty 'after' — the loop "
                "may revise a stance, never null it back to dormancy; skipped",
                slot_id,
            )
            continue

        from_stance = belief.get("stance")
        belief["stance"] = to_stance
        belief.setdefault("drift_log", []).append({
            "date": date,
            "from_stance": from_stance,
            "to_stance": to_stance,
            "trigger": update.get("triggered_by") or [],
            # cycle_id is spec/03's mandated field name for a thesis slot's
            # drift_log (the watchlist and queue logs name the same fact run_id).
            "cycle_id": run_id,
        })
        changed = True
        log.info("publish: thesis slot %r revised by the loop", slot_id)

    if changed:
        _bump(thesis)
        thesis["last_evolved_at"] = date
        write_json(path, thesis)
    return path, changed


def _apply_queue_transitions(root: Path, issue, state: State, run_id: str, date: str) -> tuple[Path, bool]:
    """Queue status / window transitions apply to state, under the source rule.

    Diffs the issue's catalyst_queue snapshot against state per item id. A
    transition applies ONLY when the item carries a new source relative to state —
    the SAME "no source, no transition" rule the validator enforces
    (transition_brings_new_evidence, shared), so the publisher can never write a
    transition the validator would have blocked. Skipped transitions are logged.

    The immutability carve-outs are defended HERE too, not just upstream:

      - first_expected_window is NEVER changed in state; an issue whose snapshot
        differs is upstream tamper the validator should have caught, so the whole
        item is skipped and logged rather than laundered;
      - an expected_window change is refused unless the snapshot carries a
        slip_log entry recording exactly that from→to transition — the state
        writer defends the slip invariant symmetrically with the source one.

    Items are matched by id only: this stage never adds or retires (the monthly
    re-cut, build 10, owns that). Every applied transition appends to the queue's
    drift_log with the run_id.
    """
    path = root / "state" / "catalyst-queue.json"
    queue = state.catalyst_queue
    state_items = {it.get("id"): it for it in queue.get("queue", []) if isinstance(it, dict)}
    drift_log = queue.setdefault("drift_log", [])
    changed = False

    for snap in issue.get("catalyst_queue", {}).get("items") or []:
        if not isinstance(snap, dict):
            continue
        item_id = snap.get("id")
        current = state_items.get(item_id)
        if current is None:
            log.info("publish: queue item %r not in state (add/retire is monthly) — skipped", item_id)
            continue

        if snap.get("first_expected_window") != current.get("first_expected_window"):
            log.warning(
                "publish: queue item %r first_expected_window differs from state "
                "(%r → %r) — immutable, refusing to propagate; skipping the item",
                item_id, current.get("first_expected_window"), snap.get("first_expected_window"),
            )
            continue

        status_changed = snap.get("status") != current.get("status")
        window_changed = snap.get("expected_window") != current.get("expected_window")
        if window_changed and not _records_slip(
            snap, current.get("expected_window"), snap.get("expected_window")
        ):
            # A window revision with no slip_log entry recording it is exactly the
            # goalpost-move the accountability invariant forbids — refuse the
            # window change even if a status change on the same item is valid.
            log.warning(
                "publish: queue item %r expected_window changed (%r → %r) with no slip_log "
                "entry recording it — refusing the window change",
                item_id, current.get("expected_window"), snap.get("expected_window"),
            )
            window_changed = False
        if not status_changed and not window_changed:
            continue

        if not transition_brings_new_evidence(snap, current):
            log.warning(
                "publish: queue item %r transition carries no source new to state — "
                "no source, no transition; skipped", item_id,
            )
            continue

        detail = []
        if status_changed:
            current["status"] = snap.get("status")
            detail.append(f"status {snap.get('status')!r}")
        if window_changed:
            _append_new_slips(current, snap, date)
            current["expected_window"] = snap.get("expected_window")
            detail.append(f"window {snap.get('expected_window')!r}")
        # Refresh the machine-authored evidence. window_source is the single source
        # justifying the CURRENT window, so it is replaced; sources[] is the item's
        # accumulating citation record, so it is UNIONED — replacing it would erase
        # history and let a dropped-then-reappearing URL count as "new evidence" for
        # a later transition, hollowing out the no-source-no-transition rule.
        current["window_source"] = snap.get("window_source")
        current["sources"] = _union_sources(current.get("sources"), snap.get("sources"))

        drift_log.append({
            "date": date,
            "action": "transition",
            "item_id": item_id,
            "detail": ", ".join(detail),
            "run_id": run_id,
        })
        changed = True
        log.info("publish: queue item %r transitioned (%s)", item_id, ", ".join(detail))

    if changed:
        _bump(queue)
        write_json(path, queue)
    return path, changed


def _union_sources(existing, incoming) -> list:
    """Merge `incoming` sources into `existing`, deduped by URL, existing first.

    The citation record only grows — a transition adds its receipt without ever
    dropping one already on file, so the item's accumulated evidence stays an
    honest superset and `transition_brings_new_evidence` keeps meaning what it says.
    """
    merged = list(existing or [])
    seen = {s.get("url") for s in merged if isinstance(s, dict)}
    for source in incoming or []:
        if isinstance(source, dict) and source.get("url") not in seen:
            merged.append(source)
            seen.add(source.get("url"))
    return merged


def _records_slip(snap: dict, from_window, to_window) -> bool:
    """True if the snapshot's slip_log records the from→to window transition."""
    for entry in snap.get("slip_log") or []:
        if isinstance(entry, dict) and _slip_from(entry) == from_window and _slip_to(entry) == to_window:
            return True
    return False


def _append_new_slips(current: dict, snap: dict, date: str) -> None:
    """Carry the snapshot's new slip_log entries into the state item, in state shape.

    The snapshot's slip_log ([07]: {from, to, date, source}) is translated to the
    state item_contract shape ({date, from_window, to_window, reason, source}) so
    the state file stays internally consistent. An entry already recorded (matched
    on the from/to window pair) is not duplicated — the log is append-only.
    """
    slip_log = current.setdefault("slip_log", [])
    seen = {(_slip_from(e), _slip_to(e)) for e in slip_log if isinstance(e, dict)}
    for entry in snap.get("slip_log") or []:
        if not isinstance(entry, dict):
            continue
        key = (_slip_from(entry), _slip_to(entry))
        if key in seen:
            continue
        slip_log.append({
            "date": entry.get("date") or date,
            "from_window": _slip_from(entry),
            "to_window": _slip_to(entry),
            "reason": entry.get("reason"),
            "source": entry.get("source"),
        })
        seen.add(key)


def _slip_from(entry: dict):
    return entry.get("from_window", entry.get("from"))


def _slip_to(entry: dict):
    return entry.get("to_window", entry.get("to"))


def _bump(state_file: dict) -> None:
    """Bump a state file's version and stamp it as a loop edit.

    Every machine write bumps the file's version and records last_edited_by:
    "loop", keeping loop edits distinguishable from owner edits ([03] clause 3) —
    the field an owner edit resets to distinguish theirs. The thesis carries the
    extra last_evolved_at; its caller stamps that after this, since it is thesis-
    specific and the other two files have no evolution timestamp.
    """
    state_file["version"] = (state_file.get("version") or 0) + 1
    state_file["last_edited_by"] = "loop"
