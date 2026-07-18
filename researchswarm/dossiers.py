"""The company dossier — the deep, accumulating record behind a competitor.

WHY this module exists at all. An entity record today holds whatever facts one
issue happened to establish: a name, a type, a status, a summary ([03] "the
shared fact layer"). That is enough to type a competitor and argue a read-through
about a single event, and not enough to answer the question the program owner
actually asks — *who are these people, where did they come from, what have they
already abandoned*. Worse, the loop rediscovers the same background every cycle,
so a competitor's third sighting arrives with no more history than its first.
The dossier is the store that makes understanding compound (#92).

Four rules govern everything below, and each one is load-bearing:

1. **A dossier holds FACTS ONLY.** [03] splits global facts (they lift to
   `state/entities/`) from per-program interpretation (it stays on the relation
   edge in `state/programs/<id>/edges.json`). A dossier is *shared across
   programs*; an opinion is not. So `read_through`, `priority`, `thesis_bearing`
   and `so_what` are refused here — see `INTERPRETIVE_FIELDS`. If a second
   program inherited the first program's opinions the split would be dead.

2. **Provenance is per FIELD, not per record.** Each section is stored as
   `{value, established_by, issue}` — exactly the shape the existing entity-fact
   writer uses (`state_edits.ENTITY_FACT_FIELDS_V2`), because an auditor should
   not have to learn a second provenance shape to read a second file.

3. **Corrections APPEND.** A changed section rewrites the fact *and* appends a
   `drift_log` entry recording from/to and the run ([03] clause 5: "a competitor
   record's factual fields correct by appending, never overwriting"). The prior
   belief survives in the record itself, not only in git.

4. **Thin is visible.** A dossier assembled from partial sources marks which
   sections are thin, at the point of the absence (#92 story 27, and the China
   coverage decision). Several of the most important competitors are China-listed
   — the system's rank-1 blind spot — and a sparse dossier must read as
   *unmeasured*, never as *a small company*. `coverage.thin_sections[]` is
   recomputed on every write so the marker can never go stale relative to the
   facts it describes.

WHY the shapes are dataclasses. The schema block in #92 is the contract; these
dataclasses are that contract made executable, so a reader of the code and a
reader of the spec see the same object. Every one of them parses through
`from_payload`, which is **total**: it accepts null, prose where a dict belongs,
a dict where a list belongs, a list of strings where a list of objects belongs,
and arbitrary nesting depth, and it returns a well-formed value for all of them.
That totality is not defensiveness for its own sake — a gate that crashes is
strictly worse than one that misses, because it takes the run down *after*
publishing. This has shipped as a bug five times in this repo.

WHY this module never writes outside a run and never calls git: `run.py` is the
sole machine writer ([03] clause 1). The pure builders here
(`build_company_dossier_record`, `build_asset_company_link`) do no IO at all; the
two `apply_*` functions do exactly what the existing `state_edits` writers do —
return `(path, changed)` and rewrite the file only when `changed`, so a quiet
cycle stages nothing and the diff is exactly the edit.

Spec: docs/spec/03-state-and-governance.md, docs/spec/04-researchers.md,
      https://github.com/cmengu/Research-Swarm/issues/92
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from researchswarm.state_edits import write_json

log = logging.getLogger("researchswarm.dossiers")

# The store splits by kind (#92 "The entity store splits by kind"). A molecule
# and a company are different objects with different fields, and today they are
# undifferentiated in state/entities/. Companies get their own subdirectory so a
# dossier is never confused with an asset's clinical record (story 30), and
# assets get theirs so the traversal in story 31 has a place to land.
COMPANIES_DIRNAME = "companies"
ASSETS_DIRNAME = "assets"

DOSSIER_KIND = "company"

#: The eight fact sections of a dossier, in the order #92's schema block states
#: them. This tuple IS the field table: the writer iterates it, the thin-section
#: marker iterates it, and the tests assert against it, so gate coverage cannot
#: drift from the contract by someone adding a section in one place only.
DOSSIER_SECTIONS: tuple[str, ...] = (
    "identity",
    "origin",
    "funding",
    "pipeline",
    "deals",
    "people",
    "pivots",
    "setbacks",
)

#: Sections that are lists of records rather than a single object. Split out
#: because "thin" means something different for each: an empty list is thin, and
#: so is an object whose every field is blank.
DOSSIER_LIST_SECTIONS: frozenset[str] = frozenset({"pipeline", "deals", "people", "pivots", "setbacks"})

#: Interpretation. Program-relative, therefore banned from the entity layer
#: ([03], #92 "A dossier holds facts only"). Checked at the top level AND one
#: level down inside every section, because the realistic leak is a model
#: attaching a read_through to a single setback rather than to the record.
INTERPRETIVE_FIELDS: tuple[str, ...] = (
    "read_through",
    "priority",
    "thesis_bearing",
    "so_what",
    "relation",
    "recommendation",
)

# Closed vocabularies from #92's schema block. Unknown values are NOT coerced to
# a default — they are dropped to None, so a hallucinated status reads as absent
# rather than as a confident wrong answer.
IDENTITY_STATUSES = ("public", "private", "subsidiary")
DEAL_TYPES = ("license", "option", "M&A", "collab")
DEAL_DIRECTIONS = ("in", "out")
SETBACK_KINDS = (
    "clinical_hold",
    "discontinuation",
    "CRL",
    "layoff",
    "restructuring",
    "delisting",
)


# ---------------------------------------------------------------------------
# Total coercion helpers
#
# Every one of these takes `Any` and returns a well-formed value. They are the
# reason no gate in this module can crash: parsing happens exactly once, here,
# and nothing downstream ever indexes a value it has not been handed by one of
# them.
# ---------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    """A string field, or None. Non-strings are dropped, not stringified.

    Dropping rather than coercing is deliberate: `str({"amount": 4})` would
    produce a plausible-looking field that no human wrote and no source states.
    An absent field is honest; a manufactured one is not.
    """
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _scalar(value: Any) -> Any:
    """A money/number-ish field: string, int or float pass; everything else drops.

    Amounts arrive as both `"$1.1B"` and `1100000000` in real filings, so this
    stays deliberately permissive about type while still refusing containers.
    Booleans are refused because `True` as an upfront payment is a parse error
    wearing a number's clothes.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return _text(value)


