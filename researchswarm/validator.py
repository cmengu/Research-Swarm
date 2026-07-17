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
    """A beat actually failed this cycle (it is in sources_and_method.beats_failed).

    The schema's entry-level degradation object is {kind, marker} — it names no
    beat, so the mechanical fact this certifies is that SOME beat failed. A
    beat_failed marker on a cycle where every beat ran is a claimed absence the
    orchestrator can prove false, and it blocks. (A degradation that DOES name
    beats via an optional `beats` list is held to the stronger bar — each named
    beat must be in beats_failed — so the field is honoured if it ever appears.)
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
    """A stale calendar empties no REQUIRED section, so it exempts none.

    calendar_stale is a real degradation (spec/06 register) but a different KIND
    from the other three: it disables surge and prints a marker on every issue,
    yet nothing in the issue's required sections goes empty because of it. This
    register maps kinds to section-emptying triggers, so calendar_stale correctly
    grants no section exemption — it is filed as an advisory by run.py's Stage 1
    (from the mechanical `calendar.stale_reason`), not certified here. Kept in the
    register so the enforcer and the register list stay in lockstep, and returns
    False so a manager could never launder an unexplained empty section behind a
    calendar_stale marker.
    """
    return False  # stale calendar disables surge; it empties no required section


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


def _iter_sourced_objects(issue):
    """Yield (obj, where) for every content object obliged to carry sources[].

    The five content sections uncited_claim guards, and where malformed_source
    finds most of its sources. The catalyst queue's items and the singleton
    source objects (window_source, slip_log entries, dropped_story receipts) are
    walked separately in _iter_sources — they are not content objects a reader
    acts on, so uncited_claim does not touch them.
    """
    headline = issue.get("headline")
    if isinstance(headline, dict) and headline:
        yield headline, "headline"

    for section in ("watchlist", "new_on_radar", "elsewhere_on_frontier"):
        for i, entry in enumerate(issue.get(section) or []):
            if isinstance(entry, dict):
                yield entry, f"{section}[{_ref(entry, i)}]"

    for i, catch in enumerate(
        issue.get("quiet_this_cycle", {}).get("critic_catches") or []
    ):
        if isinstance(catch, dict):
            yield catch, f"quiet_this_cycle.critic_catches[{i}]"


def _iter_sources(issue):
    """Yield (source, where) for every source object anywhere in the issue.

    Reuses the one content-object walk, then adds the queue's own sources and the
    three singleton source objects the content walk deliberately excludes.
    """
    for obj, where in _iter_sourced_objects(issue):
        yield from _sources_of(obj, where)

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
    for obj, where in _iter_sourced_objects(issue):
        if obj and not _has_source(obj):
            problems.append(Finding("uncited_claim", where, "no sources[]"))


def _has_source(obj):
    sources = obj.get("sources")
    return isinstance(sources, list) and len(sources) > 0


def _check_malformed_source(issue, problems):
    """Every source object anywhere carries the four core fields, tier valid.

    Reuses findings.py's SOURCE_FIELDS / SOURCE_TIERS so the definition of a
    well-formed source lives in one place. The issue side does not require the
    `paywalled` boolean — that is findings.json's extra.

    Twin of findings.py's `_check_source`, deliberately kept separate: that one
    layers the `paywalled` boolean check on top (findings.json always sets it;
    the issue side never carries it), and sharing the loop would force one caller
    to thread a "require paywalled?" flag through the other's contract. The
    shared part — the four core fields and the tier enum — lives in the two
    constants both import, which is the drift that actually mattered.
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


# The section arrays that may carry entry-level `degradation` objects ([07]:
# the marker renders at the point of the absence, on the affected entry). An
# entry keeps its section non-empty AND carries its own explanation, so there is
# no separate section-level declaration — and no 15th top-level key.
DEGRADABLE_SECTIONS = (
    "watchlist", "new_on_radar", "themes_and_signals", "elsewhere_on_frontier",
    "tldr_bullets",
)


