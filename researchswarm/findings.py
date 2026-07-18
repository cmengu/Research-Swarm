"""The findings contract — validation at the seam.

What one researcher hands back. Deliberately NOT issue.json-shaped: researchers
report facts, the manager authors interpretation, and section-shaped researcher
output would invite the manager to paste rather than synthesize.

So these fields are absent by design — thesis_impact, research_angle, so_what,
priority-as-published — and this module rejects them if they appear. A
researcher that emits a stance has broken the contract; catching it here means
it never reaches the manager dressed as a fact.

Spec: docs/spec/04-researchers.md
"""

from __future__ import annotations

SOURCE_TIERS = frozenset({"primary", "trade", "aggregator"})
BEAT_PRIORITIES = frozenset({"high", "medium", "low"})
SOURCE_FIELDS = ("url", "publisher", "tier", "published_at")
COVERAGE_NOTE_FIELDS = ("angles_run", "entities_checked", "notes")

# Fields that only the manager may author. Their presence in findings means
# interpretation has leaked one stage upstream of where it belongs.
INTERPRETATION_FIELDS = ("thesis_impact", "research_angle", "so_what", "priority")

REQUIRED_TOP_LEVEL = (
    "beat", "run_id", "coverage_window", "quiet", "findings", "coverage_notes", "errors",
)


class FindingsInvalid(ValueError):
    """The seam validator's verdict. Carries every problem, not just the first,
    so one retry can fix everything rather than peeling an onion."""


def _check_source(source, where: str, problems: list[str]) -> None:
    if not isinstance(source, dict):
        problems.append(f"{where}: sources[] entries must be objects, not strings")
        return
    for field in SOURCE_FIELDS:
        if not source.get(field):
            problems.append(f"{where}: source missing required field {field!r}")
    tier = source.get("tier")
    if tier and tier not in SOURCE_TIERS:
        problems.append(f"{where}: source tier {tier!r} not in {sorted(SOURCE_TIERS)}")
    # paywalled must be present and boolean: it drives the paywalled_primary
    # advisory downstream, and a missing flag reads as "not paywalled" — which
    # silently turns an unassessable claim into a solid-looking one.
    if not isinstance(source.get("paywalled"), bool):
        problems.append(f"{where}: source 'paywalled' must be present and boolean")


def _check_finding(finding, index: int, known_entity_ids, problems: list[str]) -> None:
    where = f"findings[{index}]"

    if not isinstance(finding, dict):
        problems.append(f"{where}: must be an object")
        return

    for field in INTERPRETATION_FIELDS:
        if field in finding:
            problems.append(
                f"{where}: {field!r} is the manager's to author — researchers report facts"
            )

    if not finding.get("summary"):
        problems.append(f"{where}: summary is required")

    priority = finding.get("beat_priority")
    if priority not in BEAT_PRIORITIES:
        problems.append(
            f"{where}: beat_priority {priority!r} not in {sorted(BEAT_PRIORITIES)}"
        )

    sources = finding.get("sources")
    if not isinstance(sources, list) or not sources:
        # A finding with no source does not exist.
        problems.append(f"{where}: at least one entry in sources[] is required")
    else:
        for source_index, source in enumerate(sources):
            _check_source(source, f"{where}.sources[{source_index}]", problems)

    # The spine. A proposed_entity does NOT excuse a named ref: an off-roster
    # find carries entity_ids: [] and a proposal, so there is nothing to
    # resolve. Treating the proposal as a blanket exemption would let one
    # proposal smuggle any number of dangling references past the only check
    # guarding the spine.
    entity_ids = finding.get("entity_ids", [])
    if not isinstance(entity_ids, list):
        problems.append(f"{where}: entity_ids must be a list")
    else:
        for entity_id in entity_ids:
            if entity_id not in known_entity_ids:
                problems.append(
                    f"{where}: entity_id {entity_id!r} is not on the watchlist "
                    f"(off-roster finds carry entity_ids: [] and a proposed_entity)"
                )