def _enum(value: Any, allowed: tuple[str, ...]) -> str | None:
    """A closed-vocabulary field. Anything off the list becomes None."""
    text = _text(value)
    return text if text in allowed else None


def _str_list(value: Any) -> list[str]:
    """A list of strings, from a list, a bare string, or nothing.

    A bare string is promoted to a one-element list because models routinely
    emit `"founders": "Jane Doe"` for a single-founder company, and refusing
    that would lose a real fact to a container mistake. Non-string members are
    dropped individually rather than failing the whole list — partial recovery
    beats total loss, and the thin-section marker will show what survived.
    """
    if isinstance(value, str):
        text = _text(value)
        return [text] if text else []
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = _text(item)
        if text:
            out.append(text)
    return out


def _mapping(value: Any) -> dict:
    """A dict, or an empty dict. Prose where an object belongs becomes absence."""
    return value if isinstance(value, dict) else {}


def _records(value: Any, parse) -> list:
    """A list of parsed records, tolerating every container mistake seen so far.

    A single dict is promoted to a one-element list (the model emitted one round
    and forgot the array). Non-dict members are dropped. A scalar or None yields
    an empty list. `parse` is one of the `from_payload` classmethods below, each
    of which is itself total, so this cannot raise regardless of nesting depth.
    """
    if isinstance(value, dict):
        return [parse(value)]
    if not isinstance(value, list):
        return []
    return [parse(item) for item in value if isinstance(item, dict)]


def _prune(data: dict) -> dict:
    """Drop None / empty-container members so absence is absence, not clutter.

    A dossier is read by a human auditing a claim. Twenty `null`s around one
    real fact make the real fact harder to find, and a persisted `null` is
    indistinguishable in a diff from a fact we later deleted.
    """
    return {k: v for k, v in data.items() if v not in (None, [], {}, "")}


def _log(value: Any) -> list:
    """A prior append-only log, or a fresh one.

    Deliberately NOT `list(value or [])`: a corrupt record whose `drift_log` is a
    string would silently become a list of single characters, and the next
    append would produce a log no reader could parse. A prior log we cannot
    trust is discarded — git still holds it — rather than half-adopted.
    """
    return list(value) if isinstance(value, list) else []


def _is_blank(value: Any) -> bool:
    """Whether a section carries nothing a reader could act on.

    Recursive, because `{"identity": {"legal_name": null, "aliases": []}}` is a
    section that *exists* and says nothing — exactly the case story 27 is about.
    Treating it as populated would render a thin dossier as a complete one.
    """
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, dict):
        return all(_is_blank(v) for v in value.values())
    if isinstance(value, list):
        return all(_is_blank(v) for v in value)
    return False


