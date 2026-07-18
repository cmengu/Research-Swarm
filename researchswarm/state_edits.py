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

from researchswarm.findings import dossier_subject
from researchswarm.state import State
from researchswarm.validator import PROGRAM_RELATIONS, transition_brings_new_evidence

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


# ---------------------------------------------------------------------------
# The v2 state edits — the split layers (spec/03 "The layers, and why they split")
#
# Additive twins of the three v1 writers above, in the same idiom the pivot used
# for research/synthesis/critique/validation: run.py's v2 orchestration calls
# these, v1 calls those, and neither branches inside the other. The loop bodies
# for the thesis and the queue are near-duplicates of v1's ON PURPOSE — the
# thesis and queue SHAPES are unchanged (spec/03: "unchanged in shape from v1"),
# only their PATHS moved, and hoisting a shared core would mean editing v1
# functions that are scheduled for deletion as their own ticket. The duplication
# is bounded and dies with v1.
#
# What is genuinely new is the split the v1 watchlist did not have:
#   - the shared FACT about an entity  -> state/entities/<entity_id>.json
#   - what that fact means for THIS program (the read-through) -> the relation
#     edge in state/programs/<id>/edges.json
# ---------------------------------------------------------------------------

# The program-agnostic factual fields lifted from an issue entry into a shared
# entity record. Two exclusions are load-bearing, not oversights:
#
#   - `read_through` is NOT a fact. It is the per-program interpretation and it
#     belongs on the edge (spec/03: "facts lift to global ... read-throughs stay
#     per-program, which is where the detective's judgment lives"). Lifting it
#     would be exactly the silo-drift the split exists to kill, inverted.
#   - `priority` is a judgment RELATIVE to a program (HER3-DXd is high priority
#     to HMBD-001 and may be noise to a heme program), so it is not global either.
#
# ⚑ DERIVED, NOT SPECIFIED. Spec/03 fixes the RULES for `state/entities/`
# (one record per entity_id, program-agnostic, a materialized index over the
# append-only issue archive, every factual field citing the run that established
# it, corrections APPEND rather than overwrite) but publishes no field list and
# no sample — `state/entities/` is seeded empty and the roster migration is a
# deferred human curation session. This list is the narrowest set that satisfies
# the stated rules from the fields the v2 issue schema already carries; it is a
# compilable default, not a ruling. Widen it in one place when the curation
# session lands.
ENTITY_FACT_FIELDS_V2 = (
    "name",
    "type",
    "holders",
    "status",
    "summary",
    "categories",
    "failure",
    "sources",
)


def apply_state_edits_v2(
    root: Path,
    issue,
    *,
    program_id: str,
    entities: dict,
    edges,
    thesis: dict,
    catalyst_queue: dict,
    run_id: str,
    now: datetime,
) -> list[Path]:
    """Apply the run's state edits across the split v2 layers.

    Stage 6, step 4 (spec/09): "new/corrected entity facts -> `state/entities/`
    (append, cite the run); accepted promotions + retypes -> `state/programs/<id>/edges.json`
    + `drift_log`; thesis revisions -> `thesis.json` + `drift_log` + version bump;
    queue transitions -> `catalyst-queue.json` + `slip_log`."

    Order matches the spec's, and each writer is independent, cites the run_id,
    appends to its own file's log, and rewrites its file only when something
    actually changed — so a quiet cycle stages nothing and the diff is exactly the
    edit. Returns the paths that were rewritten: what the commit stages.

    **Interest proposals are deliberately NOT applied.** A `promotion_proposal`
    may carry a `proposes_interest`, and spec/03 clause 4 makes `config/interests.toml`
    one of the two surfaces that are never machine-written — the proposal is
    recorded as a finding in the published issue and the human confirms it in the
    editor. It is logged here so the proposal is visible in the run log too.
    """
    date = now.date().isoformat()
    touched: list[Path] = []

    for path, changed in [
        *_apply_entity_facts_v2(root, issue, entities, run_id, date),
        _apply_edges_v2(root, issue, program_id, edges, run_id, date),
        _apply_thesis_updates_v2(root, issue, thesis, run_id, date),
        _apply_queue_transitions_v2(root, issue, program_id, catalyst_queue, run_id, date),
    ]:
        if changed:
            touched.append(path)

    _log_interest_proposals_v2(issue)
    return touched


