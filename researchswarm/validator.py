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

from dataclasses import dataclass, field

from researchswarm.findings import SOURCE_FIELDS, SOURCE_TIERS
from researchswarm.findings import DOSSIER_ENTITY_KIND as _DOSSIER_KIND
from researchswarm.findings import DOSSIER_SECTIONS as _PAYLOAD_SECTIONS

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
    """The array-derived stats counts, computed from the issue's arrays.

    The single home for how a count is taken from the issue, shared by the two
    components that must never disagree about it: the **publisher** stamps this
    onto the issue (build 06), and the **validator** below recomputes it to
    confirm the stamp still matches the arrays. If the derivation lived in two
    places, a change to one counting rule (say, which objects carry sources[])
    would let a published bar silently drift from the arrays it summarizes — the
    exact lie `stats` is derived to prevent ([07]: "stats is derived, never
    authored"). So it lives here once.

    Dispatches on `schema_version`: a v2.0.0 issue counts the program-detective
    arrays ([07] v2 `stats` shape); anything else keeps the v1 market-digest
    counts. Both exclude `previous_issue` — it is not a count of this issue's
    arrays but a walk back over the issues on disk, which is IO the pure
    validator does not do. The publisher adds it after this ([07] stats shape).
    """
    if issue.get("schema_version") == SCHEMA_VERSION_V2:
        return _derive_stats_v2(issue)
    return _derive_stats_v1(issue)


def _derive_stats_v1(issue) -> dict:
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


# ===========================================================================
# issue.json v2.0.0 — the per-program detective path
# ===========================================================================
#
# The top-level noun changed (a market-wide digest became a per-program
# detective), so v2 is a major bump, not a delta ([07] v2.0.0). The v1 checks
# above are left untouched and the two paths run side by side, dispatched on the
# issue's own `schema_version`, so the engine can migrate one stage at a time
# without a red tree. When the manager and publisher emit v2, the v1 path retires.
#
# What v2 adds over the ported spine checks is the ADMISSION RULE made mechanical
# ([07] "what the validator checks"): every typed competitor and house item must
# carry a structured read-through, so "no read-through" is a deterministic block,
# not a critic judgment. Four new blocking kinds enforce it.

SCHEMA_VERSION_V2 = "2.0.0"

# The typed relation set ([07] the read-through). The four PROGRAM relations are
# the only ones a `competitors[]` entry may carry; `platform_threat` is company-
# unit and lives in the house view, never on the program's competitor list.
PROGRAM_RELATIONS = frozenset(
    {"mechanism_twin", "target_twin", "setting_rival", "benchmark_soc"}
)
HOUSE_LENSES = frozenset({"partnership_bd", "threat_financing"})
ALL_RELATIONS = PROGRAM_RELATIONS | frozenset({"platform_threat"})

# Top-level sections a v2 issue must carry. Absent the roster (deferred to the
# state-shape build), coverage is not policed here — but the spine sections that
# make an issue readable are: a detective issue with no competitors, no
# indications or no house view is not an issue.
REQUIRED_SECTIONS_V2 = (
    "headline",
    "tldr_bullets",
    "competitors",
    "indications",
    "house_view",
    "quiet_this_cycle",
    "sources_and_method",
)
QUIET_KEYS_V2 = ("no_news", "critic_catches", "open_threads")


def _mapping(obj, key):
    """The value at `key`, but ONLY if it is genuinely a mapping — else `{}`.

    The one way every v2 check reaches into a nested object, and it exists
    because the idiom it replaces is unsound. `x.get(k, {}).get(...)` supplies
    its default only when the key is ABSENT: a key present-but-null, or holding
    the prose the manager sends when it misreads [07], sails past the default and
    raises AttributeError *inside the gate*. `x.get(k) or {}` fixes only the null
    half — a non-empty string is truthy and still crashes.

    Found by the first live-run review: `_check_malformed_dropped_receipt`, the
    check written to catch a manager shape error, crashed on
    `{"quiet_this_cycle": null}` and on `{"quiet_this_cycle": "prose"}` — the two
    inputs it is most likely to meet.

    A gate that CRASHES is strictly worse than one that misses ([06] admission
    test 2: every check is mechanically decidable from the issue alone, and a
    check that dies decides nothing). It takes the whole run down, and it does so
    at exactly the moment the issue is most malformed. So every v2 traversal
    routes through here: an intermediate that is not a mapping yields an empty
    one, the walk finds nothing there, and the SHAPE checks — which read the raw
    field, not this — are what report the malformation.
    """
    if not isinstance(obj, dict):
        return {}
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def _iter_competitor_items_v2(issue):
    """(item, where) for every typed program item obliged to carry a read_through.

    The competitors[], each indication arena's rivals and SOC, and the discovery
    proposals — the items whose read-through answers "why is this a competitor."
    House items are walked separately because they carry a `lens`, not a `relation`.
    """
    for i, c in enumerate(issue.get("competitors") or []):
        if isinstance(c, dict):
            yield c, f"competitors[{_ref(c, i)}]"
    for ind in issue.get("indications") or []:
        if not isinstance(ind, dict):
            continue
        iid = ind.get("indication_id", "?")
        arena = _mapping(ind, "arena")
        for key in ("setting_rivals", "benchmark_soc"):
            for i, item in enumerate(arena.get(key) or []):
                if isinstance(item, dict):
                    yield item, f"indications[{iid}].arena.{key}[{_ref(item, i)}]"
    for i, c in enumerate(issue.get("newly_discovered") or []):
        if isinstance(c, dict):
            yield c, f"newly_discovered[{_ref(c, i)}]"


def _iter_house_items_v2(issue):
    """(item, where) for every house-view lens item obliged to carry a read_through."""
    house = _mapping(issue, "house_view")
    for key in ("partnership_bd", "threat_financing"):
        for i, item in enumerate(house.get(key) or []):
            if isinstance(item, dict):
                yield item, f"house_view.{key}[{_ref(item, i)}]"


def _iter_intel_objects_v2(issue):
    """(obj, where) for the affirmative content objects that MUST carry sources[].

    Headline, competitors, arena rivals/SOC, house lens items, and discovery
    proposals — every object making a factual claim a reader acts on. Themes cite
    via entity_refs rather than sources[] (unchanged from v1), and the queue,
    critic catches, and dropped receipts are provenance objects walked only by the
    malformed check, so they are excluded here exactly as v1 excluded their kin.
    """
    headline = issue.get("headline")
    if isinstance(headline, dict) and headline:
        yield headline, "headline"
    yield from _iter_competitor_items_v2(issue)
    yield from _iter_house_items_v2(issue)