# ---------------------------------------------------------------------------
# The record shapes — #92's schema block, made executable
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Listing:
    """One exchange listing. Plural because HKEX + a US ADR is one company."""

    exchange: str | None = None
    ticker: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> "Listing":
        raw = _mapping(raw)
        return cls(exchange=_text(raw.get("exchange")), ticker=_text(raw.get("ticker")))

    def to_dict(self) -> dict:
        return _prune({"exchange": self.exchange, "ticker": self.ticker})


@dataclass(frozen=True)
class Identity:
    """Who the company legally is. Table stakes — a vendor sells this."""

    legal_name: str | None = None
    aliases: list[str] = field(default_factory=list)
    founded: str | None = None
    hq: str | None = None
    status: str | None = None
    listings: list[Listing] = field(default_factory=list)

    @classmethod
    def from_payload(cls, raw: Any) -> "Identity":
        raw = _mapping(raw)
        return cls(
            legal_name=_text(raw.get("legal_name")),
            aliases=_str_list(raw.get("aliases")),
            founded=_text(raw.get("founded")),
            hq=_text(raw.get("hq")),
            status=_enum(raw.get("status"), IDENTITY_STATUSES),
            listings=_records(raw.get("listings"), Listing.from_payload),
        )

    def to_dict(self) -> dict:
        return _prune({
            "legal_name": self.legal_name,
            "aliases": self.aliases,
            "founded": self.founded,
            "hq": self.hq,
            "status": self.status,
            "listings": [x.to_dict() for x in self.listings if x.to_dict()],
        })


@dataclass(frozen=True)
class Origin:
    """Where the company came from — story 3: platform spin-out vs repurposed shell.

    `founding_thesis` is the anchor half of the pivot argument: it is what they
    said they were for, recorded at the time, so a later deviation is measurable
    rather than remembered (story 5).
    """

    founding_story: str | None = None
    founders: list[str] = field(default_factory=list)
    spun_out_of: str | None = None
    founding_thesis: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> "Origin":
        raw = _mapping(raw)
        return cls(
            founding_story=_text(raw.get("founding_story")),
            founders=_str_list(raw.get("founders")),
            spun_out_of=_text(raw.get("spun_out_of")),
            founding_thesis=_text(raw.get("founding_thesis")),
        )

    def to_dict(self) -> dict:
        return _prune({
            "founding_story": self.founding_story,
            "founders": self.founders,
            "spun_out_of": self.spun_out_of,
            "founding_thesis": self.founding_thesis,
        })


@dataclass(frozen=True)
class FundingRound:
    """One financing. Dates + amounts + leads are how runway gets estimated (story 4)."""

    date: str | None = None
    stage: str | None = None
    amount: Any = None
    currency: str | None = None
    lead: str | None = None
    investors: list[str] = field(default_factory=list)
    pre_money: Any = None
    post_money: Any = None

    @classmethod
    def from_payload(cls, raw: Any) -> "FundingRound":
        raw = _mapping(raw)
        return cls(
            date=_text(raw.get("date")),
            stage=_text(raw.get("stage")),
            amount=_scalar(raw.get("amount")),
            currency=_text(raw.get("currency")),
            lead=_text(raw.get("lead")),
            investors=_str_list(raw.get("investors")),
            pre_money=_scalar(raw.get("pre_money")),
            post_money=_scalar(raw.get("post_money")),
        )

    def to_dict(self) -> dict:
        return _prune({
            "date": self.date,
            "stage": self.stage,
            "amount": self.amount,
            "currency": self.currency,
            "lead": self.lead,
            "investors": self.investors,
            "pre_money": self.pre_money,
            "post_money": self.post_money,
        })


@dataclass(frozen=True)
class Ipo:
    """The listing event, kept separate from `rounds[]` because it is not a round."""

    date: str | None = None
    exchange: str | None = None
    raised: Any = None
    price: Any = None

    @classmethod
    def from_payload(cls, raw: Any) -> "Ipo":
        raw = _mapping(raw)
        return cls(
            date=_text(raw.get("date")),
            exchange=_text(raw.get("exchange")),
            raised=_scalar(raw.get("raised")),
            price=_scalar(raw.get("price")),
        )

    def to_dict(self) -> dict:
        return _prune({
            "date": self.date,
            "exchange": self.exchange,
            "raised": self.raised,
            "price": self.price,
        })


@dataclass(frozen=True)
class Funding:
    """The financing history — story 4, and the pressure-to-deliver read."""

    total_raised: Any = None
    rounds: list[FundingRound] = field(default_factory=list)
    ipo: Ipo | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> "Funding":
        raw = _mapping(raw)
        ipo = Ipo.from_payload(raw.get("ipo")) if isinstance(raw.get("ipo"), dict) else None
        return cls(
            total_raised=_scalar(raw.get("total_raised")),
            rounds=_records(raw.get("rounds"), FundingRound.from_payload),
            ipo=ipo,
        )

    def to_dict(self) -> dict:
        return _prune({
            "total_raised": self.total_raised,
            "rounds": [x.to_dict() for x in self.rounds if x.to_dict()],
            "ipo": self.ipo.to_dict() if self.ipo else {},
        })