def apply_dossier_edits_v2(
    root: Path,
    findings_by_aperture,
    *,
    run_id: str,
    issue_id: str | None = None,
    now: datetime | None = None,
    date: str | None = None,
) -> list[Path]:
    """Persist every `dossier_scan` envelope's company dossier. Returns touched paths.

    **A SEPARATE ENTRY POINT, not a branch inside `apply_state_edits_v2` — three
    reasons, each of which would have to be argued away to merge them:**

      1. *Different input.* `apply_state_edits_v2` edits state from the PUBLISHED
         ISSUE. A dossier is not in the issue: it rides its own aperture envelope
         in the research corpus (`findings_by_aperture`), because a dossier scan
         answers "who is this company" and the manager never authors it. Merging
         would mean threading a second, unrelated corpus through a function whose
         whole signature says "the issue is the input".
      2. *Different scope.* Everything `apply_state_edits_v2` writes is
         program-scoped or program-derived; it takes a `program_id` and writes
         under `state/programs/<id>/`. A dossier is program-AGNOSTIC by
         construction (#92: "a dossier is shared across programs; a read-through
         is not"), so passing a program_id into this path would invite exactly
         the leak the interpretation ban exists to prevent.
      3. *Different failure posture.* The issue-derived edits are the cycle's
         product; a dossier is background gathering, and #92 is explicit that a
         failed dossier scan degrades rather than fails the run. Keeping it a
         separate call lets `run.py` order and guard it accordingly instead of
         burying a soft-failure boundary inside a function that has none.

    What it shares is the discipline, which is the part that matters ([03] and
    story 36 — `run.py` stays the sole writer): every write cites the run, every
    correction APPENDS a drift entry rather than overwriting, and a file is
    rewritten only when something actually changed, so a quiet refresh cycle
    stages nothing and the diff is exactly the edit.

    All merge and record logic lives in `researchswarm.dossiers`; this function
    is the state-edit-shaped shell over it. It re-implements no part of the
    record shape.

    TOTAL ON HOSTILE INPUT. `findings_by_aperture` may be None, a list, prose, or
    a dict of prose; an envelope may be anything; a `dossier` value may be a
    string or a list. Every one of those cases yields "nothing to write", never
    an exception. This is past the publish line: a crash here would take the run
    down *after* the issue reached disk, which is the bug this repo has shipped
    five times.

    Spec: docs/spec/03-state-and-governance.md, issue #92.
    """
    stamp = date or (now.date().isoformat() if isinstance(now, datetime) else None)
    if stamp is None:
        raise ValueError(
            "apply_dossier_edits_v2 needs `now` or `date`: state writers never read the clock"
        )

    touched: list[Path] = []
    for aperture_id, envelope in _dossier_envelopes_v2(findings_by_aperture):
        for path, changed in _apply_one_dossier_v2(
            root, aperture_id, envelope, run_id=run_id, issue_id=issue_id, date=stamp
        ):
            if changed:
                touched.append(path)
    return touched


def _dossier_envelopes_v2(findings_by_aperture):
    """`(aperture_id, envelope)` for every readable dossier_scan envelope.

    The filter is by APERTURE KIND, not by "does this payload happen to have a
    `dossier` key": a biology_scan that emitted a stray `dossier` key is a
    contract breach the findings gate refuses, and honouring it here would let a
    refused shape write to the shared store through the back door.

    Yields nothing — rather than raising — for a corpus that is null, a list, or
    a mapping of prose. See the totality note on the public function.
    """
    if not isinstance(findings_by_aperture, dict):
        return
    for aperture_id, envelope in findings_by_aperture.items():
        if dossier_subject(aperture_id) is None:
            continue
        if not isinstance(envelope, dict):
            log.warning(
                "publish: dossier envelope for %r is %s, not an object — skipped",
                aperture_id, type(envelope).__name__,
            )
            continue
        yield aperture_id, envelope