def validate_findings(
    payload,
    *,
    beat_id: str,
    run_id: str,
    window: dict,
    known_entity_ids,
) -> None:
    """Raise FindingsInvalid describing everything wrong, or return quietly.

    The beat/run_id/window echo checks are cheap and catch a crossed fan-out —
    a researcher answering for the wrong beat is worse than a missing beat,
    because the result looks like coverage.
    """
    problems: list[str] = []

    if not isinstance(payload, dict):
        raise FindingsInvalid("payload must be a JSON object")

    for key in REQUIRED_TOP_LEVEL:
        if key not in payload:
            problems.append(f"missing required key {key!r}")

    if payload.get("beat") != beat_id:
        problems.append(f"beat {payload.get('beat')!r} does not match this beat ({beat_id!r})")

    if payload.get("run_id") != run_id:
        problems.append(f"run_id {payload.get('run_id')!r} does not match this run ({run_id!r})")

    if payload.get("coverage_window") != window:
        problems.append(f"coverage_window {payload.get('coverage_window')!r} does not match {window!r}")

    findings = payload.get("findings")
    if not isinstance(findings, list):
        problems.append("findings must be a list")
        findings = []

    # quiet:true is a claim about the world, and coverage_notes is what makes it
    # falsifiable. An honest quiet beat and a truncated one look identical
    # without it.
    #
    # The type check is load-bearing, not pedantry: `is True`/`is False` against
    # a string like "false" matches NEITHER, so both consistency checks below
    # would silently skip and the guard would vanish exactly when the payload is
    # already sloppy.
    quiet = payload.get("quiet")
    if not isinstance(quiet, bool):
        problems.append(f"quiet must be a boolean, got {type(quiet).__name__}")
    elif quiet and findings:
        problems.append("quiet is true but findings is non-empty")
    elif not quiet and not findings:
        problems.append("quiet is false but findings is empty")

    notes = payload.get("coverage_notes")
    if not isinstance(notes, dict):
        problems.append("coverage_notes is required, quiet or busy")
    else:
        # entities_checked carries the coverage duty: every high-priority roster
        # entity in scope must be checked and recorded either way. Unvalidated,
        # the one field proving the duty was honoured is the one that can be
        # quietly omitted.
        for field in COVERAGE_NOTE_FIELDS:
            if not notes.get(field):
                problems.append(f"coverage_notes.{field} must be present and non-empty")

    for index, finding in enumerate(findings):
        _check_finding(finding, index, known_entity_ids, problems)

    if problems:
        raise FindingsInvalid("; ".join(problems))


# ---------------------------------------------------------------------------
# The v2 findings contract — apertures, not beats.
#
# Additive alongside the v1 validator above: the pivot changed the SCOPE UNIT
# (an aperture, scoped to a program) and three field names with it, so a v2
# payload cannot be held to the v1 checks — `beat` is gone, `beat_priority`
# became the explicitly within-aperture `priority_hint`, and `angles_run` became
# `scope_run` (an aperture runs scope slices, not beat angles). Everything the
# two contracts SHARE — the source shape, the paywalled flag, the interpretation
# ban, the entity-id spine — is checked by the same helpers, so the two seams
# cannot drift on the rules that did not change.
#
# Shape: prompts/researcher-v2.md ("Output"), docs/spec/04-researchers.md.
# ---------------------------------------------------------------------------

# The within-aperture triage hint — the ONE ranking that crosses from researcher
# to manager, and it is explicitly not the published priority.
PRIORITY_HINTS = frozenset({"high", "medium", "low"})

REQUIRED_TOP_LEVEL_V2 = (
    "aperture", "program_id", "run_id", "coverage_window",
    "quiet", "findings", "coverage_notes", "errors",
)

COVERAGE_NOTE_FIELDS_V2 = ("scope_run", "entities_checked", "notes")

# The manager-only fields, spelled out (spec/04 "field rules"): researchers report
# FACTS, the manager authors INTERPRETATION — including the read-through and the
# typed relation. v1's INTERPRETATION_FIELDS is the same ban with a shorter list;
# v2 names the two the pivot added (`read_through`, `thesis_bearing`) plus
# `section`, because section placement is the manager's editorial call. The
# contract is shaped so there is no field these could ride in — this check is the
# enforcement, so a model that invents one is caught at the seam rather than
# quietly promoted into the digest.
INTERPRETATION_FIELDS_V2 = INTERPRETATION_FIELDS + (
    "read_through", "thesis_bearing", "section",
)

# The two house lenses. `house_lens` is a house_sweep-ONLY field: a biology or
# arena finding carrying one has mislabelled its own scope, which would land it in
# the wrong section of the digest.
HOUSE_LENSES = frozenset({"partnership_bd", "threat_financing"})

# Spelled literally rather than imported from `apertures` — this module is the
# bottom of the stack (the seam contract), and importing the planner to learn one
# string would point a dependency the wrong way. It is asserted against
# `apertures.HOUSE_SWEEP` in the tests, so the duplication cannot drift silently.
HOUSE_SWEEP_KIND = "house_sweep"


