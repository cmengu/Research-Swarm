"""Stage 1 — the deterministic validator, and the degradation register.

The free gate. Every structural check a manager's draft must pass before a
single token of critic budget is spent on it. Structural checks are decidable
by a script with perfect accuracy, in milliseconds, for free — and a model
asked to count fields will miss an empty section *inconsistently*, which is
worse than not checking. So the gate is a script, not a model.

**This module owns BOTH the register and its enforcer, on purpose.** The
degradation register is the single home for the declarations that let an empty
section publish instead of block; the validator's `empty_section` check is what
reads it. The spec insists the list and the thing enforcing the list cannot
drift apart ([06](docs/spec/06-validator-and-critic.md#the-degradation-register)),
and in code that means one module, not two.

What the register is NOT: a place a model self-report can reach. A degradation
earns an exemption only when the system can detect its cause MECHANICALLY, from
facts the orchestrator holds itself. "Did the researcher remember to confess in
errors[]" is not such a fact — it fails silently on exactly the run where it
matters. So a degradation object whose `kind` is not in the register, or whose
mechanical trigger is not true, grants no exemption, and an empty section
explained only by a researcher self-report BLOCKS. "We don't know why this is
empty" is precisely when blocking is right.

Spec: docs/spec/06-validator-and-critic.md, docs/spec/07-issue-schema.md
"""

from __future__ import annotations

from dataclasses import dataclass

from researchswarm.findings import SOURCE_FIELDS, SOURCE_TIERS

