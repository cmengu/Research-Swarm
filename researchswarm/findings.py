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

import re
from dataclasses import dataclass
from dataclasses import field as _field

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

    A `dossier_scan` payload is a DIFFERENT SHAPE inside the same envelope, so the
    seam dispatches here rather than at the call site (spec #92: "dossier payloads
    validate at the same seam as every other aperture's output, so ONE contract
    governs all model output"). `window` is accepted and ignored for a dossier —
    the scan is window-exempt, and the exemption is CHECKED, not assumed.
    """
    if aperture_kind == DOSSIER_SCAN_KIND:
        validate_dossier_findings(
            payload,
            aperture_id=aperture_id,
            program_id=program_id,
            run_id=run_id,
            known_entity_ids=known_entity_ids,
        )
        return

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


# ---------------------------------------------------------------------------
# The dossier contract — a fourth aperture kind at the same seam.
#
# A `dossier_scan` answers "who is this company", not "what moved this window".
# It rides the v2 envelope (aperture / program_id / run_id / quiet /
# coverage_notes / errors) so it inherits fan-out, transport, retry, degradation
# and cost accounting unchanged — spec #92 "the dossier introduces no new stage
# and no new transport". What it adds is one payload key, `dossier`, and what it
# subtracts is the coverage window.
#
# Two rules carry this contract, and both are refusals:
#
# 1. **The manager-only ban, unchanged.** `read_through`, `thesis_bearing`,
#    `priority`, `section` are the manager's, exactly as for the other three
#    kinds, and are rejected with the same words.
#
# 2. **The interpretation ban, which is new here and is the point.** A dossier is
#    SHARED ACROSS PROGRAMS; a read-through is not. A researcher writing what a
#    company MEANS — its threat level, its competitive position, what we should
#    do about it — is not merely early, it is authoring an opinion that a second
#    program would silently inherit as if it were a fact. That is the same
#    contract breach as a leaked read-through wearing a different noun, so it is
#    refused in the same voice.
#
# Shape: spec #92 "The dossier record shape". Spec: docs/spec/04-researchers.md,
# docs/spec/03-state-and-governance.md (facts lift, read-throughs stay).
# ---------------------------------------------------------------------------

# Spelled literally for the same reason as HOUSE_SWEEP_KIND: this module is the
# bottom of the stack, and importing the planner to learn one string would point a
# dependency the wrong way. Asserted against `apertures.DOSSIER_SCAN` in the tests.
DOSSIER_SCAN_KIND = "dossier_scan"

# A dossier is a company record, never an asset one — spec #92 keeps a molecule's
# clinical facts and a company's corporate facts from being conflated.
DOSSIER_ENTITY_KIND = "company"

# `as_of` is what separates fresh intelligence from a stale record (story 15), so
# it must be a date a staleness check can actually compare, not prose.
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REQUIRED_TOP_LEVEL_DOSSIER = (
    "aperture", "program_id", "run_id", "coverage_window",
    "quiet", "findings", "dossier", "coverage_notes", "errors",
)

# The scalar spine of the record itself.
REQUIRED_DOSSIER_KEYS = ("entity_id", "kind", "as_of")

# Interpretation, banned at EVERY depth of the dossier.
#
# These are not hypothetical field names. They are the shapes an opinion takes
# when it is not allowed to be called `read_through`: a "threat_level" on a
# funding round, a "so_what" on a pivot, an "implication" on a setback. The
# dossier's whole value is that a second program can inherit its facts without
# inheriting the first program's opinions (story 18), and that property is worth
# exactly as much as this list is complete.
DOSSIER_INTERPRETATION_FIELDS = (
    "implication", "implications", "what_it_means", "meaning",
    "significance", "assessment", "analysis", "interpretation",
    "threat_level", "threat_assessment", "competitive_position",
    "competitive_threat", "opportunity", "risk_to_program",
    "recommendation", "recommended_action", "outlook", "verdict",
    "takeaway", "our_view", "impact_on_program",
)


@dataclass(frozen=True)
class DossierSection:
    """One row of the declarative shape table.

    Declarative rather than hand-written because hand-written checks were found to
    cover roughly a third of this repo's schema (spec #92 "The dossier shape joins
    the declarative shape table, so its gate coverage cannot drift from its
    contract"). A new dossier field is one row here, and it is gated the moment it
    is named — there is no second place to remember to update.
    """

    name: str
    container: str                       # "object" | "array"
    required: bool = False
    provenance: bool = True              # every fact cites the run that made it
    required_fields: tuple[str, ...] = ()
    enums: dict = _field(default_factory=dict)


# The shape table (spec #92 "The dossier record shape").
#
# `pivots` and `setbacks` are REQUIRED while richer sections are not, which looks
# backwards until you read why they exist: they are the fields no vendor sells —
# what a company SAID it would do versus what it then did — and a section that is
# optional is a section a model will quietly skip. Required-but-empty is a claim
# ("we looked, there were none"); absent is silence. The receipt rule needs the
# difference. `coverage` is required for the same reason and is the one section
# exempt from provenance: thin-section marking is self-assessment about the scan,
# not a sourced fact about the company.
DOSSIER_SECTIONS = (
    DossierSection(
        "identity", "object", required=True,
        required_fields=("legal_name",),
        enums={"status": frozenset({"public", "private", "subsidiary"})},
    ),
    DossierSection("origin", "object"),
    DossierSection("funding", "object"),
    DossierSection("pipeline", "array"),
    DossierSection(
        "deals", "array",
        enums={
            "type": frozenset({"license", "option", "M&A", "collab"}),
            "direction": frozenset({"in", "out"}),
        },
    ),
    DossierSection("people", "array"),
    DossierSection("pivots", "array", required=True),
    DossierSection(
        "setbacks", "array", required=True,
        enums={
            "kind": frozenset({
                "clinical_hold", "discontinuation", "CRL",
                "layoff", "restructuring", "delisting",
            })
        },
    ),
    DossierSection("coverage", "object", required=True, provenance=False),
)

# Guard on the recursive interpretation walk. A model can emit arbitrarily nested
# JSON, and a gate that blows the stack on it is exactly the failure mode this
# contract exists to prevent.
_MAX_WALK_DEPTH = 12


def _check_banned_fields(node, where: str, problems: list[str], depth: int = 0) -> None:
    """Walk a dossier subtree refusing manager-only and interpretive keys.

    Recursive because interpretation hides at depth: the read-through a gate
    catches at the top level reappears as `rounds[2].what_it_means`. Depth-capped
    and type-guarded because this walk runs over adversarial input by definition —
    the node may be null, a string, a list of lists, or a dict nested past any
    sane limit, and none of those may raise.
    """
    if depth > _MAX_WALK_DEPTH:
        problems.append(f"{where}: nested deeper than {_MAX_WALK_DEPTH} levels")
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                continue
            if key in INTERPRETATION_FIELDS_V2:
                problems.append(
                    f"{where}.{key}: {key!r} is the manager's to author — "
                    "researchers report facts"
                )
            elif key in DOSSIER_INTERPRETATION_FIELDS:
                problems.append(
                    f"{where}.{key}: {key!r} is the manager's to author — a dossier "
                    "holds facts, and what a company MEANS for a program lives on "
                    "the program's relation edge, not on a record every program shares"
                )
            _check_banned_fields(value, f"{where}.{key}", problems, depth + 1)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _check_banned_fields(item, f"{where}[{index}]", problems, depth + 1)


def _check_provenance(entry, where: str, run_id: str, problems: list[str]) -> None:
    """Every dossier fact cites the run that established it and its source.

    Per-record rather than per-run because a dossier accumulates: a refresh
    appends, and without a per-fact citation the reader cannot tell which claim
    came from which scan (story 13/14, and spec/03 "every factual field cites the
    run_id/issue that established it"). `established_by` must echo THIS run: a
    researcher is reporting what it just found, and a model naming some other run
    is inventing a provenance chain rather than reporting one.
    """
    if not isinstance(entry, dict):
        return                                   # already reported as a shape problem
    provenance = entry.get("provenance")
    if not isinstance(provenance, dict):
        problems.append(
            f"{where}: provenance is required — every dossier fact cites the run "
            "that established it and its source"
        )
        return

    established_by = provenance.get("established_by")
    if established_by != run_id:
        problems.append(
            f"{where}: provenance.established_by {established_by!r} does not match "
            f"this run ({run_id!r})"
        )

    sources = provenance.get("sources")
    if not isinstance(sources, list) or not sources:
        # Same rule as a finding: a claim with no source does not exist. Spec #92
        # story 29 — an unsourceable dossier claim is OMITTED with a receipt, not
        # carried bare.
        problems.append(f"{where}: at least one entry in provenance.sources[] is required")
    else:
        for index, source in enumerate(sources):
            _check_source(source, f"{where}.provenance.sources[{index}]", problems)


def _check_enums(entry, where: str, enums: dict, problems: list[str]) -> None:
    """Range-check the table's enums, and ONLY when the field is present.

    Absent is legal everywhere in a dossier: the ternary receipt rule says an
    unsourceable claim is omitted, so a missing `status` is honest reporting while
    a `status` of "probably public" is a broken contract.
    """
    if not isinstance(entry, dict):
        return
    for field_name, allowed in enums.items():
        value = entry.get(field_name)
        if value is None:
            continue
        if value not in allowed:
            problems.append(
                f"{where}: {field_name} {value!r} not in {sorted(allowed)}"
            )


def _check_section(dossier: dict, section: DossierSection, run_id: str, problems: list[str]) -> None:
    """One shape-table row against the payload. Never raises."""
    where = f"dossier.{section.name}"
    value = dossier.get(section.name)

    if value is None:
        if section.required:
            problems.append(
                f"{where} is required — an absent section and an empty one are "
                "different claims (unmeasured vs nothing there)"
            )
        return

    if section.container == "object":
        if not isinstance(value, dict):
            problems.append(
                f"{where}: must be an object, got {type(value).__name__}"
            )
            return
        for field_name in section.required_fields:
            if not value.get(field_name):
                problems.append(f"{where}: {field_name} is required")
        _check_enums(value, where, section.enums, problems)
        if section.provenance:
            _check_provenance(value, where, run_id, problems)
        return

    # array
    if not isinstance(value, list):
        problems.append(f"{where}: must be a list, got {type(value).__name__}")
        return
    for index, entry in enumerate(value):
        entry_where = f"{where}[{index}]"
        if not isinstance(entry, dict):
            problems.append(
                f"{entry_where}: entries must be objects, got {type(entry).__name__}"
            )
            continue
        for field_name in section.required_fields:
            if not entry.get(field_name):
                problems.append(f"{entry_where}: {field_name} is required")
        _check_enums(entry, entry_where, section.enums, problems)
        if section.provenance:
            _check_provenance(entry, entry_where, run_id, problems)


def dossier_subject(aperture_id: str) -> str | None:
    """The entity_id a `dossier_scan:<entity_id>` aperture is about, or None.

    Total on garbage input — an aperture id that is not a dossier id simply has no
    subject, which is a fact about it, not an error.
    """
    if not isinstance(aperture_id, str):
        return None
    prefix = f"{DOSSIER_SCAN_KIND}:"
    if not aperture_id.startswith(prefix):
        return None
    return aperture_id[len(prefix):] or None


def validate_dossier_findings(
    payload,
    *,
    aperture_id: str,
    program_id: str,
    run_id: str,
    known_entity_ids,
) -> None:
    """Raise FindingsInvalid describing everything wrong, or return quietly.

    The third sibling of `validate_findings` / `validate_findings_v2`, reached
    through the same seam (`validate_findings_v2` dispatches on the aperture kind).
    Kept as a sibling for the reason the v2 checker was: the shapes differ enough
    that a flag threaded through the v2 validator would put a fork inside the one
    function every aperture depends on.

    Three things are different from a v2 payload, all load-bearing:

    * **The window is exempt, explicitly.** `coverage_window` must be present and
      NULL. Requiring the key while forbidding a value makes the exemption a
      declared fact in the payload rather than an omission that reads the same as
      a model that forgot — spec #92 wants the exemption explicit, and the reason
      is the seven-day window that once discarded a $1.1B acquisition.
    * **`dossier` carries the answer.** `findings[]` still validates (a dossier
      scan may legitimately surface a datable event while reading history), but
      the record is the product.
    * **Interpretation is refused**, not just deferred. See the module note above.

    NEVER RAISES ANYTHING BUT `FindingsInvalid`. A gate that crashes is strictly
    worse than one that misses, because it takes the run down AFTER publishing;
    this repo has shipped that bug five times, so the dossier walk is belt-and-
    braces — every branch type-guards, and the whole record check is additionally
    wrapped so that an input shape nobody imagined becomes a finding, not a
    traceback.
    """
    problems: list[str] = []

    if not isinstance(payload, dict):
        raise FindingsInvalid("payload must be a JSON object")

    for key in REQUIRED_TOP_LEVEL_DOSSIER:
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

    # The window exemption, stated in the payload rather than inferred.
    if "coverage_window" in payload and payload.get("coverage_window") is not None:
        problems.append(
            f"coverage_window {payload.get('coverage_window')!r} must be null — a "
            "dossier_scan is window-exempt because its subject is history"
        )

    findings = payload.get("findings")
    if not isinstance(findings, list):
        problems.append("findings must be a list")
        findings = []

    notes = payload.get("coverage_notes")
    if not isinstance(notes, dict):
        problems.append("coverage_notes is required, quiet or busy")
    else:
        for field_name in COVERAGE_NOTE_FIELDS_V2:
            if not notes.get(field_name):
                problems.append(f"coverage_notes.{field_name} must be present and non-empty")

    dossier = payload.get("dossier")
    if dossier is not None and not isinstance(dossier, dict):
        problems.append(f"dossier must be an object, got {type(dossier).__name__}")
        dossier = None

    # quiet:true is a claim about the world, and here the world includes the
    # record. A scan that RETURNED NOTHING must be distinguishable from one that
    # did not run (spec #92 story 38), so quiet is checked against the dossier and
    # the findings together — a populated dossier under quiet:true is the
    # ambiguity the story exists to forbid.
    quiet = payload.get("quiet")
    if not isinstance(quiet, bool):
        problems.append(f"quiet must be a boolean, got {type(quiet).__name__}")
    elif quiet and (findings or dossier):
        problems.append("quiet is true but the scan returned a dossier or findings")
    elif not quiet and not findings and not dossier:
        problems.append("quiet is false but the scan returned nothing")

    if dossier is not None:
        try:
            _check_dossier_record(
                dossier, aperture_id=aperture_id, run_id=run_id, problems=problems
            )
        except Exception as exc:                        # pragma: no cover - belt and braces
            problems.append(f"dossier: unreadable ({type(exc).__name__}: {exc})")

    for index, finding in enumerate(findings):
        _check_finding_v2(
            finding, index, known_entity_ids, problems, aperture_kind=DOSSIER_SCAN_KIND
        )
        # The interpretation ban reaches a dossier scan's findings too — otherwise
        # the opinion the record refuses simply moves next door.
        _check_banned_fields(finding, f"findings[{index}]", problems)

    if problems:
        raise FindingsInvalid("; ".join(problems))


def _check_dossier_record(dossier: dict, *, aperture_id: str, run_id: str, problems: list[str]) -> None:
    """The record itself: spine, freshness, the shape table, the interpretation ban."""
    for key in REQUIRED_DOSSIER_KEYS:
        if not dossier.get(key):
            problems.append(f"dossier: missing required key {key!r}")

    # A dossier of the wrong company is worse than a missing one: it looks like
    # coverage. The aperture names its subject, so the echo is checkable.
    subject = dossier_subject(aperture_id)
    entity_id = dossier.get("entity_id")
    if subject and entity_id and entity_id != subject:
        problems.append(
            f"dossier: entity_id {entity_id!r} is not this aperture's subject ({subject!r})"
        )

    kind = dossier.get("kind")
    if kind is not None and kind != DOSSIER_ENTITY_KIND:
        problems.append(
            f"dossier: kind {kind!r} must be {DOSSIER_ENTITY_KIND!r} — a dossier is a "
            "company record, never an asset one"
        )

    as_of = dossier.get("as_of")
    if as_of is not None and not (isinstance(as_of, str) and ISO_DATE.match(as_of)):
        problems.append(f"dossier: as_of {as_of!r} must be an ISO date (YYYY-MM-DD)")

    for section in DOSSIER_SECTIONS:
        _check_section(dossier, section, run_id, problems)

    # Thin sections are the China-coverage gap made visible at the point of the
    # absence (spec #92): a sparse dossier must read as unmeasured, not as a small
    # company. The list may be empty; it may not be a string pretending to be one.
    coverage = dossier.get("coverage")
    if isinstance(coverage, dict):
        thin = coverage.get("thin_sections", [])
        if not isinstance(thin, list):
            problems.append(
                f"dossier.coverage.thin_sections must be a list, got {type(thin).__name__}"
            )
        else:
            known = {section.name for section in DOSSIER_SECTIONS}
            for name in thin:
                if name not in known:
                    problems.append(
                        f"dossier.coverage.thin_sections: {name!r} is not a dossier section "
                        f"({sorted(known)})"
                    )

    _check_banned_fields(dossier, "dossier", problems)