@dataclass(frozen=True)
class PipelineEntry:
    """One asset in the company's pipeline — story 32: what else would they fund?

    `asset_entity_id` is the traversal handle onto the `entity_id` spine ([03]),
    the other half of the asset->company link built by
    `build_asset_company_link`.
    """

    asset_entity_id: str | None = None
    indication: str | None = None
    phase: str | None = None
    status: str | None = None
    first_disclosed: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> "PipelineEntry":
        raw = _mapping(raw)
        return cls(
            asset_entity_id=_text(raw.get("asset_entity_id")),
            indication=_text(raw.get("indication")),
            phase=_text(raw.get("phase")),
            status=_text(raw.get("status")),
            first_disclosed=_text(raw.get("first_disclosed")),
        )

    def to_dict(self) -> dict:
        return _prune({
            "asset_entity_id": self.asset_entity_id,
            "indication": self.indication,
            "phase": self.phase,
            "status": self.status,
            "first_disclosed": self.first_disclosed,
        })


@dataclass(frozen=True)
class Deal:
    """One transaction. `direction` carries story 10: their science or their chequebook."""

    date: str | None = None
    type: str | None = None
    counterparty: str | None = None
    direction: str | None = None
    upfront: Any = None
    milestones: Any = None
    royalty: Any = None
    territory: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> "Deal":
        raw = _mapping(raw)
        return cls(
            date=_text(raw.get("date")),
            type=_enum(raw.get("type"), DEAL_TYPES),
            counterparty=_text(raw.get("counterparty")),
            direction=_enum(raw.get("direction"), DEAL_DIRECTIONS),
            upfront=_scalar(raw.get("upfront")),
            milestones=_scalar(raw.get("milestones")),
            royalty=_scalar(raw.get("royalty")),
            territory=_text(raw.get("territory")),
        )

    def to_dict(self) -> dict:
        return _prune({
            "date": self.date,
            "type": self.type,
            "counterparty": self.counterparty,
            "direction": self.direction,
            "upfront": self.upfront,
            "milestones": self.milestones,
            "royalty": self.royalty,
            "territory": self.territory,
        })


@dataclass(frozen=True)
class Person:
    """A key person. `departure_signal` is story 12 — the early read on trouble.

    It is a FACT ("named successor", "no successor named", "resigned effective
    immediately"), not a judgement about what the departure means. The judgement
    is a read-through and belongs on the program edge.
    """

    name: str | None = None
    role: str | None = None
    since: str | None = None
    until: str | None = None
    prior: list[str] = field(default_factory=list)
    departure_signal: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> "Person":
        raw = _mapping(raw)
        return cls(
            name=_text(raw.get("name")),
            role=_text(raw.get("role")),
            since=_text(raw.get("since")),
            until=_text(raw.get("until")),
            prior=_str_list(raw.get("prior")),
            departure_signal=_text(raw.get("departure_signal")),
        )

    def to_dict(self) -> dict:
        return _prune({
            "name": self.name,
            "role": self.role,
            "since": self.since,
            "until": self.until,
            "prior": self.prior,
            "departure_signal": self.departure_signal,
        })


@dataclass(frozen=True)
class Pivot:
    """A change of direction — one of the two differentiated sections (#92).

    `from`/`to`/`trigger` is strategy-versus-execution, which no vendor sells
    because it is an argument assembled over time rather than a row in a
    database. Stored as `from_`/`to_` in Python (both are keywords or too close
    to one) and serialized back to `from`/`to` to match the spec's schema block
    exactly — the JSON is the contract, not the attribute name.
    """

    date: str | None = None
    from_: str | None = None
    to_: str | None = None
    trigger: str | None = None
    evidence: list[str] = field(default_factory=list)
    outcome: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> "Pivot":
        raw = _mapping(raw)
        return cls(
            date=_text(raw.get("date")),
            from_=_text(raw.get("from")),
            to_=_text(raw.get("to")),
            trigger=_text(raw.get("trigger")),
            evidence=_str_list(raw.get("evidence")),
            outcome=_text(raw.get("outcome")),
        )

    def to_dict(self) -> dict:
        return _prune({
            "date": self.date,
            "from": self.from_,
            "to": self.to_,
            "trigger": self.trigger,
            "evidence": self.evidence,
            "outcome": self.outcome,
        })