def _iter_all_sources_v2(issue):
    """(source, where) for EVERY source object anywhere in a v2 issue.

    The widest walk — what malformed_source and the derived source count both
    need to see. Reuses the intel-object walk, then adds the singleton sources the
    intel walk excludes: treatment-landscape efficacy sources, the dropped-receipt
    sources, the queue's own sources, and the critic's finding receipts.
    """
    for obj, where in _iter_intel_objects_v2(issue):
        yield from _sources_of(obj, where)

    # theme items may carry sources[] even though they are not obliged to
    for i, theme in enumerate(_mapping(issue, "house_view").get("themes_and_signals") or []):
        if isinstance(theme, dict):
            yield from _sources_of(theme, f"house_view.themes_and_signals[{i}]")

    # treatment-landscape efficacy numbers — primary-source-only, one per line
    for ind in issue.get("indications") or []:
        if not isinstance(ind, dict):
            continue
        iid = ind.get("indication_id", "?")
        for j, line in enumerate(_mapping(ind, "treatment_landscape").get("lines") or []):
            if isinstance(line, dict) and line.get("efficacy_source") is not None:
                yield line["efficacy_source"], f"indications[{iid}].treatment_landscape.lines[{j}].efficacy_source"

    # dropped-with-receipt: the source is the receipt the critic's rule reads
    for i, dropped in enumerate(
        _mapping(issue, "quiet_this_cycle").get("dropped_with_receipt") or []
    ):
        if isinstance(dropped, dict) and dropped.get("source") is not None:
            yield dropped["source"], f"quiet_this_cycle.dropped_with_receipt[{i}].source"

    # critic catches carry the sources that refuted a claim
    for i, catch in enumerate(
        _mapping(issue, "quiet_this_cycle").get("critic_catches") or []
    ):
        if isinstance(catch, dict):
            yield from _sources_of(catch, f"quiet_this_cycle.critic_catches[{i}]")

    # the catalyst queue and the critic's blocking-finding receipts — unchanged
    for i, item in enumerate(_mapping(issue, "catalyst_queue").get("items") or []):
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
        _mapping(issue, "critic_report").get("blocking_findings") or []
    ):
        if isinstance(finding, dict) and finding.get("source") is not None:
            yield finding["source"], f"critic_report.blocking_findings[{i}].source"


def _iter_entity_refs_v2(issue):
    """(entity_id, where) for every entity reference in a v2 issue."""
    headline = issue.get("headline")
    if isinstance(headline, dict):
        for ref in headline.get("entity_refs") or []:
            yield ref, "headline.entity_refs"

    for i, bullet in enumerate(issue.get("tldr_bullets") or []):
        if isinstance(bullet, dict):
            for ref in bullet.get("entity_refs") or []:
                yield ref, f"tldr_bullets[{i}].entity_refs"

    for item, where in _iter_competitor_items_v2(issue):
        if item.get("entity_id"):
            yield item["entity_id"], f"{where}.entity_id"
    for item, where in _iter_house_items_v2(issue):
        if item.get("entity_id"):
            yield item["entity_id"], f"{where}.entity_id"

    quiet = _mapping(issue, "quiet_this_cycle")
    for key in ("no_news", "open_threads", "dropped_with_receipt"):
        for i, entry in enumerate(quiet.get(key) or []):
            if isinstance(entry, dict) and entry.get("entity_id"):
                yield entry["entity_id"], f"quiet_this_cycle.{key}[{i}].entity_id"

    for i, theme in enumerate(_mapping(issue, "house_view").get("themes_and_signals") or []):
        if isinstance(theme, dict):
            for ref in theme.get("evidence_refs") or []:
                yield ref, f"house_view.themes_and_signals[{i}].evidence_refs"

    for i, update in enumerate(issue.get("thesis_updates") or []):
        if isinstance(update, dict):
            for ref in update.get("triggered_by") or []:
                yield ref, f"thesis_updates[{i}].triggered_by"

    for i, item in enumerate(_mapping(issue, "catalyst_queue").get("items") or []):
        if isinstance(item, dict):
            for ref in item.get("entity_ids") or []:
                yield ref, f"catalyst_queue.items[{item.get('id', i)}].entity_ids"


def _check_uncited_claim_v2(issue, problems):
    """Every affirmative content object carries at least one source[]."""
    for obj, where in _iter_intel_objects_v2(issue):
        if obj and not _has_source(obj):
            problems.append(Finding("uncited_claim", where, "no sources[]"))


def _check_malformed_source_v2(issue, problems):
    """Every source object anywhere carries the four core fields, tier valid."""
    for source, where in _iter_all_sources_v2(issue):
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


def _check_dangling_entity_v2(issue, state, problems):
    """Every entity reference resolves to a known entity or a discovery slug.

    Known = the state roster UNION the entity_ids this issue declares in
    newly_discovered — a discovery entry introduces itself, exactly as v1's
    new_on_radar did. Anything else dangling blocks.
    """
    self_introduced = {
        entry.get("entity_id")
        for entry in issue.get("newly_discovered") or []
        if isinstance(entry, dict) and entry.get("entity_id")
    }
    known = state.entity_ids | self_introduced
    for ref, where in _iter_entity_refs_v2(issue):
        if ref not in known:
            problems.append(Finding("dangling_entity", where, f"entity_id {ref!r} resolves to nothing"))


def _check_unaccounted_entity(issue, roster, problems):
    """Every rostered competitor is accounted for this cycle — and in one place.

    The v2 analogue of v1's unaccounted_watchlist_entity, but against the PROGRAM
    ROSTER (this program's typed competitors), not a flat watchlist. The manager's
    coverage duty: each rostered entity either MOVED (it appears in `competitors[]`
    or an indication arena) or is explicitly QUIET (in `quiet_this_cycle.no_news`),
    every cycle. An entity in NEITHER is unaccounted; an entity in BOTH is
    double-accounted (the spec says exactly one). Both block.

    The roster is deliberately narrow. A house-view entity (wider aperture, not a
    typed program competitor) and a queue-only holder (e.g. a co-developer named
    only in `entity_ids`) are NOT on the roster, so they carry no coverage duty —
    which is why the coverage set and the dangling check's known set are different
    sets. When `roster` is empty/None the check is skipped: an unknown roster can
    hold nothing accountable.
    """
    if not roster:
        return

    moved = {
        c.get("entity_id")
        for c in issue.get("competitors") or []
        if isinstance(c, dict) and c.get("entity_id")
    }
    for ind in issue.get("indications") or []:
        if not isinstance(ind, dict):
            continue
        arena = _mapping(ind, "arena")
        for key in ("setting_rivals", "benchmark_soc"):
            for item in arena.get(key) or []:
                if isinstance(item, dict) and item.get("entity_id"):
                    moved.add(item["entity_id"])

    quiet = {
        entry.get("entity_id")
        for entry in _mapping(issue, "quiet_this_cycle").get("no_news") or []
        if isinstance(entry, dict) and entry.get("entity_id")
    }

    for entity_id in sorted(roster):
        here, there = entity_id in moved, entity_id in quiet
        if here and there:
            problems.append(
                Finding(
                    "unaccounted_entity",
                    entity_id,
                    "double-accounted — appears as both a moved competitor and quiet (exactly one is required)",
                )
            )
        elif not here and not there:
            problems.append(
                Finding(
                    "unaccounted_entity",
                    entity_id,
                    "rostered competitor in neither competitors/arena nor quiet_this_cycle.no_news",
                )
            )


def _check_empty_section_v2(issue, state, problems):
    """Each required top-level section is present and non-empty.

    The one implicit exemption survives from v1: `competitors` may be empty when
    every tracked entity sits in quiet_this_cycle.no_news (a genuinely quiet
    cycle, decidable from the issue alone). Per-indication arena dormancy and the
    new degradation-register rows are deferred to the researcher/degradation build
    that actually emits apertures — an empty arena is not policed here yet.
    """
    for section in REQUIRED_SECTIONS_V2:
        if not _section_empty_v2(issue, section):
            continue
        if section == "competitors" and _trigger_quiet_cycle(
            {}, issue=issue, state=state, beats_failed=[]
        ):
            continue
        problems.append(
            Finding("empty_section", section, "required section is empty and no registered degradation explains it")
        )