def _check_finding_v2(
    finding, index: int, known_entity_ids, problems: list[str], *, aperture_kind: str | None = None
) -> None:
    """One v2 finding. Same spine as `_check_finding`, one renamed triage field.

    Kept as a sibling rather than a flag on the v1 checker: the two differ only in
    the priority field's NAME, and a `if v2:` branch threaded through the v1
    checker would put the migration's temporary fork inside the one function both
    schemas depend on.
    """
    where = f"findings[{index}]"

    if not isinstance(finding, dict):
        problems.append(f"{where}: must be an object")
        return

    for field in INTERPRETATION_FIELDS_V2:
        if field in finding:
            problems.append(
                f"{where}: {field!r} is the manager's to author — researchers report facts"
            )

    if not finding.get("summary"):
        problems.append(f"{where}: summary is required")

    # house_lens is house_sweep's alone. `aperture_kind` is None only when a
    # caller validates a payload out of band (no aperture in hand), in which case
    # the value is still range-checked but not scope-checked.
    lens = finding.get("house_lens")
    if lens is not None:
        if lens not in HOUSE_LENSES:
            problems.append(f"{where}: house_lens {lens!r} not in {sorted(HOUSE_LENSES)}")
        elif aperture_kind is not None and aperture_kind != HOUSE_SWEEP_KIND:
            problems.append(
                f"{where}: house_lens is house_sweep-only, but this is a {aperture_kind} aperture"
            )

    hint = finding.get("priority_hint")
    if hint not in PRIORITY_HINTS:
        problems.append(
            f"{where}: priority_hint {hint!r} not in {sorted(PRIORITY_HINTS)}"
        )

    sources = finding.get("sources")
    if not isinstance(sources, list) or not sources:
        # A finding with no source does not exist.
        problems.append(f"{where}: at least one entry in sources[] is required")
    else:
        for source_index, source in enumerate(sources):
            _check_source(source, f"{where}.sources[{source_index}]", problems)

    # The spine, unchanged from v1: an off-roster find carries entity_ids: [] and
    # a proposed_entity, so a proposal never excuses a dangling reference.
    entity_ids = finding.get("entity_ids", [])
    if not isinstance(entity_ids, list):
        problems.append(f"{where}: entity_ids must be a list")
    else:
        for entity_id in entity_ids:
            if entity_id not in known_entity_ids:
                problems.append(
                    f"{where}: entity_id {entity_id!r} is not on the roster "
                    f"(off-roster finds carry entity_ids: [] and a proposed_entity)"
                )


def validate_findings_v2(
    payload,
    *,
    aperture_id: str,
    program_id: str,
    run_id: str,
    window: dict,
    known_entity_ids,
    aperture_kind: str | None = None,
) -> None:
    """Raise FindingsInvalid describing everything wrong, or return quietly.

    The v2 twin of `validate_findings`. The echo checks gain `program_id`: a run
    fans out over ONE program's apertures, so findings answering for another
    program are the multi-program version of v1's crossed fan-out — they look like
    coverage while covering the wrong subject.

    `aperture_kind` is optional so a payload can be checked without an `Aperture`
    in hand; supplying it turns on the one scope-dependent rule (house_lens is
    house_sweep's alone). The v2 stage always supplies it.
    """
    problems: list[str] = []

    if not isinstance(payload, dict):
        raise FindingsInvalid("payload must be a JSON object")

    for key in REQUIRED_TOP_LEVEL_V2:
        if key not in payload:
            problems.append(f"missing required key {key!r}")

    if payload.get("aperture") != aperture_id:
        problems.append(
            f"aperture {payload.get('aperture')!r} does not match this aperture ({aperture_id!r})"
        )

    if payload.get("program_id") != program_id:
        problems.append(
            f"program_id {payload.get('program_id')!r} does not match this program ({program_id!r})"
        )

    if payload.get("run_id") != run_id:
        problems.append(f"run_id {payload.get('run_id')!r} does not match this run ({run_id!r})")

    if payload.get("coverage_window") != window:
        problems.append(
            f"coverage_window {payload.get('coverage_window')!r} does not match {window!r}"
        )

    findings = payload.get("findings")
    if not isinstance(findings, list):
        problems.append("findings must be a list")
        findings = []

    # quiet:true is a claim about the world; coverage_notes is what makes it
    # falsifiable. Same reasoning as v1, including the `isinstance` guard — a
    # string "false" matches neither branch, so both consistency checks would
    # silently vanish exactly when the payload is already sloppy.
    quiet = payload.get("quiet")
    if not isinstance(quiet, bool):
        problems.append(f"quiet must be a boolean, got {type(quiet).__name__}")
    elif quiet and findings:
        problems.append("quiet is true but findings is non-empty")
    elif not quiet and not findings:
        problems.append("quiet is false but findings is empty")

    notes = payload.get("coverage_notes")
    if not isinstance(notes, dict):
        problems.append("coverage_notes is required, quiet or busy")
    else:
        for field in COVERAGE_NOTE_FIELDS_V2:
            if not notes.get(field):
                problems.append(f"coverage_notes.{field} must be present and non-empty")

    for index, finding in enumerate(findings):
        _check_finding_v2(
            finding, index, known_entity_ids, problems, aperture_kind=aperture_kind
        )

    if problems:
        raise FindingsInvalid("; ".join(problems))