@dataclass(frozen=True)
class Setback:
    """A failure event — the second differentiated section (#92, story 7).

    Recorded individually so the reader can see a *pattern* rather than a single
    failure, and so story 8 works: an asset abandoned in one indication makes a
    competitor's later silence legible as retreat rather than as quiet.
    """

    date: str | None = None
    kind: str | None = None
    detail: str | None = None
    program: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> "Setback":
        raw = _mapping(raw)
        return cls(
            date=_text(raw.get("date")),
            kind=_enum(raw.get("kind"), SETBACK_KINDS),
            detail=_text(raw.get("detail")),
            program=_text(raw.get("program")),
        )

    def to_dict(self) -> dict:
        return _prune({
            "date": self.date,
            "kind": self.kind,
            "detail": self.detail,
            "program": self.program,
        })


@dataclass(frozen=True)
class Coverage:
    """How complete this dossier is — the honesty layer (#92 story 27).

    `thin_sections[]` is DERIVED, never taken from the payload: a model that
    assembled a sparse dossier is the last thing that should get to grade its own
    completeness. `degradation` is the free-text receipt from the scan (cap hit,
    HKEX unreachable, scan dormant) and IS taken from the payload, because only
    the scan knows why it came back short.
    """

    thin_sections: list[str] = field(default_factory=list)
    degradation: str | None = None

    def to_dict(self) -> dict:
        # thin_sections is always emitted, even when empty: an empty list is the
        # positive claim "we looked and nothing is thin", which is different from
        # the absence of the key ("nobody computed this"). Story 38's
        # distinction, applied to the record instead of the scan. `degradation`
        # is pruned when absent, because there a missing key and a null mean the
        # same thing — the scan had nothing to confess.
        out: dict = {"thin_sections": list(self.thin_sections)}
        if self.degradation:
            out["degradation"] = self.degradation
        return out


# ---------------------------------------------------------------------------
# Parsing a whole payload
# ---------------------------------------------------------------------------

_SECTION_PARSERS = {
    "identity": lambda raw: Identity.from_payload(raw).to_dict(),
    "origin": lambda raw: Origin.from_payload(raw).to_dict(),
    "funding": lambda raw: Funding.from_payload(raw).to_dict(),
    "pipeline": lambda raw: [x.to_dict() for x in _records(raw, PipelineEntry.from_payload) if x.to_dict()],
    "deals": lambda raw: [x.to_dict() for x in _records(raw, Deal.from_payload) if x.to_dict()],
    "people": lambda raw: [x.to_dict() for x in _records(raw, Person.from_payload) if x.to_dict()],
    "pivots": lambda raw: [x.to_dict() for x in _records(raw, Pivot.from_payload) if x.to_dict()],
    "setbacks": lambda raw: [x.to_dict() for x in _records(raw, Setback.from_payload) if x.to_dict()],
}


def normalize_dossier_payload(payload: Any) -> dict:
    """Parse an arbitrary payload into the eight canonical sections.

    Total by construction: a null, a string, a list, or a dict of garbage all
    return a dict whose keys are exactly `DOSSIER_SECTIONS` and whose values are
    well-formed (possibly empty). Callers may index the result freely, which is
    what lets every function downstream of here be written without try/except.

    Sections the payload did not mention are still present but blank — the
    *writer* is what distinguishes "mentioned and blank" from "not mentioned",
    via `mentioned_sections`, because an absent field is silence and never a
    deletion (the same rule `state_edits._apply_entity_facts_v2` follows).
    """
    payload = _mapping(payload)
    return {name: parse(payload.get(name)) for name, parse in _SECTION_PARSERS.items()}


def mentioned_sections(payload: Any) -> tuple[str, ...]:
    """Which sections the payload actually spoke about, in canonical order.

    Silence and emptiness are different claims. A scan that returned no `deals`
    key has not told us the company has no deals; a scan that returned
    `"deals": []` has. Only mentioned sections are written, so a partial refresh
    can never blank out a section an earlier, deeper scan established.
    """
    payload = _mapping(payload)
    return tuple(name for name in DOSSIER_SECTIONS if name in payload)


def thin_sections(payload: Any) -> list[str]:
    """The sections that carry nothing a reader could act on.

    This is the China-coverage marker (#92): several of the most important
    competitors are HKEX/STAR-listed, where filings coverage is genuinely
    partial, and a dossier built from partial sources must say *where* the gap
    is. Marking it at the point of the absence is what makes a sparse record
    read as unmeasured rather than as a small company.

    A section nobody has scanned yet reports thin, which is intended: "we have
    not looked" is itself the finding (story 25), and is far better read as a
    marked gap than as a quiet company.
    """
    normalized = normalize_dossier_payload(payload)
    return [name for name in DOSSIER_SECTIONS if _is_blank(normalized.get(name))]


