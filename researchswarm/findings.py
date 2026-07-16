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