def _section_empty_v2(issue, section):
    value = issue.get(section)
    if section == "quiet_this_cycle":
        return not isinstance(value, dict) or any(
            key not in value for key in QUIET_KEYS_V2
        )
    if section in ("headline", "sources_and_method", "house_view"):
        return not value  # a non-null object is required
    return not value  # tldr_bullets, competitors, indications — a non-empty list


def _check_missing_read_through(issue, problems):
    """The admission rule, made mechanical ([07]).

    Every typed competitor and house item publishes with a read-through or it does
    not publish. Deterministic parts only: the read_through object is present, its
    `text` is non-empty, and its typed key (`relation` for program items, `lens`
    for house items) is inside its enum. Whether the prose earns its place is the
    critic's `weak_read_through` advisory, not this gate.
    """
    for item, where in _iter_competitor_items_v2(issue):
        rt = item.get("read_through")
        if not isinstance(rt, dict):
            problems.append(Finding("missing_read_through", where, "no read_through"))
            continue
        if not (rt.get("text") or "").strip():
            problems.append(Finding("missing_read_through", where, "read_through.text is empty"))
        relation = rt.get("relation")
        if relation not in ALL_RELATIONS:
            problems.append(
                Finding("missing_read_through", where, f"read_through.relation {relation!r} is outside the enum")
            )

    for item, where in _iter_house_items_v2(issue):
        rt = item.get("read_through")
        if not isinstance(rt, dict):
            problems.append(Finding("missing_read_through", where, "no read_through"))
            continue
        if not (rt.get("text") or "").strip():
            problems.append(Finding("missing_read_through", where, "read_through.text is empty"))
        lens = rt.get("lens")
        if lens not in HOUSE_LENSES:
            problems.append(
                Finding("missing_read_through", where, f"read_through.lens {lens!r} is outside the enum")
            )


# ---------------------------------------------------------------------------
# The declarative shape table — [07]'s required shapes, as data
# ---------------------------------------------------------------------------
#
# WHY A TABLE. The gate's first v2 pass hand-wrote one function per shape, and
# covered roughly fourteen of [07]'s ~twenty required shapes. The other six were
# not *decided* against — they were simply never written, and the way the system
# discovered each one was by CRASHING into it, one field at a time, after
# publishing: a manager emits an off-spec shape, a defensive accessor swallows
# it, and the gate reports clean because it could not read the data. Four such
# incidents landed in a single night (the landscape envelope, open threads as
# bare strings, a promotion proposal as prose, dropped receipts under invented
# key names).
#
# The defect was never any one missing check. It was that COVERAGE lived in
# prose — a reviewer's memory of which fields had a function — so it could drift
# from the contract silently and without a diff. A table cannot drift silently:
# it is one artifact, next to the spec it mirrors, and `test_shape_table_drift`
# fails the moment the two disagree. Adding a shape to [07] and forgetting the
# gate is now a red test, not a 3am AttributeError.
#
# WHAT LIVES HERE AND WHAT DOES NOT. Only SHAPE — the container type, the keys
# [07] marks required, and the closed enums. Anything encoding JUDGMENT stays a
# hand-written check, because judgment does not compress to a row: the admission
# rule (`missing_read_through`), the platform_threat-belongs-in-the-house-view
# rule (`untyped_competitor`), the capped-with-a-receipt rule
# (`blind_spot_overflow`), the primary-source-only bar
# (`landscape_number_unsourced`), and the mechanically-corroborated dormancy
# rule (`_check_empty_arena_v2`). The table says what the data IS; those say
# what it MEANS.


_MISSING = object()  # "the key was absent", distinct from a present null


@dataclass(frozen=True)
class Shape:
    """One row of the contract: the shape required at one path in a v2 issue.

    `path` is a dotted walk from the issue root; a `foo[]` segment means "each
    element of the list at foo", so one row covers every competitor at once.
    `kind` is the finding kind the row files — several rows deliberately reuse
    the kinds the retired hand-written checks filed (`malformed_open_thread`,
    `malformed_treatment_landscape`, …) because those names are the repair
    vocabulary the manager's retry prompt and the critic already speak.

    `required` means the path must be PRESENT and non-null. It is set only on
    rows whose absence nothing else reports: the seven `REQUIRED_SECTIONS_V2`
    already have `empty_section` for that, so their rows police type and keys
    and leave absence to the check that owns it, and no defect is filed twice.
    """

    path: str
    kind: str = "malformed_shape"
    container: str = "object"  # "object" | "array"
    required: bool = False
    keys: tuple[str, ...] = ()
    enums: dict[str, frozenset] = field(default_factory=dict)