def _apply_one_dossier_v2(
    root: Path, aperture_id: str, envelope: dict, *, run_id: str, issue_id: str | None, date: str
):
    """Write one envelope's dossier and its asset->company links. `(path, changed)` pairs.

    Three writes, in the order a reader would want them:

      - the company dossier itself, merged against whatever we already hold;
      - one asset record per `pipeline[]` row, pointed at this company (story 31:
        traverse from a readout to its sponsor's balance sheet). The link is
        derived from the dossier the model just gave us, so it is written from the
        same evidence and in the same cycle, never inferred later.

    The subject comes from the APERTURE ID, not from the payload's `entity_id`.
    The gate already refuses a mismatch; taking the id from the aperture means
    that even if a payload slipped through claiming to be someone else, the write
    lands under the company we asked about — a dossier filed under the wrong
    company is worse than a missing one, because it looks like knowledge.

    A quiet scan (nothing found) and a scan that did not run are both simply
    absent writes here — the distinction between them lives in the envelope and
    the degradation register, not in the store (story 38).
    """
    # Imported HERE, not at module scope: `dossiers` imports `write_json` from
    # this module (it writes to the same formatting contract, and that contract
    # lives with the state writers). A top-level import back would close the
    # cycle and break `import researchswarm.state_edits`. The deferral keeps the
    # dependency pointing one way — state_edits is the orchestration shell over
    # dossiers, never the reverse.
    from researchswarm.dossiers import (
        apply_asset_company_link_v2,
        apply_company_dossier_v2,
        assets_of_company,
        load_asset_record,
        load_company_dossier,
    )

    entity_id = dossier_subject(aperture_id)
    payload = envelope.get("dossier")
    if not isinstance(payload, dict):
        if payload is not None:
            log.warning(
                "publish: %s dossier payload is %s, not an object — nothing written",
                entity_id, type(payload).__name__,
            )
        return []

    results = [
        apply_company_dossier_v2(
            root,
            entity_id,
            payload,
            existing=load_company_dossier(root, entity_id),
            run_id=run_id,
            issue_id=issue_id,
            date=date,
            as_of=payload.get("as_of"),
            degradation=_dossier_degradation_v2(envelope),
        )
    ]

    # Read the merged record back rather than mining the payload: `pipeline` is a
    # provenanced fact on the record, and the links we owe are the ones we NOW
    # HOLD, not the ones this one scan happened to mention. A refresh that stayed
    # silent on pipeline therefore still reconciles its known assets — as a no-op
    # if they are already linked, which is the point of `(path, changed)`.
    for asset_id in assets_of_company(load_company_dossier(root, entity_id)):
        results.append(
            apply_asset_company_link_v2(
                root,
                asset_id,
                entity_id,
                existing=load_asset_record(root, asset_id),
                run_id=run_id,
                issue_id=issue_id,
                date=date,
            )
        )
    return results


def _dossier_degradation_v2(envelope: dict) -> str | None:
    """The scan's own confession, carried onto the record's `coverage`.

    A dossier assembled from partial sources must say so AT THE POINT OF THE GAP
    (#92's China-coverage decision), and the scan is the only party that knows
    why it came back thin — a cap hit, an unreachable filings archive, a
    tool error. `thin_sections` says *where*; this says *why*, and without it a
    sparse HKEX dossier reads as a small company rather than as unmeasured
    coverage.

    Errors outrank coverage notes because an error explains an absence while a
    note merely annotates one. Anything that is not a list of strings degrades to
    None rather than raising: the receipt is worth having, but never at the price
    of the run.
    """
    for key in ("errors", "coverage_notes"):
        value = envelope.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = [item.strip() for item in value if isinstance(item, str) and item.strip()]
            if parts:
                return "; ".join(parts)
    return None