# ---------------------------------------------------------------------------
# The finding shape and the result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One thing the validator caught. Mirrors the {kind, where, note} shape the
    schema puts in critic_report.validator_report.findings ([07])."""

    kind: str
    where: str
    note: str


@dataclass(frozen=True)
class ValidationResult:
    """The gate's verdict. Blocking and advisory are kept SEPARATE because they
    are acted on differently: blocking findings drive the retry loop and, on
    exhaustion, a stub; advisories only publish in the record and never gate.

    Every check runs and every problem is collected before this is returned —
    the same all-problems-at-once contract the other seam validators keep, so a
    single retry can fix everything rather than peeling an onion.
    """

    blocking: tuple[Finding, ...] = ()
    advisory: tuple[Finding, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.blocking


# ---------------------------------------------------------------------------
# The degradation register — kind → its mechanical trigger
# ---------------------------------------------------------------------------
#
# An empty required section blocks UNLESS a declared degradation explains it,
# and a declaration explains it only if its `kind` is registered here AND this
# trigger returns True. Each trigger is decidable from facts the orchestrator
# already holds — the issue, the state, and the list of beats that actually
# failed this cycle. Nothing here consults a model self-report.
#
# Every trigger takes (degradation, *, issue, state, beats_failed) and returns
# a bool. `degradation` is the declared object (it may carry scoping fields such
# as `beats`); the rest are the mechanical facts.


def _trigger_thesis_unseeded(degradation, *, issue, state, beats_failed) -> bool:
    """A belief slot's stance is null in state/thesis.json.

    Scoped in spec to that slot's angle, not every empty angle. The declared
    object does not name a slot, so the mechanical fact this certifies is only
    that a dormant slot EXISTS — finer per-slot gating is the manager's job at
    authoring time and the critic's at judgment time. The register's duty is to
    confirm the condition is real, and a null stance is real or it is not.
    """
    return any(
        belief.get("stance") is None for belief in state.thesis.get("beliefs", [])
    )


def _trigger_beat_failed(degradation, *, issue, state, beats_failed) -> bool:
    """The beat(s) the degradation names are actually in beats_failed.

    A degradation may name the beats it covers via a `beats` list; each named
    beat must be in beats_failed for the exemption to hold — a declaration
    naming a beat that ran is not evidence of an absence. A declaration that
    names none is honoured only when SOME beat failed this cycle; an exemption
    that points at nothing is not mechanical.
    """
    failed = set(beats_failed or ())
    if not failed:
        return False
    named = degradation.get("beats")
    if named:
        return set(named) <= failed
    return True


def _trigger_quiet_cycle(degradation, *, issue, state, beats_failed) -> bool:
    """Every tracked entity appears in quiet_this_cycle.no_news.

    Decidable from the issue alone: if the whole roster is quiet, an empty
    watchlist is the honest render, not a defect. Requires the roster to be
    non-empty — an empty roster is a state problem, not a quiet week.
    """
    tracked = state.entity_ids
    if not tracked:
        return False
    quiet = {
        entry.get("entity_id")
        for entry in issue.get("quiet_this_cycle", {}).get("no_news", [])
    }
    return tracked <= quiet


def _trigger_calendar_stale(degradation, *, issue, state, beats_failed) -> bool:
    """No conference window verified in N cycles — surge disabled.

    Registered so the enforcer and the register list stay in lockstep, but its
    trigger cannot fire yet: surge/calendar verification lands in build 10, and
    until `verified_at` exists there is no mechanical fact to read. Returning
    False means it grants no exemption today — an honest "not yet" rather than a
    silent always-true that would exempt anything claiming staleness.
    """
    return False  # build 10 — no calendar verification exists to read yet


DEGRADATION_REGISTER = {
    "thesis_unseeded": _trigger_thesis_unseeded,
    "beat_failed": _trigger_beat_failed,
    "quiet_cycle": _trigger_quiet_cycle,
    "calendar_stale": _trigger_calendar_stale,
}


# ---------------------------------------------------------------------------
# Required sections — what must be present, and why each is required
# ---------------------------------------------------------------------------
#
# A required section that is empty BLOCKS unless a scoped degradation explains
# it. The sections NOT listed here may be honestly empty — a quiet week is not a
# defect, and forcing the manager to invent radar items or themes to fill them
# would manufacture exactly the false signal the whole system exists to avoid.
# So new_on_radar, themes_and_signals, elsewhere_on_frontier and thesis_updates
# are deliberately absent: their emptiness is information, not a bug.

REQUIRED_SECTIONS = (
    # The cycle's biggest story. An issue with no headline is not an issue.
    "headline",
    # One bullet per main topic — the skim layer a busy reader lands on first.
    "tldr_bullets",
    # The entities with news. Empty only under a true quiet_cycle (whole roster
    # quiet) or a beat_failed covering the beats that would have fed it.
    "watchlist",
    # The accounting half: no_news + critic_catches + open_threads. Its three
    # keys are the contract that every tracked entity is accounted for.
    "quiet_this_cycle",
    # The audit trail — beats_run, beats_failed, tier counts. Never optional.
    "sources_and_method",
)

QUIET_THIS_CYCLE_KEYS = ("no_news", "critic_catches", "open_threads")


# ---------------------------------------------------------------------------
# Source walking — one traversal, reused by malformed_source and stats
# ---------------------------------------------------------------------------
#
# Sources ride in many places. malformed_source must check every one; the
# derived-stats count must total every one. So the traversal lives once, yields
# (source_object, where) pairs, and both checks consume it. The issue-side
# source object requires the four CORE fields (url, publisher, tier,
# published_at) — NOT the `paywalled` boolean, which is the findings.json
# contract's extra ([07] "all four core fields").


def _iter_sources(issue):
    """Yield (source, where) for every source object anywhere in the issue."""
    headline = issue.get("headline")
    if isinstance(headline, dict):
        yield from _sources_of(headline, "headline")

    for section in ("watchlist", "new_on_radar", "elsewhere_on_frontier"):
        for i, entry in enumerate(issue.get(section) or []):
            if isinstance(entry, dict):
                yield from _sources_of(entry, f"{section}[{_ref(entry, i)}]")

    for i, catch in enumerate(
        issue.get("quiet_this_cycle", {}).get("critic_catches") or []
    ):
        if isinstance(catch, dict):
            yield from _sources_of(catch, f"quiet_this_cycle.critic_catches[{i}]")

    for i, item in enumerate(issue.get("catalyst_queue", {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        where = f"catalyst_queue.items[{item.get('id', i)}]"
        yield from _sources_of(item, where)
        window_source = item.get("window_source")
        if window_source is not None:
            yield window_source, f"{where}.window_source"
        for j, slip in enumerate(item.get("slip_log") or []):
            if isinstance(slip, dict) and slip.get("source") is not None:
                yield slip["source"], f"{where}.slip_log[{j}].source"

    for i, finding in enumerate(
        issue.get("critic_report", {}).get("blocking_findings") or []
    ):
        # The receipt on a dropped_story. Present only when the critic ran, but
        # if it is there it is a source object and must be well-formed too.
        if isinstance(finding, dict) and finding.get("source") is not None:
            yield finding["source"], f"critic_report.blocking_findings[{i}].source"


def _sources_of(obj, where):
    for i, source in enumerate(obj.get("sources") or []):
        yield source, f"{where}.sources[{i}]"


def _ref(entry, index):
    """A stable label for an entry — its entity_id if it has one, else index."""
    return entry.get("entity_id") or index


# ---------------------------------------------------------------------------
# The seven checks — each its own function, each collecting into `problems`
# ---------------------------------------------------------------------------


def _check_uncited_claim(issue, problems):
    """A content-bearing object that must carry sources carries at least one.

    The mechanical version of the check: true claim-detection (is THIS sentence
    an assertion that needs a source?) is the critic's judgment. The free gate
    checks only that every object obliged to carry sources[] carries a non-empty
    one — headline, and each entry in the sourced sections.
    """
    headline = issue.get("headline")
    if isinstance(headline, dict) and headline and not _has_source(headline):
        problems.append(Finding("uncited_claim", "headline", "no sources[]"))

    for section in ("watchlist", "new_on_radar", "elsewhere_on_frontier"):
        for i, entry in enumerate(issue.get(section) or []):
            if isinstance(entry, dict) and entry and not _has_source(entry):
                problems.append(
                    Finding("uncited_claim", f"{section}[{_ref(entry, i)}]", "no sources[]")
                )

    for i, catch in enumerate(
        issue.get("quiet_this_cycle", {}).get("critic_catches") or []
    ):
        if isinstance(catch, dict) and catch and not _has_source(catch):
            problems.append(
                Finding(
                    "uncited_claim",
                    f"quiet_this_cycle.critic_catches[{i}]",
                    "no sources[]",
                )
            )


def _has_source(obj):
    sources = obj.get("sources")
    return isinstance(sources, list) and len(sources) > 0


def _check_malformed_source(issue, problems):
    """Every source object anywhere carries the four core fields, tier valid.

    Reuses findings.py's SOURCE_FIELDS / SOURCE_TIERS so the definition of a
    well-formed source lives in one place. The issue side does not require the
    `paywalled` boolean — that is findings.json's extra.
    """
    for source, where in _iter_sources(issue):
        if not isinstance(source, dict):
            problems.append(
                Finding("malformed_source", where, "source must be an object, not a string")
            )
            continue
        for field_name in SOURCE_FIELDS:
            if not source.get(field_name):
                problems.append(
                    Finding("malformed_source", where, f"missing required field {field_name!r}")
                )
        tier = source.get("tier")
        if tier and tier not in SOURCE_TIERS:
            problems.append(
                Finding("malformed_source", where, f"tier {tier!r} not in {sorted(SOURCE_TIERS)}")
            )


def _check_dangling_entity(issue, state, problems):
    """Every entity reference resolves to a tracked entity or a new_on_radar slug.

    The cross-file join check on the spine. The allowed set is the state
    watchlist's entity_ids UNION the entity_ids this issue declares in
    new_on_radar — a new_on_radar entry IS the proposal, so its own slug is
    self-introducing. Anything else dangling blocks.
    """
    self_introduced = {
        entry.get("entity_id")
        for entry in issue.get("new_on_radar") or []
        if isinstance(entry, dict) and entry.get("entity_id")
    }
    known = state.entity_ids | self_introduced

    for ref, where in _iter_entity_refs(issue):
        if ref not in known:
            problems.append(Finding("dangling_entity", where, f"entity_id {ref!r} resolves to nothing"))


def _iter_entity_refs(issue):
    """Yield (entity_id, where) for every entity reference in the issue."""
    headline = issue.get("headline")
    if isinstance(headline, dict):
        for ref in headline.get("entity_refs") or []:
            yield ref, "headline.entity_refs"

    for i, bullet in enumerate(issue.get("tldr_bullets") or []):
        if isinstance(bullet, dict):
            for ref in bullet.get("entity_refs") or []:
                yield ref, f"tldr_bullets[{i}].entity_refs"

    for i, entry in enumerate(issue.get("watchlist") or []):
        if isinstance(entry, dict) and entry.get("entity_id"):
            yield entry["entity_id"], f"watchlist[{_ref(entry, i)}].entity_id"

    quiet = issue.get("quiet_this_cycle", {})
    for i, entry in enumerate(quiet.get("no_news") or []):
        if isinstance(entry, dict) and entry.get("entity_id"):
            yield entry["entity_id"], f"quiet_this_cycle.no_news[{i}].entity_id"
    for i, thread in enumerate(quiet.get("open_threads") or []):
        if isinstance(thread, dict) and thread.get("entity_id"):
            yield thread["entity_id"], f"quiet_this_cycle.open_threads[{i}].entity_id"

    for i, theme in enumerate(issue.get("themes_and_signals") or []):
        if isinstance(theme, dict):
            for ref in theme.get("evidence_refs") or []:
                yield ref, f"themes_and_signals[{i}].evidence_refs"

    for i, update in enumerate(issue.get("thesis_updates") or []):
        if isinstance(update, dict):
            for ref in update.get("triggered_by") or []:
                yield ref, f"thesis_updates[{i}].triggered_by"

    for i, item in enumerate(issue.get("catalyst_queue", {}).get("items") or []):
        if isinstance(item, dict):
            for ref in item.get("entity_ids") or []:
                yield ref, f"catalyst_queue.items[{item.get('id', i)}].entity_ids"


def _check_unaccounted_watchlist_entity(issue, state, problems):
    """Every tracked entity appears in watchlist or no_news — and in exactly one.

    The manager's coverage duty: each tracked entity is covered (it had news) or
    explicitly quiet, every cycle. An entity in NEITHER is unaccounted; an entity
    in BOTH is double-accounted (the spec says exactly one). Both block.
    """
    in_watchlist = {
        e.get("entity_id")
        for e in issue.get("watchlist") or []
        if isinstance(e, dict) and e.get("entity_id")
    }
    in_quiet = {
        e.get("entity_id")
        for e in issue.get("quiet_this_cycle", {}).get("no_news") or []
        if isinstance(e, dict) and e.get("entity_id")
    }

    for entity_id in sorted(state.entity_ids):
        here, there = entity_id in in_watchlist, entity_id in in_quiet
        if here and there:
            problems.append(
                Finding(
                    "unaccounted_watchlist_entity",
                    entity_id,
                    "double-accounted — in both watchlist and quiet_this_cycle (exactly one is required)",
                )
            )
        elif not here and not there:
            problems.append(
                Finding(
                    "unaccounted_watchlist_entity",
                    entity_id,
                    "in neither watchlist nor quiet_this_cycle",
                )
            )


def _check_empty_section(issue, state, beats_failed, problems):
    """A required section is populated, or a scoped degradation explains it.

    The exemption is SCOPED: a degradation explains only the section it sits in.
    An undeclared empty section blocks; so does one whose only explanation is a
    degradation that is not registered, or whose trigger is not mechanically
    true, or a researcher's self-report (which is not a trigger at all).
    """
    for section in REQUIRED_SECTIONS:
        if not _section_empty(issue, section):
            continue
        if _empty_section_exempt(section, issue, state, beats_failed):
            continue
        problems.append(
            Finding("empty_section", section, "required section is empty and no registered degradation explains it")
        )


def _section_empty(issue, section):
    value = issue.get(section)
    if section == "quiet_this_cycle":
        # Not "empty" so much as "well-formed": the three keys are the accounting
        # contract, and a quiet_this_cycle missing one cannot be reasoned about.
        return not isinstance(value, dict) or any(
            key not in value for key in QUIET_THIS_CYCLE_KEYS
        )
    if section in ("headline", "sources_and_method"):
        return not value  # None or {} — a non-null object is required
    return not value  # tldr_bullets, watchlist — a non-empty list is required


def _empty_section_exempt(section, issue, state, beats_failed):
    """True if a registered, mechanically-true degradation explains this absence.

    Two routes, both scoped to the section:

    1. A fully-mechanical trigger that needs no declaration. `watchlist` may be
       empty when every tracked entity is quiet — quiet_cycle is decidable from
       the issue alone, so requiring the manager to also declare an object for
       the ordinary quiet week would be ceremony. This is the ONLY implicit
       route; it exists because the fact is global and self-evident.
    2. A declared degradation object sitting AT the absence — read from
       issue["degradations"][section], the home for a marker on a section that
       has no entry to carry one — whose kind is registered and whose trigger is
       true. (Entry-level degradation markers, on populated sections, are the
       manager/critic's concern; this validator's empty-section duty is the
       absence of a whole section.)
    """
    if section == "watchlist" and _trigger_quiet_cycle(
        {}, issue=issue, state=state, beats_failed=beats_failed
    ):
        return True

    for degradation in _section_degradations(issue, section):
        trigger = DEGRADATION_REGISTER.get(degradation.get("kind"))
        if trigger and trigger(
            degradation, issue=issue, state=state, beats_failed=beats_failed
        ):
            return True
    return False


def _section_degradations(issue, section):
    """Degradation objects declared for a whole-section absence.

    An empty array section has no entry to hang a `degradation` on, so a
    section-level absence is declared in an optional `degradations` map,
    section-name → [degradation objects]. The map is the validator's reading
    convention for the case the schema's entry-level `degradation` cannot cover;
    manager-side emission of it is a later wire-up, and the common empty-watchlist
    case (a quiet week) is already handled mechanically above without it.
    """
    declared = issue.get("degradations")
    if not isinstance(declared, dict):
        return []
    return [d for d in (declared.get(section) or []) if isinstance(d, dict)]


def _check_derived_stats_mismatch(issue, problems):
    """`stats` agrees with the arrays it summarizes, or it is an empty draft.

    stats == {} is a draft the orchestrator has not derived yet (build 06) — the
    manager is forbidden to author it, so an empty stats is expected here and is
    skipped SILENTLY. Any other stats is recomputed from the arrays and must
    agree exactly; a disagreement is a bug in the derivation, not an edit.
    """
    stats = issue.get("stats")
    if stats == {}:
        return  # a draft — the orchestrator derives stats in build 06
    if not isinstance(stats, dict):
        problems.append(Finding("derived_stats_mismatch", "stats", "stats must be an object"))
        return

    expected = {
        "tracked_updates": len(issue.get("watchlist") or []),
        "tracked_quiet": len(issue.get("quiet_this_cycle", {}).get("no_news") or []),
        "new_on_radar": len(issue.get("new_on_radar") or []),
        "frontier_items": len(issue.get("elsewhere_on_frontier") or []),
        "sources_cited": sum(1 for _ in _iter_sources(issue)),
        "critic_catches": len(issue.get("quiet_this_cycle", {}).get("critic_catches") or []),
    }
    for key, want in expected.items():
        if stats.get(key) != want:
            problems.append(
                Finding("derived_stats_mismatch", f"stats.{key}", f"stats says {stats.get(key)!r}, arrays say {want}")
            )


def _check_queue_tamper(issue, queue_baseline, problems):
    """The catalyst queue's tamper-evidence rule — the whole point of the queue.

    Compares each item against the most recent snapshot CARRYING the field (the
    baseline the continuity walk found, never the positionally-previous issue —
    a single failed run must not launder the invariant). For an item present in
    both baseline and this issue it blocks on:

      - first_expected_window changed — the value is immutable after creation;
      - expected_window changed with no NEW slip_log entry recording the exact
        from→to transition;
      - status changed with no source on the item or its newest slip_log entry.

    An item new to this issue has no baseline to compare, so it is checked only
    for internal coherence: first_expected_window == expected_window unless a
    slip_log entry already explains the difference (a queue may be seeded
    mid-slip, and the log is where that is made honest).
    """
    baseline_items = {}
    for item in (queue_baseline or {}).get("items") or []:
        if isinstance(item, dict) and item.get("id"):
            baseline_items[item["id"]] = item

    for item in issue.get("catalyst_queue", {}).get("items") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        where = f"catalyst_queue.items[{item.get('id')}]"
        prior = baseline_items.get(item["id"])

        if prior is None:
            _check_new_queue_item(item, where, problems)
            continue

        if item.get("first_expected_window") != prior.get("first_expected_window"):
            problems.append(
                Finding(
                    "queue_tamper",
                    where,
                    "first_expected_window changed — it is immutable after creation "
                    f"({prior.get('first_expected_window')!r} → {item.get('first_expected_window')!r})",
                )
            )

        if item.get("expected_window") != prior.get("expected_window"):
            if not _slip_records(item, prior.get("expected_window"), item.get("expected_window")):
                problems.append(
                    Finding(
                        "queue_tamper",
                        where,
                        "expected_window changed with no new slip_log entry recording "
                        f"{prior.get('expected_window')!r} → {item.get('expected_window')!r}",
                    )
                )

        if item.get("status") != prior.get("status") and not _transition_sourced(item):
            problems.append(
                Finding(
                    "queue_tamper",
                    where,
                    f"status changed {prior.get('status')!r} → {item.get('status')!r} "
                    "with no source on the item or its newest slip_log entry",
                )
            )


def _check_new_queue_item(item, where, problems):
    if item.get("first_expected_window") == item.get("expected_window"):
        return
    if _slip_records(item, item.get("first_expected_window"), item.get("expected_window")):
        return
    problems.append(
        Finding(
            "queue_tamper",
            where,
            "new item's expected_window differs from first_expected_window with no "
            "slip_log entry explaining the difference",
        )
    )


def _slip_records(item, from_window, to_window):
    """A slip_log entry records the from→to transition."""
    for entry in item.get("slip_log") or []:
        if isinstance(entry, dict) and entry.get("from") == from_window and entry.get("to") == to_window:
            return True
    return False


def _transition_sourced(item):
    """The item, or its newest slip_log entry, carries a source for a status move."""
    if item.get("sources") or item.get("window_source"):
        return True
    slip_log = item.get("slip_log") or []
    if slip_log and isinstance(slip_log[-1], dict):
        return bool(slip_log[-1].get("source"))
    return False


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def validate_issue(
    issue,
    *,
    state,
    queue_baseline=None,
    baseline_expired=False,
    beats_failed=None,
):
    """Run all seven checks; return a ValidationResult, never raise.

    Deterministic and free — no model, no web, no IO. The continuity walk that
    finds `queue_baseline` (and whether it hit the floor) belongs to the caller,
    so this stays a pure function of the draft plus the facts handed to it.

    `beats_failed` defaults to the issue's own audit trail when not supplied, so
    the validator can be exercised standalone; the stage passes the research
    stage's authoritative list.

    A `queue_baseline` of None means the continuity walk found nothing — run #1,
    or nothing but stubs. That is TOLERATED: queue_tamper simply has no baseline
    to compare against and skips, exactly as the coverage window does. Only if
    the walk hit the 12-issue floor is `continuity_baseline_expired` filed as an
    ADVISORY — rendered in the report, never blocking.
    """
    if beats_failed is None:
        beats_failed = issue.get("sources_and_method", {}).get("beats_failed") or []

    blocking: list[Finding] = []

    _check_uncited_claim(issue, blocking)
    _check_malformed_source(issue, blocking)
    _check_dangling_entity(issue, state, blocking)
    _check_unaccounted_watchlist_entity(issue, state, blocking)
    _check_empty_section(issue, state, beats_failed, blocking)
    _check_derived_stats_mismatch(issue, blocking)
    _check_queue_tamper(issue, queue_baseline, blocking)

    advisory: list[Finding] = []
    if baseline_expired:
        advisory.append(
            Finding(
                "continuity_baseline_expired",
                "catalyst_queue",
                "backwards search hit the 12-issue floor without a snapshot to compare against",
            )
        )

    return ValidationResult(blocking=tuple(blocking), advisory=tuple(advisory))