# Closed enums [07] states. Named once so a row and a prose reference to the
# same enum cannot drift.
RUN_STATUSES = frozenset(
    {"published", "published_uncritiqued", "published_with_unresolved_findings", "failed"}
)
CRITIC_VERDICTS = frozenset({"pass", "pass_with_advisories", "blocked", "not_run"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
THESIS_BEARINGS = frozenset({"confirms", "challenges", "neutral"})
FAILURE_TIERS = frozenset({"program_tier", "indication_tier"})
INDICATION_ROLES = frozenset({"active_arena", "priority_indication"})
QUEUE_STATUSES = frozenset({"pending", "slipped", "delivered", "dead"})
FED_BY = frozenset({"competitor_discovery", "scheduled", "manual"})
THESIS_CHANGES = frozenset({"amended", "added", "retired"})
ROT_STATUSES = frozenset({"fresh", "stale"})
INTEREST_TIERS = frozenset({"strong", "watching"})

# The contract, in rows. Ordered as [07] orders its sections so the two read
# side by side. A `null`-legal field ([07] says so in words — `previous_issue`
# on run #1, `overflow` when nothing overflowed, `efficacy_source` on a line
# claiming no number) is deliberately ABSENT from a `keys` tuple: requiring it
# would turn a documented honest null into a finding.
ISSUE_SHAPE_V2 = (
    # --- issue ------------------------------------------------------------
    Shape(
        "issue",
        required=True,
        keys=("id", "program_id", "published_at", "coverage_window", "run"),
    ),
    Shape("issue.coverage_window", keys=("from", "to")),
    Shape(
        "issue.run",
        keys=(
            "run_id",
            "status",
            "critic_verdict",
            "critic_retries",
            "thesis_version",
            "interest_list_version",
            "models",
        ),
        enums={"status": RUN_STATUSES, "critic_verdict": CRITIC_VERDICTS},
    ),
    Shape("issue.run.models", keys=("researchers", "manager", "critic")),
    # --- program ----------------------------------------------------------
    Shape(
        "program",
        required=True,
        keys=(
            "id",
            "name",
            "sponsor",
            "modality",
            "target",
            "moa",
            "one_line",
            "priority_indications",
            "clinical_stage",
            "config_source",
            "aperture",
        ),
    ),
    Shape("program.priority_indications", container="array"),
    Shape("program.aperture", keys=("biology_scan", "arena_scans")),
    Shape("program.aperture.biology_scan", keys=("target", "moa")),
    Shape("program.aperture.arena_scans", container="array"),
    # --- headline ---------------------------------------------------------
    Shape(
        "headline",
        keys=("title", "summary", "so_what", "entity_refs", "confidence", "sources"),
        enums={"confidence": CONFIDENCE_LEVELS},
    ),
    Shape("headline.entity_refs", container="array"),
    Shape("headline.sources", container="array"),
    # --- stats ------------------------------------------------------------
    Shape(
        "stats",
        required=True,
        keys=(
            "competitors_moved",
            "competitors_quiet",
            "newly_discovered",
            "indications_covered",
            "house_items",
            "blind_spots_ranked",
            "sources_cited",
            "critic_catches",
        ),
    ),
    # --- tldr_bullets -----------------------------------------------------
    Shape("tldr_bullets", container="array"),
    Shape(
        "tldr_bullets[]",
        keys=("text", "entity_refs", "priority"),
        enums={"priority": CONFIDENCE_LEVELS},
    ),
    Shape("tldr_bullets[].entity_refs", container="array"),
    # --- catalyst_queue ---------------------------------------------------
    Shape("catalyst_queue", required=True, keys=("snapshot_of", "items")),
    Shape("catalyst_queue.items", container="array"),
    Shape(
        "catalyst_queue.items[]",
        keys=(
            "id",
            "catalyst",
            "first_expected_window",
            "expected_window",
            "status",
            "slip_log",
            "bears_on_thesis_slot",
            "fed_by",
            "sources",
        ),
        enums={"status": QUEUE_STATUSES, "fed_by": FED_BY},
    ),
    Shape("catalyst_queue.items[].slip_log", container="array"),
    Shape("catalyst_queue.items[].sources", container="array"),
    # --- competitors ------------------------------------------------------
    Shape("competitors", container="array"),
    Shape(
        "competitors[]",
        keys=("entity_id", "name", "type", "status", "priority", "summary", "read_through", "sources"),
        enums={"priority": CONFIDENCE_LEVELS},
    ),
    # `relation` / `lens` / non-empty `text` belong to the admission rule
    # (`missing_read_through`), not here — this row covers only what that check
    # does not read, so one defect never files two findings.
    Shape(
        "competitors[].read_through",
        keys=("thesis_bearing", "established_by"),
        enums={"thesis_bearing": THESIS_BEARINGS},
    ),
    Shape("competitors[].sources", container="array"),
    Shape(
        "competitors[].failure",
        keys=("tier", "status", "archived", "established_by"),
        enums={"tier": FAILURE_TIERS},
    ),
    # --- indications ------------------------------------------------------
    Shape("indications", container="array"),
    Shape(
        "indications[]",
        keys=("indication_id", "name", "role", "program_context", "arena"),
        enums={"role": INDICATION_ROLES},
    ),
    Shape("indications[].arena", keys=("setting_rivals", "benchmark_soc")),
    Shape("indications[].arena.setting_rivals", container="array"),
    Shape("indications[].arena.benchmark_soc", container="array"),
    # Arena items are ordinary competitor items ([07] §indications), one altitude
    # down — the same duty to name themselves and carry sourced evidence.
    Shape(
        "indications[].arena.setting_rivals[]",
        keys=("entity_id", "name", "read_through", "sources"),
    ),
    Shape(
        "indications[].arena.benchmark_soc[]",
        keys=("entity_id", "name", "read_through", "sources"),
    ),
    Shape(
        "indications[].arena.setting_rivals[].read_through",
        keys=("thesis_bearing", "established_by"),
        enums={"thesis_bearing": THESIS_BEARINGS},
    ),
    Shape(
        "indications[].arena.benchmark_soc[].read_through",
        keys=("thesis_bearing", "established_by"),
        enums={"thesis_bearing": THESIS_BEARINGS},
    ),
    Shape(
        "indications[].treatment_landscape",
        kind="malformed_treatment_landscape",
        keys=("as_of", "last_changed", "changed_by", "keyed_by", "lines"),
    ),
    Shape(
        "indications[].treatment_landscape.lines",
        kind="malformed_treatment_landscape",
        container="array",
    ),
    Shape(
        "indications[].treatment_landscape.lines[]",
        kind="malformed_treatment_landscape",
        keys=("line", "biomarker_subgroup", "standard_of_care", "bar_direction"),
    ),
    # --- quiet_this_cycle -------------------------------------------------
    Shape(
        "quiet_this_cycle",
        keys=("no_news", "critic_catches", "open_threads", "dropped_with_receipt"),
    ),
    Shape("quiet_this_cycle.no_news", container="array"),
    Shape("quiet_this_cycle.critic_catches", container="array"),
    Shape(
        "quiet_this_cycle.critic_catches[]",
        keys=("claim", "rejected_because", "detail", "caught_by"),
    ),
    Shape("quiet_this_cycle.no_news[]", keys=("entity_id", "name", "cycles_quiet")),
    Shape("quiet_this_cycle.open_threads", kind="malformed_open_thread", container="array"),
    Shape(
        "quiet_this_cycle.open_threads[]",
        kind="malformed_open_thread",
        keys=("entity_id", "thread", "since"),
    ),
    Shape(
        "quiet_this_cycle.dropped_with_receipt",
        kind="malformed_dropped_receipt",
        container="array",
    ),
    Shape(
        "quiet_this_cycle.dropped_with_receipt[]",
        kind="malformed_dropped_receipt",
        keys=("name", "dropped_because", "source"),
    ),
    # --- newly_discovered -------------------------------------------------
    Shape("newly_discovered", container="array"),
    Shape(
        "newly_discovered[]",
        keys=(
            "entity_id",
            "name",
            "type",
            "priority",
            "what_it_is",
            "development",
            "proposed_relation",
            "read_through",
            "promotion_proposal",
            "sources",
        ),
        enums={"priority": CONFIDENCE_LEVELS, "proposed_relation": ALL_RELATIONS},
    ),
    Shape(
        "newly_discovered[].read_through",
        keys=("thesis_bearing", "established_by"),
        enums={"thesis_bearing": THESIS_BEARINGS},
    ),
    Shape("newly_discovered[].sources", container="array"),
    Shape(
        "newly_discovered[].promotion_proposal",
        kind="malformed_promotion_proposal",
        keys=("promote_to_competitors", "reason"),
    ),
    # --- house_view -------------------------------------------------------
    Shape(
        "house_view",
        keys=("partnership_bd", "threat_financing", "themes_and_signals", "blind_spots"),
    ),
    Shape("house_view.partnership_bd", container="array"),
    Shape("house_view.threat_financing", container="array"),
    Shape("house_view.partnership_bd[]", keys=("name", "summary", "read_through", "sources")),
    Shape("house_view.threat_financing[]", keys=("name", "summary", "read_through", "sources")),
    # A house read-through carries a `lens`, not a `thesis_bearing` — the house
    # aperture is wider than the thesis. `lens` and `text` are the admission
    # rule's; only the provenance field is left for the table.
    Shape("house_view.partnership_bd[].read_through", keys=("established_by",)),
    Shape("house_view.threat_financing[].read_through", keys=("established_by",)),
    Shape("house_view.themes_and_signals", container="array"),
    Shape("house_view.themes_and_signals[].evidence_refs", container="array"),
    Shape(
        "house_view.themes_and_signals[]",
        keys=("theme", "evidence_refs", "argument", "thesis_impact"),
    ),
    Shape("house_view.blind_spots", keys=("cap", "ranked")),
    Shape("house_view.blind_spots.ranked", container="array"),
    Shape(
        "house_view.blind_spots.ranked[]",
        keys=("rank", "blind_spot", "why_it_matters", "signal_magnitude", "mitigation"),
        enums={"signal_magnitude": CONFIDENCE_LEVELS},
    ),
    # --- thesis_updates ---------------------------------------------------
    # The list itself may be honestly empty (no drift is information, [06]), so
    # only its ENTRIES carry a shape.
    Shape("thesis_updates", container="array"),
    Shape(
        "thesis_updates[]",
        keys=("change", "field", "before", "after", "triggered_by"),
        enums={"change": THESIS_CHANGES},
    ),
    Shape("thesis_updates[].triggered_by", container="array"),
    # --- critic_report ----------------------------------------------------
    Shape(
        "critic_report",
        required=True,
        keys=(
            "verdict",
            "retries_used",
            "blocking_findings",
            "advisory_findings",
            "validator_report",
        ),
        enums={"verdict": CRITIC_VERDICTS},
    ),
    Shape("critic_report.blocking_findings", container="array"),
    Shape("critic_report.advisory_findings", container="array"),
    Shape("critic_report.validator_report", keys=("passed", "retries_used", "findings")),
    Shape("critic_report.validator_report.findings", container="array"),
    # --- sources_and_method -----------------------------------------------
    Shape(
        "sources_and_method",
        keys=(
            "apertures_run",
            "apertures_degraded",
            "registry_watch",
            "source_tier_counts",
            "paywalled_flagged",
            "interest_list",
        ),
    ),
    Shape("sources_and_method.apertures_run", container="array"),
    Shape("sources_and_method.apertures_run[]", keys=("aperture", "scope", "status")),
    Shape("sources_and_method.apertures_degraded", container="array"),
    Shape(
        "sources_and_method.registry_watch",
        keys=(
            "input_class",
            "tracked_nct_ids",
            "polled_by",
            "diffs_observed",
            "coverage_note",
        ),
    ),
    Shape("sources_and_method.registry_watch.tracked_nct_ids", container="array"),
    Shape("sources_and_method.registry_watch.diffs_observed", container="array"),
    Shape("sources_and_method.registry_watch.diffs_observed[]", keys=("source", "note")),
    Shape("sources_and_method.source_tier_counts", keys=("primary", "trade", "aggregator")),
    Shape("sources_and_method.paywalled_flagged", container="array"),
    Shape(
        "sources_and_method.interest_list",
        keys=(
            "source",
            "version",
            "last_edited",
            "last_edited_by",
            "rot_status",
            "interests",
        ),
        enums={"rot_status": ROT_STATUSES},
    ),
    Shape("sources_and_method.interest_list.interests", container="array"),
    Shape(
        "sources_and_method.interest_list.interests[]",
        keys=("tier", "note"),
        enums={"tier": INTEREST_TIERS},
    ),
)


def _label(element, index):
    """A readable name for a list element — its own id if it has one, else index.

    Keeps a `where` traceable by eye: `competitors[asset_her3_dxd].read_through`
    rather than `competitors[3].read_through`, so a manager repairing a finding
    does not have to count array positions in its own output.
    """
    if isinstance(element, dict):
        for key in ("entity_id", "indication_id", "id"):
            value = element.get(key)
            if isinstance(value, str) and value:
                return value
    return index


def _walk_path(issue, path):
    """(value, where) for every node the dotted `path` names. NEVER raises.

    The one traversal every row uses. It is walking adversarial input by
    definition — the table exists because managers emit nulls, prose and wrong
    containers at arbitrary depth — so every step is total: an intermediate that
    is not a mapping, or a `foo[]` segment over something that is not a list,
    contributes NOTHING and the walk continues. That is not tolerance; the row
    naming that intermediate is what reports it. A gate that crashes is strictly
    worse than one that misses, because it takes the run down AFTER publishing
    ([06] admission test 2: a check that dies decides nothing).

    A yielded value may be `_MISSING` — absent is a distinct answer from null,
    and only the caller knows whether the row requires presence.
    """
    frontier = [(issue, "")]
    for segment in path.split("."):
        is_list = segment.endswith("[]")
        key = segment[:-2] if is_list else segment
        nxt = []
        for node, where in frontier:
            if not isinstance(node, dict):
                continue
            here = f"{where}.{key}" if where else key
            value = node.get(key, _MISSING)
            if not is_list:
                nxt.append((value, here))
            elif isinstance(value, list):
                nxt.extend(
                    (element, f"{here}[{_label(element, i)}]")
                    for i, element in enumerate(value)
                )
        frontier = nxt
    return frontier


def _in_enum(value, allowed) -> bool:
    """Membership that survives an unhashable value — a manager that sends a
    list or an object where an enum string belongs must get a finding, not a
    TypeError out of the gate."""
    try:
        return value in allowed
    except TypeError:
        return False


def _check_shape(shape, value, where, problems):
    """Apply one row to one node. Total: every branch either files or returns."""
    if value is _MISSING or value is None:
        if shape.required:
            problems.append(
                Finding(shape.kind, where, "required by [07] but absent or null")
            )
        return

    if shape.container == "array":
        if not isinstance(value, list):
            problems.append(
                Finding(shape.kind, where, f"must be a list, got {type(value).__name__}")
            )
        return

    if shape.container == "string":
        # Added for the dossier record table, where the load-bearing fields at
        # the root are scalars (`entity_id`, `as_of`) rather than objects. No
        # ISSUE_SHAPE_V2 row uses it, so this is additive: the "object" default
        # is untouched. A container/prose confusion here is exactly the defect
        # this row exists to name — an `as_of` that is a dict renders as a date
        # on the page and silently claims freshness it does not have.
        if not isinstance(value, str) or not value.strip():
            problems.append(
                Finding(shape.kind, where, f"must be a non-empty string, got {type(value).__name__}")
            )
        return

    if not isinstance(value, dict):
        expected = ", ".join(shape.keys) or "the shape [07] specifies"
        problems.append(
            Finding(
                shape.kind,
                where,
                f"must be an object with {expected}, got {type(value).__name__}",
            )
        )
        return

    missing = [k for k in shape.keys if value.get(k, _MISSING) in (_MISSING, None, "")]
    if missing:
        problems.append(
            Finding(
                shape.kind,
                where,
                f"missing required key(s) {', '.join(missing)}"
                f" (has: {', '.join(sorted(str(k) for k in value)) or 'nothing'})",
            )
        )

    for key, allowed in shape.enums.items():
        got = value.get(key, _MISSING)
        if got is _MISSING or got is None:
            continue  # absence is the `keys` tuple's business, not the enum's
        if not _in_enum(got, allowed):
            problems.append(
                Finding(
                    shape.kind,
                    f"{where}.{key}",
                    f"{got!r} is outside the enum {sorted(allowed)}",
                )
            )


def _check_issue_shape_v2(issue, problems, table=ISSUE_SHAPE_V2):
    """The whole contract, enforced by one walk over one table.

    Replaces the per-field shape functions that covered two-thirds of [07] and
    left the rest to be found by crashing. Coverage is now an artifact that can
    be diffed against the spec, and `test_shape_table_drift` fails when it is not.
    """
    for shape in table:
        for value, where in _walk_path(issue, shape.path):
            _check_shape(shape, value, where, problems)


# ---------------------------------------------------------------------------
# The dossier record contract (#92)
# ---------------------------------------------------------------------------
#
# A dossier is NOT an issue section. It is a STATE file —
# `state/entities/companies/<id>.json` — written by `run.py` through the
# state-edit path and read by every future run and every other program ([03] the
# shared fact layer). So it gets its own table rather than rows in
# ISSUE_SHAPE_V2: a row there would be walked against an issue that will never
# contain a `facts.identity`, and would police nothing forever.
#
# It gets a table at all, rather than hand-written checks, for the reason #92
# states outright ("the dossier shape joins the declarative shape table, so its
# gate coverage cannot drift from its contract") — the discipline adopted after
# hand-written v2 checks were found to cover roughly a third of [07]. Same
# `Shape`, same `_walk_path`, same `_check_shape`; only the table differs.
#
# WHY THIS GATE EXISTS AT ALL, given `findings.validate_dossier_findings`
# already refuses a bad payload at the model seam: the two guard different
# moments. The findings gate asks "did the model emit a legal payload"; this
# asks "is the record we are about to hand to every future run legal after the
# merge". A dossier accumulates across runs — a record can be corrupted by an
# old write, a hand edit, or a merge bug long after the payload that seeded it
# passed. The layer that must be trustworthy is the one that is read, and this
# is the gate on that layer. Asymmetric defence, per the house style.
#
# The vocabulary is IMPORTED, never respelled. `findings` is the bottom of the
# stack (it imports nothing from this package), so taking the enums from there
# is cycle-free and means an enum cannot mean one thing at the model seam and
# another at the state seam. `dossiers` — which sits ABOVE this module, via
# state_edits — is deliberately not imported; the drift test asserts the two
# agree instead, which is the same guarantee without the import cycle.

# The eight FACT sections, derived from the payload contract rather than
# retyped. `coverage` is filtered out because in the persisted record it is not
# a fact: it lives at the root, unprovenanced, as the honesty layer.
DOSSIER_FACT_SECTIONS: tuple[str, ...] = tuple(
    section.name for section in _PAYLOAD_SECTIONS if section.name != "coverage"
)
DOSSIER_LIST_SECTIONS: frozenset[str] = frozenset(
    section.name for section in _PAYLOAD_SECTIONS if section.container == "array"
)
_SECTION_ENUMS: dict[str, dict] = {
    section.name: dict(section.enums) for section in _PAYLOAD_SECTIONS
}

# Every field of a dossier carries the run that established it and the issue it
# came from ([03]: "every factual field cites the run_id/issue that established
# it, so the record has cross-scan memory but cannot drift from published
# truth"). A fact wrapper missing `established_by` is a claim with no audit
# trail, which story 13 exists to make impossible.
FACT_KEYS = ("value", "established_by")

# A drift entry is how a correction APPENDS instead of overwriting (story 14).
# `from` is absent from this tuple on purpose: the first time a field is
# established there is nothing to have drifted from, and a null there is the
# honest answer, not a defect.
DRIFT_ENTRY_KEYS = ("date", "action", "field", "run_id")
DRIFT_ACTIONS = frozenset({"established", "corrected"})

# The root row. Applied directly rather than through `_walk_path`, because the
# root has no path to walk to — `_check_shape` is the whole of it.
DOSSIER_RECORD_ROOT = Shape(
    "<dossier>",
    kind="malformed_dossier",
    required=True,
    keys=("entity_id", "kind", "as_of", "facts", "coverage", "drift_log"),
    enums={"kind": frozenset({_DOSSIER_KIND})},
)


def _dossier_rows() -> tuple[Shape, ...]:
    """Build the record table from the section contract, not by hand.

    Generated rather than typed out so the table cannot fall behind the
    contract by one section: add a section to the payload table and its
    `facts.<name>`, `facts.<name>.value` and element rows appear here in the
    same commit, gated the moment it is named. Hand-listing them is precisely
    the drift `test_the_record_table_does_not_drift` would then have to catch
    after the fact, and a table that cannot drift beats a test that notices.
    """
    rows: list[Shape] = [
        Shape("entity_id", kind="malformed_dossier", container="string", required=True),
        Shape("as_of", kind="malformed_dossier", container="string", required=True),
        Shape("coverage", kind="malformed_dossier", keys=("thin_sections",)),
        Shape("coverage.thin_sections", kind="malformed_dossier", container="array"),
        Shape("drift_log", kind="malformed_dossier", container="array"),
        Shape(
            "drift_log[]",
            kind="malformed_dossier",
            keys=DRIFT_ENTRY_KEYS,
            enums={"action": DRIFT_ACTIONS},
        ),
        Shape("facts", kind="malformed_dossier"),
    ]
    for name in DOSSIER_FACT_SECTIONS:
        is_list = name in DOSSIER_LIST_SECTIONS
        enums = _SECTION_ENUMS.get(name) or {}
        # The provenance wrapper: one row per section, so a fact that lost its
        # `established_by` in a merge is named at the section that lost it.
        rows.append(Shape(f"facts.{name}", kind="malformed_dossier", keys=FACT_KEYS))
        if is_list:
            # The value is the list; the ENUMS live on its elements (a deal's
            # `type`, a setback's `kind`), so the element row carries them.
            rows.append(
                Shape(f"facts.{name}.value", kind="malformed_dossier", container="array")
            )
            rows.append(
                Shape(f"facts.{name}.value[]", kind="malformed_dossier", enums=enums)
            )
        else:
            rows.append(
                Shape(f"facts.{name}.value", kind="malformed_dossier", enums=enums)
            )
    # The nested containers inside the two object sections that [92]'s schema
    # block spells out as lists. Named explicitly because a walk cannot infer
    # them, and an `aliases` emitted as prose is the exact wrong-container
    # defect this table exists to catch.
    rows.extend((
        Shape("facts.identity.value.aliases", kind="malformed_dossier", container="array"),
        Shape("facts.identity.value.listings", kind="malformed_dossier", container="array"),
        Shape("facts.origin.value.founders", kind="malformed_dossier", container="array"),
        Shape("facts.funding.value.rounds", kind="malformed_dossier", container="array"),
        Shape("facts.funding.value.rounds[]", kind="malformed_dossier"),
        Shape("facts.funding.value.rounds[].investors", kind="malformed_dossier", container="array"),
        Shape("facts.pivots.value[].evidence", kind="malformed_dossier", container="array"),
        # A person's PRIOR affiliations are a list, not a string — the writer's
        # `Person.prior` is `list[str]`. Found by the drift test rather than by
        # reading, which is the whole argument for having it.
        Shape("facts.people.value[].prior", kind="malformed_dossier", container="array"),
    ))
    return tuple(rows)


DOSSIER_RECORD_V2 = _dossier_rows()


def check_dossier_record(record, problems) -> None:
    """The whole dossier contract, enforced by one walk over one table.

    NEVER raises, at any depth, for any input. This gate reads a file on disk
    that a prior run wrote — the input is adversarial by construction, and a
    gate that dies decides nothing ([06] admission test 2). `_walk_path` is
    total by design and `_check_shape` files-or-returns on every branch, so
    totality here is inherited rather than re-argued; the root is the one node
    neither covers, and it is handled first.
    """
    _check_shape(DOSSIER_RECORD_ROOT, record, "<dossier>", problems)
    if not isinstance(record, dict):
        return  # the root row already filed it; walking a non-mapping yields nothing
    _check_issue_shape_v2(record, problems, table=DOSSIER_RECORD_V2)


def validate_dossier_record(record) -> ValidationResult:
    """Public seam: is this persisted company dossier structurally legal?

    Returns findings rather than raising or writing — this module is a gate, and
    `run.py` is the sole writer ([03] clause 1). Blocking, not advisory: a
    malformed dossier is inherited by every program that reads it, so it is the
    one place where letting it through is worse than the alternative.
    """
    problems: list[Finding] = []
    check_dossier_record(record, problems)
    return ValidationResult(blocking=tuple(problems))


def _check_untyped_competitor(issue, problems):
    """A competitors[] entry carries one of the four PROGRAM relations, and no other.

    The competitor list is program-level: only mechanism/target twins and the
    indication-level setting/benchmark relations belong. A `platform_threat` is
    company-unit and must live in the house view — placing it in competitors[] is
    the one misplacement this check names explicitly ([07] the platform_threat
    asymmetry).
    """
    for i, c in enumerate(issue.get("competitors") or []):
        if not isinstance(c, dict):
            continue
        where = f"competitors[{_ref(c, i)}]"
        relation = _mapping(c, "read_through").get("relation")
        if relation == "platform_threat":
            problems.append(
                Finding("untyped_competitor", where, "platform_threat is company-unit — it belongs in house_view, not competitors[]")
            )
        elif relation not in PROGRAM_RELATIONS:
            problems.append(
                Finding("untyped_competitor", where, f"relation {relation!r} is not one of the four program relations")
            )


def _check_blind_spot_overflow(issue, problems):
    """The house blind-spot list is capped, and overflow is never silent ([07]).

    When more blind spots exist than the cap, the extras are not dropped — the
    list carries an `overflow` receipt. A `ranked` longer than `cap` with no
    receipt is exactly the silent truncation the admission rule forbids.
    """
    blind_spots = _mapping(issue, "house_view").get("blind_spots")
    if not isinstance(blind_spots, dict):
        return
    cap = blind_spots.get("cap")
    ranked = blind_spots.get("ranked") or []
    if isinstance(cap, int) and len(ranked) > cap and not blind_spots.get("overflow"):
        problems.append(
            Finding(
                "blind_spot_overflow",
                "house_view.blind_spots",
                f"{len(ranked)} ranked blind spots exceed cap {cap} with no overflow receipt",
            )
        )


def _check_landscape_number_unsourced(issue, problems):
    """Treatment-landscape efficacy numbers are primary-source-only ([07], #57).

    Trade press may flag a number, never set it. A landscape line that carries an
    `efficacy_source` whose tier is not `primary` is a benchmark number resting on
    secondary coverage — stricter than the general admission bar, and it blocks.
    A line with no efficacy_source claims no number and is untouched.
    """
    for ind in issue.get("indications") or []:
        if not isinstance(ind, dict):
            continue
        iid = ind.get("indication_id", "?")
        for j, line in enumerate(_mapping(ind, "treatment_landscape").get("lines") or []):
            if not isinstance(line, dict):
                continue
            source = line.get("efficacy_source")
            if source is None:
                continue
            tier = source.get("tier") if isinstance(source, dict) else None
            if tier != "primary":
                problems.append(
                    Finding(
                        "landscape_number_unsourced",
                        f"indications[{iid}].treatment_landscape.lines[{j}]",
                        f"efficacy number sourced to tier {tier!r}, not 'primary'",
                    )
                )


# The v2 degradation register ([06] the register). The vocabulary both gates
# read — same lockstep discipline as v1's DEGRADATION_REGISTER, extended for the
# per-program schema. thesis_unseeded / quiet_cycle / calendar_stale survive from
# v1; beat_failed becomes arena_scan_failed; arena_scan_dormant, china_feed_partial
# and interest_list_stale are new. A kind not in here earns no exemption.
DEGRADATION_REGISTER_V2 = frozenset(
    {
        "thesis_unseeded",
        "quiet_cycle",
        "calendar_stale",
        "arena_scan_failed",
        "arena_scan_dormant",
        "china_feed_partial",
        "interest_list_stale",
    }
)

# The two kinds that explain an EMPTY arena (an indication with no rivals and no
# SOC this cycle). Both must be mechanically confirmable from the audit trail —
# a marker the orchestrator cannot corroborate is a self-report and blocks.
ARENA_DEGRADATION_KINDS = frozenset({"arena_scan_dormant", "arena_scan_failed"})


def _arena_is_empty(indication) -> bool:
    arena = _mapping(indication, "arena")
    return not (arena.get("setting_rivals") or arena.get("benchmark_soc"))


def _indication_degradation(indication):
    """The dormancy/failure marker for an indication, wherever it renders.

    The sample carries it on `treatment_landscape.degradation`; the arena or the
    indication itself are also honest homes. First one found wins — the reader
    sees a marker at the absence regardless of which sub-object holds it.
    """
    for holder in (
        _mapping(indication, "treatment_landscape"),
        _mapping(indication, "arena"),
        indication,
    ):
        degradation = holder.get("degradation")
        if isinstance(degradation, dict):
            return degradation
    return None


def _arena_mechanically_degraded(issue, indication_id) -> bool:
    """The orchestrator-held fact behind an arena dormancy/failure marker.

    True when the audit trail confirms this indication's arena scan did not run
    productively: an `apertures_run` entry for `arena_scan` scoped to the
    indication with status dormant/failed, or the combined aperture id present in
    `apertures_degraded`. Either is a fact run.py holds, so the exemption never
    rests on a model self-report ([06] admission test 2).
    """
    method = _mapping(issue, "sources_and_method")
    for entry in method.get("apertures_run") or []:
        if (
            isinstance(entry, dict)
            and entry.get("aperture") == "arena_scan"
            and entry.get("scope") == indication_id
            and entry.get("status") in {"dormant", "failed"}
        ):
            return True
    return f"arena_scan:{indication_id}" in (method.get("apertures_degraded") or [])


def _check_empty_arena_v2(issue, problems):
    """An empty indication arena is a marked dormancy/failure, or it blocks.

    An arena with no setting rivals and no benchmark/SOC is an absence the reader
    would misread as "no competition here." So it publishes only with a dormancy
    or failure degradation whose kind is registered AND whose mechanical trigger
    is confirmed by the audit trail. A missing marker, an off-topic kind, or a
    marker the apertures do not corroborate all block — the admission rule at the
    indication altitude ([06] dormant/failed aperture).
    """
    for indication in issue.get("indications") or []:
        if not isinstance(indication, dict) or not _arena_is_empty(indication):
            continue
        iid = indication.get("indication_id", "?")
        where = f"indications[{iid}].arena"
        marker = _indication_degradation(indication)
        if marker is None:
            problems.append(
                Finding("empty_section", where, "empty arena with no dormancy/failure degradation to explain it")
            )
            continue
        kind = marker.get("kind")
        if kind not in ARENA_DEGRADATION_KINDS:
            problems.append(
                Finding("empty_section", where, f"degradation kind {kind!r} does not explain an empty arena")
            )
        elif not _arena_mechanically_degraded(issue, iid):
            problems.append(
                Finding(
                    "empty_section",
                    where,
                    f"degradation claims {kind!r} but apertures_run/apertures_degraded do not confirm it",
                )
            )


def _derive_stats_v2(issue) -> dict:
    """The program-detective stats counts ([07] v2 `stats`).

    `sources_cited` counts the affirmative reader-facing intelligence — headline,
    competitors, arena, treatment-landscape efficacy numbers, and the house view
    — which is the set the `source_tier_counts` tiers; provenance sources (the
    queue, dropped receipts, critic catches) are not the digest's cited evidence.
    `previous_issue` is a disk walk the publisher adds, not a count.
    """
    house = _mapping(issue, "house_view")
    quiet = _mapping(issue, "quiet_this_cycle")
    return {
        "competitors_moved": len(issue.get("competitors") or []),
        "competitors_quiet": len(quiet.get("no_news") or []),
        "newly_discovered": len(issue.get("newly_discovered") or []),
        "indications_covered": len(issue.get("indications") or []),
        "house_items": (
            len(house.get("partnership_bd") or [])
            + len(house.get("threat_financing") or [])
            + len(house.get("themes_and_signals") or [])
        ),
        "blind_spots_ranked": len(_mapping(house, "blind_spots").get("ranked") or []),
        "sources_cited": _count_intel_sources_v2(issue),
        "critic_catches": len(quiet.get("critic_catches") or []),
    }


def _count_intel_sources_v2(issue) -> int:
    """Sources on the affirmative reader-facing intelligence — the tiered set.

    Headline + competitors + arena + house (incl. themes) + one per landscape
    efficacy number. Deliberately narrower than both the malformed-source walk
    (which must see every source object, including provenance) and the
    uncited-claim set (which includes discovery proposals): `newly_discovered` is
    a PROPOSAL, not yet cited intelligence, so its sources do not count toward
    what the reader is told is "cited." This is the set `source_tier_counts` tiers.
    """
    total = len(_mapping(issue, "headline").get("sources") or [])
    for ind in issue.get("indications") or []:
        if not isinstance(ind, dict):
            continue
        arena = _mapping(ind, "arena")
        for key in ("setting_rivals", "benchmark_soc"):
            for item in arena.get(key) or []:
                if isinstance(item, dict):
                    total += len(item.get("sources") or [])
        for line in _mapping(ind, "treatment_landscape").get("lines") or []:
            if isinstance(line, dict) and line.get("efficacy_source") is not None:
                total += 1
    for c in issue.get("competitors") or []:
        if isinstance(c, dict):
            total += len(c.get("sources") or [])
    for item, _ in _iter_house_items_v2(issue):
        total += len(item.get("sources") or [])
    for theme in _mapping(issue, "house_view").get("themes_and_signals") or []:
        if isinstance(theme, dict):
            total += len(theme.get("sources") or [])
    return total


# The top-level containers the SHARED (v1-authored) checks traverse with the
# `x.get(k, {}).get(...)` idiom `_mapping` replaces. v2 cannot harden those checks
# — v1 is frozen and deleted whole, and giving it new dependencies is exactly the
# invariant this branch restores — so v2 guards its own call sites instead: it
# proves the container is a mapping BEFORE handing the issue to a shared walk.
SHARED_WALK_CONTAINERS_V2 = ("catalyst_queue", "stats", "sources_and_method")


def _shared_walks_are_safe_v2(issue) -> bool:
    """Whether the SHARED (v1-authored) walks can be run over this issue.

    `_check_queue_tamper` and `_check_derived_stats_mismatch` are shared with v1
    and reach into `issue["catalyst_queue"]` directly, so a manager that sends
    prose or null there would crash them — the same class of failure `_mapping`
    fixes inside the v2 section, one altitude up.

    This is a PREDICATE, not a check: the malformation is reported by the shape
    table, which names all three containers as objects. Reporting it here too
    would file the same defect twice. Nothing is silently tolerated — the finding
    exists, it just has one home.
    """
    return all(
        key not in issue or isinstance(issue[key], dict)
        # ABSENT is safe: `.get(k, {})` supplies its default correctly. PRESENT-
        # but-not-a-mapping is the unsafe case, and null is the sharpest form of
        # it — `.get(k, {})` returns the null rather than the default.
        for key in SHARED_WALK_CONTAINERS_V2
    )


def _validate_issue_v2(issue, *, state, queue_baseline, baseline_expired, calendar_stale, roster=None):
    """The v2.0.0 check-suite: the ported spine checks plus the four new
    admission checks ([07]). Same all-problems-at-once contract as v1, same
    ValidationResult, never raises.

    The queue tamper-evidence and derived-stats checks are shared with v1
    unchanged — the catalyst queue shape did not change, and `derive_stats`
    dispatches on version, so the shared checks compare v2 arrays to v2 counts.
    """
    blocking: list[Finding] = []

    _check_uncited_claim_v2(issue, blocking)
    _check_malformed_source_v2(issue, blocking)
    _check_dangling_entity_v2(issue, state, blocking)
    _check_unaccounted_entity(issue, roster, blocking)
    _check_empty_section_v2(issue, state, blocking)
    _check_empty_arena_v2(issue, blocking)
    if _shared_walks_are_safe_v2(issue):
        _check_derived_stats_mismatch(issue, blocking)
        _check_queue_tamper(issue, queue_baseline, blocking)

    # The contract's SHAPE — one table, one walk. Everything below it encodes a
    # judgment the table cannot: admission, placement, the cap, the sourcing bar.
    _check_issue_shape_v2(issue, blocking)

    _check_missing_read_through(issue, blocking)
    _check_untyped_competitor(issue, blocking)
    _check_blind_spot_overflow(issue, blocking)
    _check_landscape_number_unsourced(issue, blocking)

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
    # interest_list_stale is fail-visible like calendar_stale — a whole-list marker
    # from a date the orchestrator holds (interest_list.rot_status), filed here so
    # it rides every issue whether or not the critic runs ([06] register, #55).
    if _mapping(_mapping(issue, "sources_and_method"), "interest_list").get("rot_status") == "stale":
        advisory.append(
            Finding("interest_list_stale", "sources_and_method.interest_list", INTEREST_LIST_STALE_MARKER)
        )

    return ValidationResult(blocking=tuple(blocking), advisory=tuple(advisory))


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


# The reader-facing marker text for a stale calendar (spec/06 register). One home,
# so the advisory the validator files and the dashboard's marker match byte-for-byte.
CALENDAR_STALE_MARKER = "conference calendar stale — surge disabled"

# The reader-facing marker for a stale interest list (spec/06 register, #55). Same
# one-home discipline as the calendar marker.
INTEREST_LIST_STALE_MARKER = "interest list stale — steering may be out of date"


def validate_issue(
    issue,
    *,
    state,
    queue_baseline=None,
    baseline_expired=False,
    beats_failed=None,
    calendar_stale=False,
    roster=None,
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

    An issue stamped `schema_version` 2.0.0 is a per-program detective issue and
    routes to the v2 check-suite ([07] v2.0.0); anything else keeps the v1
    market-digest path. The dispatch is on the issue's OWN version, not a global
    constant, so the two schemas can be validated side by side while the rest of
    the engine migrates.

    `roster` is the v2 program roster — the set of `entity_id`s typed as this
    program's competitors (`programs.program_roster`: promoted edges ∪ unpromoted
    seeds). It is the accountability set for the coverage check, and it is a
    DIFFERENT set from `state.entity_ids` (which the dangling check uses as the
    wider "every entity that exists" known set — a house-view or queue-only entity
    must resolve without being on the roster). When `roster` is None the coverage
    check is skipped: an unknown roster cannot hold anything accountable. It is
    ignored on the v1 path, which keeps its own watchlist coverage check.
    """
    if not isinstance(issue, dict):
        # This function's own docstring promises it NEVER RAISES, and until now
        # that was untrue: a null or prose issue reached `issue.get(...)` below
        # and raised AttributeError straight out of the gate.
        #
        # Not reachable in production today — `manager.validate_issue_draft`
        # rejects a non-object draft at the seam before this is ever called. It
        # is fixed anyway because the promise is the point: this gate is what
        # decides whether a malformed draft is caught, and a gate that crashes
        # is strictly worse than one that misses. That exact failure cost a run
        # its git commit earlier tonight, after the issue had already published.
        # The defence must not rest on a caller upstream staying careful.
        return ValidationResult(
            blocking=(
                Finding("malformed_shape", "<issue>", f"must be an object, got {type(issue).__name__}"),
            )
        )

    if issue.get("schema_version") == SCHEMA_VERSION_V2:
        return _validate_issue_v2(
            issue,
            state=state,
            queue_baseline=queue_baseline,
            baseline_expired=baseline_expired,
            calendar_stale=calendar_stale,
            roster=roster,
        )

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