def _entity_entries_v2(issue):
    """Every issue entry that carries facts about an entity, newest layer first.

    Both `competitors[]` and `newly_discovered[]` describe entities: a discovery
    introduces one, a competitor updates one. The house view and the arenas
    reference entities but do not own their factual record, so they are not
    lifted — a house item is a wider-aperture observation, not a maintained fact
    sheet, and treating it as one would let an unrostered mention overwrite a
    tracked competitor's status.
    """
    for entry in issue.get("competitors") or []:
        if isinstance(entry, dict) and entry.get("entity_id"):
            yield entry
    for entry in issue.get("newly_discovered") or []:
        if isinstance(entry, dict) and entry.get("entity_id"):
            yield entry


def _apply_entity_facts_v2(root: Path, issue, entities: dict, run_id: str, date: str):
    """Materialize / correct the shared fact layer, one file per entity.

    `state/entities/<entity_id>.json` is "a materialized index over the append-only
    issue archive" (spec/03): it exists so a scan has cross-entity memory, and it
    cannot be allowed to drift from published truth. Two mechanics enforce that:

      - **every factual field cites the run that established it** — a fact is
        stored as `{value, established_by, issue}`, so any field can be traced back
        to the issue that published it;
      - **corrections append, never overwrite** — a changed value updates the fact
        AND appends a `drift_log` entry recording from/to and the run, so the prior
        value survives in the record itself as well as in git.

    No new immutability invariant is needed: append semantics plus issue-citation
    do the work (spec/03 clause 5).

    Yields `(path, changed)` per entity, so an unchanged entity stages nothing.
    """
    entities_dir = root / "state" / "entities"
    results = []
    issue_id = (issue.get("issue") or {}).get("id")

    for entry in _entity_entries_v2(issue):
        entity_id = entry["entity_id"]
        record = dict(entities.get(entity_id) or {})
        record.setdefault("entity_id", entity_id)
        record.setdefault("first_seen", date)
        facts = record.setdefault("facts", {})
        drift_log = record.setdefault("drift_log", [])
        changed = False

        for field in ENTITY_FACT_FIELDS_V2:
            if field not in entry:
                continue  # an absent field is silence, never a deletion
            value = entry[field]
            current = facts.get(field)
            if current is not None and current.get("value") == value:
                continue
            facts[field] = {
                "value": value,
                "established_by": run_id,
                "issue": issue_id,
            }
            drift_log.append({
                "date": date,
                "action": "established" if current is None else "corrected",
                "field": field,
                "from": (current or {}).get("value"),
                "to": value,
                "run_id": run_id,
            })
            changed = True

        path = entities_dir / f"{entity_id}.json"
        if changed:
            _bump(record)
            entities_dir.mkdir(parents=True, exist_ok=True)
            write_json(path, record)
            log.info("publish: entity %s facts updated (%d field(s))", entity_id, len(drift_log))
        results.append((path, changed))

    return results