def interpretation_violations(payload: Any) -> list[str]:
    """Dotted paths at which the payload smuggled interpretation into a dossier.

    Returns a list, never raises, and never mutates. Empty means clean.

    Checked at the record level and one level inside each section, because the
    realistic leak is not `{"read_through": ...}` at the top — it is a model
    attaching a `read_through` to one setback, or a `priority` to one pipeline
    row. Either would make a *shared* record carry a *program-relative* opinion,
    and the second program to load it would silently inherit the first
    program's judgement ([03], #92 "A dossier holds facts only").
    """
    payload = _mapping(payload)
    found: list[str] = []

    for name in INTERPRETIVE_FIELDS:
        if name in payload:
            found.append(name)

    for section in DOSSIER_SECTIONS:
        raw = payload.get(section)
        rows = raw if isinstance(raw, list) else [raw]
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for name in INTERPRETIVE_FIELDS:
                if name in row:
                    path = f"{section}[{index}].{name}" if isinstance(raw, list) else f"{section}.{name}"
                    found.append(path)
    return found


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def company_dossier_path(root: Path, entity_id: str) -> Path:
    """Where a company's dossier lives: `state/entities/companies/<entity_id>.json`."""
    return Path(root) / "state" / "entities" / COMPANIES_DIRNAME / f"{entity_id}.json"


def asset_record_path(root: Path, entity_id: str) -> Path:
    """Where an asset record lives: `state/entities/assets/<entity_id>.json`.

    The other side of the kind split. A molecule's clinical facts and a
    company's corporate facts are not conflated (story 30), so they do not share
    a directory either.
    """
    return Path(root) / "state" / "entities" / ASSETS_DIRNAME / f"{entity_id}.json"


def _read_json(path: Path) -> dict | None:
    """Read a record, or None if it is missing or unreadable.

    A corrupt dossier degrades to absence and logs; it never raises. Background
    gathering is subordinate to the cycle's intelligence (#92), so one bad file
    on disk must not take down a run that has nothing to do with it — and
    "missing" is already a state the callers render (story 25).
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        log.warning("dossiers: unreadable record %s (%s) — treating as absent", path, exc)
        return None
    return data if isinstance(data, dict) else None


def load_company_dossier(root: Path, entity_id: str) -> dict | None:
    """One company dossier, or None if we have not built it yet.

    None is a *rendered* state, not an error: "we have not looked yet" must stay
    distinguishable from "there is nothing there" (story 25).
    """
    return _read_json(company_dossier_path(root, entity_id))


def load_company_dossiers(root: Path) -> dict[str, dict]:
    """Every company dossier on disk, keyed by entity_id.

    Mirrors how the run loads the rest of the entity layer: read once, pass the
    map into the writer, so the writer does no reads of its own and a test can
    hand it an in-memory prior with no filesystem at all.
    """
    directory = Path(root) / "state" / "entities" / COMPANIES_DIRNAME
    if not directory.is_dir():
        return {}
    out: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        record = _read_json(path)
        if record is not None:
            out[record.get("entity_id") or path.stem] = record
    return out


def load_asset_record(root: Path, entity_id: str) -> dict | None:
    """One asset record, or None. Same absence-is-a-state rule as the dossier."""
    return _read_json(asset_record_path(root, entity_id))


# ---------------------------------------------------------------------------
# Traversal — the asset <-> company link
# ---------------------------------------------------------------------------


def company_for_asset(asset_record: Any) -> str | None:
    """The company entity_id that holds this asset, or None.

    Story 31: traverse from a readout to its sponsor's balance sheet. Total on
    purpose — this is called while rendering, and a half-written record must
    render as "sponsor unknown", never as a traceback.
    """
    facts = _mapping(_mapping(asset_record).get("facts"))
    held_by = _mapping(facts.get("held_by"))
    return _text(held_by.get("value"))


def assets_of_company(dossier: Any) -> list[str]:
    """The asset entity_ids named in a dossier's pipeline — the reverse traversal."""
    facts = _mapping(_mapping(dossier).get("facts"))
    pipeline = _mapping(facts.get("pipeline")).get("value")
    if not isinstance(pipeline, list):
        return []
    out = []
    for row in pipeline:
        asset_id = _text(_mapping(row).get("asset_entity_id"))
        if asset_id and asset_id not in out:
            out.append(asset_id)
    return out


# ---------------------------------------------------------------------------
# The pure builders — no IO, no git, no clock
# ---------------------------------------------------------------------------