def _check_empty_section(issue, state, beats_failed, problems):
    """A required section is populated, and every degradation it carries is real.

    Two duties, both filed under `empty_section` because both are the degradation
    register doing its job:

    1. A truly-empty required section (its array is `[]`, or its object is null /
       malformed) blocks — UNLESS the one implicit mechanical route applies:
       `watchlist` may be empty when every tracked entity sits in
       quiet_this_cycle.no_news (quiet_cycle, decidable from the issue alone). An
       entry carrying a degradation keeps its section NON-empty, so a dead beat
       is never an empty section — it is a marked one.

    2. Every entry-level `degradation` object found in the section arrays must be
       REAL: its `kind` registered, its trigger mechanically true. This is where
       "a degradation declared anywhere else does not exist" bites — a marker
       claiming a failure the orchestrator cannot confirm (an unregistered kind,
       or beat_failed when no beat failed) is the exact self-report the register
       exists to reject, so it blocks.
    """
    for section in REQUIRED_SECTIONS:
        if not _section_empty(issue, section):
            continue
        if section == "watchlist" and _trigger_quiet_cycle(
            {}, issue=issue, state=state, beats_failed=beats_failed
        ):
            continue  # the whole roster is quiet — an empty watchlist is honest
        problems.append(
            Finding("empty_section", section, "required section is empty and no registered degradation explains it")
        )

    _check_degradation_validity(issue, state, beats_failed, problems)


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


def _check_degradation_validity(issue, state, beats_failed, problems):
    """Every entry-level degradation object earns its exemption, or blocks.

    A degradation object explains an absence its entry makes non-empty. That
    explanation is only worth anything if the system can confirm it mechanically
    — the register's third admission test. So each marker is checked against the
    register: an unregistered kind grants nothing, and a registered kind whose
    trigger is false (beat_failed with no failed beat, thesis_unseeded with no
    dormant slot) is a claimed absence the orchestrator cannot confirm, which is
    exactly a model self-report and blocks.
    """
    for section in DEGRADABLE_SECTIONS:
        for i, entry in enumerate(issue.get(section) or []):
            if not isinstance(entry, dict):
                continue
            degradation = entry.get("degradation")
            if not isinstance(degradation, dict):
                continue  # null / absent is the normal case — no claim to check
            where = f"{section}[{_ref(entry, i)}].degradation"
            kind = degradation.get("kind")
            trigger = DEGRADATION_REGISTER.get(kind)
            if trigger is None:
                problems.append(
                    Finding("empty_section", where, f"degradation kind {kind!r} is not in the register")
                )
            elif not trigger(degradation, issue=issue, state=state, beats_failed=beats_failed):
                problems.append(
                    Finding("empty_section", where, f"degradation claims {kind!r} but its trigger is not mechanically true")
                )


def derive_stats(issue) -> dict:
    """The six array-derived stats counts, computed from the issue's arrays.

    The single home for how a count is taken from the issue, shared by the two
    components that must never disagree about it: the **publisher** stamps this
    onto the issue (build 06), and the **validator** below recomputes it to
    confirm the stamp still matches the arrays. If the derivation lived in two
    places, a change to one counting rule (say, which objects carry sources[])
    would let a published bar silently drift from the arrays it summarizes — the
    exact lie `stats` is derived to prevent ([07]: "stats is derived, never
    authored"). So it lives here once.

    `previous_issue` is deliberately NOT here: it is not a count of this issue's
    arrays but a walk back over the issues on disk, which is IO the pure
    validator does not do. The publisher adds it after this ([07] stats shape).
    """
    quiet = issue.get("quiet_this_cycle", {})
    return {
        "tracked_updates": len(issue.get("watchlist") or []),
        "tracked_quiet": len(quiet.get("no_news") or []),
        "new_on_radar": len(issue.get("new_on_radar") or []),
        "frontier_items": len(issue.get("elsewhere_on_frontier") or []),
        "sources_cited": sum(1 for _ in _iter_sources(issue)),
        "critic_catches": len(quiet.get("critic_catches") or []),
    }