def _apply_edges_v2(root: Path, issue, program_id: str, edges, run_id: str, date: str) -> tuple[Path, bool]:
    """Accepted promotions and retypes become relation edges, with a drift_log.

    An edge is `(program_id x entity_id) -> relation + read_through` (spec/03
    "Discovery, promotion, and the edge"): promoting a competitor onto a program
    IS writing this edge. Two inputs write one:

      - a `competitors[]` entry — the manager typed it this cycle, so the edge is
        created (a cold-start `seed_competitors` entity earning its first edge) or
        refreshed;
      - a `newly_discovered[]` entry whose `promotion_proposal.promote_to_competitors`
        is true — a discovery accepted onto the roster.

    A discovery whose proposal is false is NOT promoted: it stays a finding in the
    published issue. That is the whole point of a proposal.

    `relation` must be one of the four PROGRAM relations. `platform_threat` is
    company-unit and lives in the house view, never on a program's competitor list
    (spec/03 the competitor model), so an edge claiming it is refused LOUDLY rather
    than written — the validator already blocks it upstream, and a state writer that
    trusted the gate would be asymmetric defense-in-depth.

    A relation change is a **retype** and a read-through change is a **refine**;
    both append to the edge's own append-only `drift_log`, which is the only
    tamper-evidence a typing change has.
    """
    path = root / "state" / "programs" / program_id / "edges.json"
    payload = _load_edges_file(path, program_id)
    by_entity = {
        e.get("entity_id"): e for e in payload.get("edges", []) if isinstance(e, dict)
    }
    file_drift = payload.setdefault("drift_log", [])
    changed = False

    for entity_id, read_through in _promotions_v2(issue):
        relation = (read_through or {}).get("relation")
        if relation not in PROGRAM_RELATIONS:
            log.warning(
                "publish: edge for %s claims relation %r — only the four program "
                "relations may be typed onto a program (platform threat is house-level); "
                "refusing to write the edge",
                entity_id, relation,
            )
            continue

        edge = by_entity.get(entity_id)
        if edge is None:
            edge = {
                "entity_id": entity_id,
                "relation": relation,
                "read_through": read_through,
                "promoted_by": run_id,
                "drift_log": [
                    {"date": date, "action": "promoted", "to_relation": relation, "run_id": run_id}
                ],
            }
            payload.setdefault("edges", []).append(edge)
            by_entity[entity_id] = edge
            file_drift.append({
                "date": date, "action": "promoted", "entity_id": entity_id,
                "relation": relation, "run_id": run_id,
            })
            changed = True
            log.info("publish: promoted %s onto %s as %s", entity_id, program_id, relation)
            continue

        if edge.get("relation") != relation:
            edge.setdefault("drift_log", []).append({
                "date": date, "action": "retyped",
                "from_relation": edge.get("relation"), "to_relation": relation,
                "run_id": run_id,
            })
            file_drift.append({
                "date": date, "action": "retyped", "entity_id": entity_id,
                "from_relation": edge.get("relation"), "to_relation": relation,
                "run_id": run_id,
            })
            edge["relation"] = relation
            edge["read_through"] = read_through
            changed = True
            log.info("publish: retyped %s on %s → %s", entity_id, program_id, relation)
        elif edge.get("read_through") != read_through:
            edge.setdefault("drift_log", []).append({
                "date": date, "action": "refined", "relation": relation, "run_id": run_id,
            })
            edge["read_through"] = read_through
            changed = True
            log.info("publish: refined %s's read-through on %s", entity_id, program_id)

    if changed:
        _bump(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, payload)
    return path, changed


def _promotion_proposal(entry) -> dict:
    """Read `promotion_proposal` as a mapping, whatever the manager actually sent.

    The V2 call sites read this field through here — `_promotions_v2` and
    `_log_interest_proposals_v2`. (v1's `_apply_promotions` keeps its own inline
    guard: v1 is deleted whole and must not acquire a dependency on v2 code
    first.) The first live run (18 Jul 2026) proved why the v2 sites must not
    each call `.get()` on it directly: the manager emitted prose
    where [07] specifies an object, and the first site raised AttributeError
    *after* the issue was published — costing the run its git commit and leaving
    state half-applied. Fixing that one site would have left the other two armed,
    so the coercion lives here, once.

    The validator now BLOCKS the malformed shape upstream
    (`malformed_promotion_proposal`), which is where the manager gets told it was
    wrong. This is the downstream half of that pair: past the publish line a
    shape surprise must degrade to "nothing proposed", never take the run down.
    """
    proposal = entry.get("promotion_proposal") if isinstance(entry, dict) else None
    return proposal if isinstance(proposal, dict) else {}


def _promotions_v2(issue):
    """`(entity_id, read_through)` for every entity this issue types onto the program."""
    for entry in issue.get("competitors") or []:
        if isinstance(entry, dict) and entry.get("entity_id"):
            yield entry["entity_id"], entry.get("read_through") or {}
    for entry in issue.get("newly_discovered") or []:
        if not isinstance(entry, dict) or not entry.get("entity_id"):
            continue
        if _promotion_proposal(entry).get("promote_to_competitors"):
            yield entry["entity_id"], entry.get("read_through") or {}