def _fact(value: Any, run_id: str, issue_id: str | None) -> dict:
    """One provenanced field, in the shape `state_edits.ENTITY_FACT_FIELDS_V2` uses."""
    return {"value": value, "established_by": run_id, "issue": issue_id}


def build_company_dossier_record(
    existing: Any,
    payload: Any,
    *,
    entity_id: str,
    run_id: str,
    issue_id: str | None = None,
    date: str,
    as_of: str | None = None,
    degradation: str | None = None,
) -> tuple[dict, bool]:
    """Merge a dossier payload into a prior record. Pure: returns `(record, changed)`.

    No IO, no clock, no git — the caller supplies `date` and the prior record, so
    this is fully determined by its arguments and a test can exercise every
    merge rule without touching a filesystem ([03] clause 1: `run.py` is the sole
    machine writer, and a library module that wrote would break that).

    The merge rules, each mapping to a rule stated at the top of this module:

      - **Only mentioned sections are considered.** An absent section is silence,
        never a deletion — identical to `_apply_entity_facts_v2`.
      - **An unchanged section is a no-op.** No provenance rewrite, no drift
        entry, nothing staged. A quarterly refresh that finds nothing new must
        produce an empty diff, or the drift log becomes noise and stops being
        readable as a history.
      - **A changed section rewrites the fact AND appends a drift entry** with
        from/to and the run. Corrections append; the prior belief survives in the
        record (story 14).
      - **Interpretation is dropped, loudly.** Refusing the whole write would
        lose real facts to one bad field; silently keeping it would poison every
        program that later reads this shared record. Dropping + logging is the
        only option that does neither. The findings gate refuses it upstream;
        this is the second, asymmetric line of defence the house style asks for.
      - **`coverage.thin_sections` is recomputed from the merged record**, not
        from the payload, so the marker describes what we now hold rather than
        what this one scan happened to see.

    `changed` is False for a no-op merge, including for a payload that is null,
    prose, or a list — which is why this can be called on adversarial input
    without a guard at the call site.
    """
    violations = interpretation_violations(payload)
    if violations:
        log.warning(
            "dossiers: dropped interpretation from %s dossier payload at %s — "
            "a dossier holds facts only; read_through/priority stay on the program edge",
            entity_id,
            ", ".join(violations),
        )

    record = dict(_mapping(existing))
    record.setdefault("entity_id", entity_id)
    record["kind"] = DOSSIER_KIND
    record.setdefault("first_seen", date)
    facts = dict(_mapping(record.get("facts")))
    drift_log = _log(record.get("drift_log"))

    normalized = normalize_dossier_payload(payload)
    changed = False

    for name in mentioned_sections(payload):
        value = normalized[name]
        current = facts.get(name)
        if isinstance(current, dict) and current.get("value") == value:
            continue
        previous = current.get("value") if isinstance(current, dict) else None
        facts[name] = _fact(value, run_id, issue_id)
        drift_log.append({
            "date": date,
            "action": "established" if current is None else "corrected",
            "field": name,
            "from": previous,
            "to": value,
            "run_id": run_id,
        })
        changed = True

    # Sections we now hold, in whatever form — established this write or earlier.
    # Computed from the MERGED facts, not from the payload, so the marker
    # describes what we hold rather than what this one scan happened to see.
    # A section nobody has ever scanned counts as thin, which is the point.
    held = {
        name: (facts[name].get("value") if isinstance(facts.get(name), dict) else None)
        for name in DOSSIER_SECTIONS
    }
    coverage = Coverage(thin_sections=thin_sections(held), degradation=_text(degradation)).to_dict()

    # A coverage-only change is still a change: the cycle a degradation receipt
    # lands, the record must restage even though no fact value moved, or the
    # honesty layer silently goes stale. Guarded by `facts` so that a null or
    # prose payload against a never-built dossier stays a clean no-op rather
    # than materializing an empty file that claims everything is thin.
    if facts and record.get("coverage") != coverage:
        record["coverage"] = coverage
        changed = True

    if changed:
        record["as_of"] = _text(as_of) or date
    else:
        record.setdefault("as_of", _text(as_of) or date)

    record["facts"] = facts
    record["drift_log"] = drift_log
    if changed:
        record["version"] = (record.get("version") or 0) + 1
        record["last_edited_by"] = "loop"
    return record, changed