def _check_derived_stats_mismatch(issue, problems):
    """`stats` agrees with the arrays it summarizes, or it is an empty draft.

    stats == {} is a draft the orchestrator has not derived yet (build 06) — the
    manager is forbidden to author it, so an empty stats is expected here and is
    skipped SILENTLY. Any other stats is recomputed from the arrays and must
    agree exactly; a disagreement is a bug in the derivation, not an edit.

    The recompute goes through the shared `derive_stats` — the publisher stamps
    with the same function, so the gate compares like with like and the two
    cannot drift. `previous_issue` is a walk, not a count, so it is not checked
    here; the derivation the manager could get wrong is the counting.
    """
    stats = issue.get("stats")
    if stats == {}:
        return  # a draft — the orchestrator derives stats in build 06
    if not isinstance(stats, dict):
        problems.append(Finding("derived_stats_mismatch", "stats", "stats must be an object"))
        return

    for key, want in derive_stats(issue).items():
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
      - status changed without NEW evidence — see transition_brings_new_evidence.

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

        if item.get("status") != prior.get("status") and not transition_brings_new_evidence(item, prior):
            problems.append(
                Finding(
                    "queue_tamper",
                    where,
                    f"status changed {prior.get('status')!r} → {item.get('status')!r} "
                    "with no new source citing the transition",
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


def transition_brings_new_evidence(item, prior):
    """A status transition cites a source the baseline item did not already carry.

    The non-vacuous reading, and the ONE definition of the queue's "no source,
    no transition" rule — shared by the two components that enforce it from
    opposite ends: the **validator** blocks an issue whose snapshot flips a
    status without new evidence, and the **publisher** refuses to apply that same
    flip to `state/catalyst-queue.json`. If the rule lived in two places, a
    generous publisher could write a transition the strict validator would have
    rejected, and the tamper-evidence guarantee would have a hole.

    A well-formed item ALWAYS carries a window_source (it is what dated the
    window), so "does the item have any source" is trivially true and would let
    every unsourced status flip sail through. A real transition brings NEW
    evidence: the item's citation set — every URL across sources[], window_source,
    and slip_log[].source — must contain at least one URL the baseline item's
    citation set did not. Coasting on the citation that dated the window is
    exactly the tamper this check exists to catch.
    """
    return bool(_citation_urls(item) - _citation_urls(prior))


def _citation_urls(item):
    """Every source URL an item carries — across sources[], window_source, slips."""
    urls = set()
    for source in item.get("sources") or []:
        if isinstance(source, dict) and source.get("url"):
            urls.add(source["url"])
    window_source = item.get("window_source")
    if isinstance(window_source, dict) and window_source.get("url"):
        urls.add(window_source["url"])
    for slip in item.get("slip_log") or []:
        if isinstance(slip, dict) and isinstance(slip.get("source"), dict) and slip["source"].get("url"):
            urls.add(slip["source"]["url"])
    return urls


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


# The reader-facing marker text for a stale calendar (spec/06 register). One home,
# so the advisory the validator files and the dashboard's marker match byte-for-byte.
CALENDAR_STALE_MARKER = "conference calendar stale — surge disabled"


def validate_issue(
    issue,
    *,
    state,
    queue_baseline=None,
    baseline_expired=False,
    beats_failed=None,
    calendar_stale=False,
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

    `calendar_stale` is the mechanical fact run.py computed in Stage 1
    (calendar.stale_reason). When true, a `calendar_stale` advisory is filed here
    — not by the model — because a stale calendar is the one failure that would
    otherwise be SILENT (spec/02): the marker must ride on every issue whether or
    not the Codex critic runs, so the deterministic gate is where it belongs.
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
    if calendar_stale:
        advisory.append(
            Finding("calendar_stale", "conference_calendar", CALENDAR_STALE_MARKER)
        )

    return ValidationResult(blocking=tuple(blocking), advisory=tuple(advisory))