def _log_interest_proposals_v2(issue) -> None:
    """Log every `proposes_interest` — recorded as a finding, never written.

    Spec/03 clause 4 and spec/09 stage 6: "Interest proposals are recorded as
    findings, never written to `interests.toml` — the human confirms them in the
    editor." The proposal already rides in the published issue; this line makes it
    visible to whoever is reading the run log, and makes the refusal explicit at
    the one place a naive implementation would have written the file.
    """
    for entry in issue.get("newly_discovered") or []:
        if not isinstance(entry, dict):
            continue
        proposed = _promotion_proposal(entry).get("proposes_interest")
        if proposed:
            log.info(
                "publish: interest proposed (%s) for %s — recorded as a finding, "
                "NOT written: config/interests.toml is human-owned",
                proposed.get("tier"), entry.get("entity_id"),
            )


def _apply_thesis_updates_v2(root: Path, issue, thesis: dict, run_id: str, date: str) -> tuple[Path, bool]:
    """Thesis revisions, against the v2 state layer. Shape unchanged from v1.

    `state/thesis.json` did NOT move in the pivot — it is the shared worldview,
    program-agnostic, and spec/03 records it as "unchanged in shape from v1". So
    the rules are v1's, defended identically: the loop may revise an ACTIVE belief
    and must log every revision; it may never author a stance into a DORMANT slot
    (an unowned opinion), and never null an active stance back to dormancy (an
    owner-only transition). Both refusals are LOUD, not silent.

    The only difference from `_apply_thesis_updates` is the input: the raw v2
    thesis dict rather than a flat `State`.
    """
    path = root / "state" / "thesis.json"
    beliefs = {b.get("id"): b for b in thesis.get("beliefs", []) if isinstance(b, dict)}
    changed = False

    for update in issue.get("thesis_updates") or []:
        slot_id = (update or {}).get("field")
        belief = beliefs.get(slot_id)
        if belief is None:
            log.warning("publish: thesis_update targets unknown slot %r — skipped", slot_id)
            continue
        if belief.get("stance") is None:
            log.warning(
                "publish: thesis_update targets DORMANT slot %r (null stance) — "
                "refusing to author a stance the owner never seeded",
                slot_id,
            )
            continue

        to_stance = update.get("after")
        if not to_stance:
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
            "cycle_id": run_id,
        })
        changed = True
        log.info("publish: thesis slot %r revised by the loop", slot_id)

    if changed:
        _bump(thesis)
        thesis["last_evolved_at"] = date
        write_json(path, thesis)
    return path, changed


def _apply_queue_transitions_v2(
    root: Path, issue, program_id: str, queue: dict, run_id: str, date: str
) -> tuple[Path, bool]:
    """Queue transitions, against `state/programs/<id>/catalyst-queue.json`.

    The queue went per-program in the pivot but "the shape and the accountability
    invariant are unchanged" (spec/03), so this is v1's rule set applied to the v2
    path: no source, no transition; `first_expected_window` is never propagated;
    an `expected_window` change is refused unless the snapshot's `slip_log` records
    exactly that from->to. Add and retire remain the monthly re-cut's, never a
    run's.
    """
    path = root / "state" / "programs" / program_id / "catalyst-queue.json"
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


def _load_edges_file(path: Path, program_id: str) -> dict:
    """The edges file as a mutable payload, or a fresh one for a new program.

    `programs.load_edges` returns typed `Edge` objects for READING; this writer
    needs the file's whole payload (its version, its file-level drift_log, any
    fields a future ticket adds) so a rewrite preserves everything it did not
    touch rather than reconstructing the file from the fields this module knows.
    """
    if path.exists():
        return json.loads(path.read_text())
    return {"program_id": program_id, "edges": [], "drift_log": []}


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
        # v1's OWN inline guard, deliberately not the v2-era `_promotion_proposal`
        # helper. v1 is deleted whole as its own ticket, and the discipline this
        # branch keeps everywhere else is that v1 is never modified and — the part
        # that was broken here — never acquires a dependency on v2 code first. A
        # v1 function calling a v2 helper is a deletion that stops being a clean
        # excision, so the shape coercion is spelled out locally instead.
        raw = entry.get("promotion_proposal") if isinstance(entry, dict) else None
        proposal = raw if isinstance(raw, dict) else {}
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