def build_asset_company_link(
    existing: Any,
    *,
    asset_entity_id: str,
    company_entity_id: str,
    run_id: str,
    issue_id: str | None = None,
    date: str,
) -> tuple[dict, bool]:
    """Point an asset record at the company that holds it. Pure: `(record, changed)`.

    Story 31 — traverse from a readout to its sponsor's balance sheet — and the
    reason the entity store splits by kind at all. The link lives on the ASSET
    side rather than only in the company's `pipeline[]` because the question
    being asked is "whose is this?", asked from the asset, and a lookup that has
    to scan every dossier's pipeline to answer it would be a join pretending to
    be a field.

    `held_by` is provenanced like any other fact and corrects by appending: a
    change of holder (an acquisition, an out-licence) is exactly the kind of
    event where the *previous* answer matters, because a published issue that
    named the old sponsor must stay auditable ([03] clause 5).
    """
    company_entity_id = _text(company_entity_id) or ""
    record = dict(_mapping(existing))
    record.setdefault("entity_id", asset_entity_id)
    record.setdefault("kind", "asset")
    record.setdefault("first_seen", date)
    facts = dict(_mapping(record.get("facts")))
    drift_log = _log(record.get("drift_log"))

    if not company_entity_id:
        # No holder named is not a claim that the asset is unheld. Silence.
        record["facts"] = facts
        record["drift_log"] = drift_log
        return record, False

    current = facts.get("held_by")
    if isinstance(current, dict) and current.get("value") == company_entity_id:
        record["facts"] = facts
        record["drift_log"] = drift_log
        return record, False

    previous = current.get("value") if isinstance(current, dict) else None
    facts["held_by"] = _fact(company_entity_id, run_id, issue_id)
    drift_log.append({
        "date": date,
        "action": "established" if current is None else "corrected",
        "field": "held_by",
        "from": previous,
        "to": company_entity_id,
        "run_id": run_id,
    })
    record["facts"] = facts
    record["drift_log"] = drift_log
    record["version"] = (record.get("version") or 0) + 1
    record["last_edited_by"] = "loop"
    return record, True


# ---------------------------------------------------------------------------
# The writers — same contract as state_edits: (path, changed), write iff changed
# ---------------------------------------------------------------------------


def apply_company_dossier_v2(
    root: Path,
    entity_id: str,
    payload: Any,
    *,
    existing: Any = None,
    run_id: str,
    issue_id: str | None = None,
    now: datetime | None = None,
    date: str | None = None,
    as_of: str | None = None,
    degradation: str | None = None,
) -> tuple[Path, bool]:
    """Persist a company dossier. Returns `(path, changed)`; writes only when changed.

    Deliberately the same shape as every writer in `state_edits.py`: the caller
    (`run.py`, the sole machine writer) collects the touched paths and stages
    exactly those, so a quiet cycle stages nothing and the diff is exactly the
    edit. All merge logic lives in `build_company_dossier_record`, which is pure
    — this function is the thin IO shell over it, and that is the whole seam.

    `now` or `date` supplies the clock; neither is read from the system, because
    a writer that called `datetime.now()` would make its own tests
    non-deterministic and its output un-replayable.
    """
    stamp = date or (now or datetime(1970, 1, 1)).date().isoformat()
    if now is None and date is None:
        raise ValueError("apply_company_dossier_v2 needs `now` or `date`: the writer never reads the clock")

    record, changed = build_company_dossier_record(
        existing,
        payload,
        entity_id=entity_id,
        run_id=run_id,
        issue_id=issue_id,
        date=stamp,
        as_of=as_of,
        degradation=degradation,
    )
    path = company_dossier_path(root, entity_id)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, record)
        log.info(
            "dossiers: company %s dossier updated (thin: %s)",
            entity_id,
            ", ".join(record.get("coverage", {}).get("thin_sections") or []) or "none",
        )
    return path, changed


def apply_asset_company_link_v2(
    root: Path,
    asset_entity_id: str,
    company_entity_id: str,
    *,
    existing: Any = None,
    run_id: str,
    issue_id: str | None = None,
    now: datetime | None = None,
    date: str | None = None,
) -> tuple[Path, bool]:
    """Persist the asset -> company link. `(path, changed)`, writes only when changed."""
    stamp = date or (now or datetime(1970, 1, 1)).date().isoformat()
    if now is None and date is None:
        raise ValueError("apply_asset_company_link_v2 needs `now` or `date`: the writer never reads the clock")

    record, changed = build_asset_company_link(
        existing,
        asset_entity_id=asset_entity_id,
        company_entity_id=company_entity_id,
        run_id=run_id,
        issue_id=issue_id,
        date=stamp,
    )
    path = asset_record_path(root, asset_entity_id)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, record)
        log.info("dossiers: asset %s held_by -> %s", asset_entity_id, company_entity_id)
    return path, changed
