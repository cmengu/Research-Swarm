"""The aperture roster — what replaced the six beats (spec/04).

The pivot replaced the six fixed beats with **apertures**: scans defined by
`relation-tier × scope`, DERIVED from the program rather than listed in a config
file. `beats.toml` is gone; a program's `config/programs/<id>.toml` plus this
planner produce the run's scans. The template pattern survives — apertures differ
in SCOPE, never in RULES, so trust tiers, citation discipline, the read-only wall
and the findings contract still live once in `prompts/researcher.md`.

The roster is `1 + N + 1` (spec/04 aperture roster), bounded by config, not by
the competitor list:

    biology_scan            1 per program   target + moa, indication-blind
                                            (carries mechanism + target twins)
    arena_scan:<indication> N per program   one per indication
                                            (carries setting rivals + benchmark/SOC)
    house_sweep             1, fixed        the wider board, aimed
                                            (interest-steering, BD, threat, blind spots)

A FOURTH kind joins them (spec #92, the entity dossier):

    dossier_scan:<entity_id> 0..M           one company's whole history

It is deliberately an aperture rather than a new stage, so it inherits the
existing fan-out, transport, validation seam, degradation handling and cost
accounting. It differs from the other three in exactly two ways, and both are
load-bearing enough to live in the aperture's OWN definition rather than as a
special case at a call site:

1. **Window exempt.** Every other aperture is bounded by the run's coverage
   window; a dossier scan is not, because its subject is history — the same
   windowing that once let a seven-day window discard a $1.1B acquisition would
   truncate a company's founding story to nothing. Modelled as
   `Aperture.window_exempt`, so a caller ASKS rather than tests `kind ==`.
2. **Not scheduled per cycle.** It is off the `1 + N + 1` roster entirely, and
   therefore out of `plan_apertures`. It is planned separately by
   `plan_dossier_scans`, which fires on (a) first sighting of a company, (b) a
   slow per-program refresh dial (⚑ quarterly), (c) a material event.

A dossier scan also carries an explicit COST CAP: history search is unbounded by
nature, so the scan declares its ceiling up front and, on exceeding it, DEGRADES
WITH A RECEIPT rather than truncating silently (spec #92 "Cost is bounded per
scan"). Degrading beats truncating because a capped scan that says so reads as
unmeasured, while a silently truncated one reads as a small company.

Cost is `FIXED + N × (one sonnet arena scan)`. A priority_indication's arena scan
is DORMANT — event-triggered, slow (SOC moves in years), rendered as a dormancy
degradation rather than run every cycle; only `active_arena` indications scan.

This module is pure: `plan_apertures(program)` is a total function of the program
config, and the dossier planner is a total function of its arguments — it reads
no clock and no file of its own. The findings shape and the researcher prompt
(which need a live run to verify) are separate — this is the deterministic
skeleton they hang on.

Spec: docs/spec/04-researchers.md, docs/spec/09-orchestrator.md,
docs/spec/03-state-and-governance.md, issue #92
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from researchswarm.programs import Program

BIOLOGY_SCAN = "biology_scan"
ARENA_SCAN = "arena_scan"
HOUSE_SWEEP = "house_sweep"
DOSSIER_SCAN = "dossier_scan"

# The role that makes an indication's arena scan run this cycle. A
# priority_indication is tracked but its arena scan is dormant (event-triggered).
ACTIVE_ARENA_ROLE = "active_arena"

# ⚑ The dossier refresh dial (spec #92: "refreshes on a slow per-program dial —
# default quarterly, a STATED default, flippable, not an open question"). 91 days
# is one quarter; the dial is coarse by design, so calendar-exact quarter maths
# would be false precision. Named here so the default is a decision with a home,
# not a magic number at a call site.
DOSSIER_REFRESH_DAYS = 91

# Why a dossier scan was planned. Recorded ON the aperture so the audit trail can
# answer "why was this cost spent" without re-deriving the decision, and so a
# reader can tell a first build from a routine refresh from an event response.
DOSSIER_TRIGGER_FIRST_SIGHTING = "first_sighting"
DOSSIER_TRIGGER_MATERIAL_EVENT = "material_event"
DOSSIER_TRIGGER_REFRESH_DUE = "refresh_due"

# The degradation this module can cause. The register in
# docs/spec/06-validator-and-critic.md owns the vocabulary; this constant is the
# id the receipt carries, so the two cannot be spelled differently.
DOSSIER_SCAN_COST_CAPPED = "dossier_scan_cost_capped"

# The entity_id prefix convention for company records (spec #92 splits
# `state/entities/` into asset and company records; `co_remegen` is the spec's own
# example). Used only as a FALLBACK when a record does not state its own `kind`.
COMPANY_ID_PREFIX = "co_"
COMPANY_KIND = "company"


@dataclass(frozen=True)
class CostCap:
    """The explicit ceiling a scan declares before it runs (spec #92).

    History search has no natural end: "everything RemeGen ever said" is a query
    that can consume a whole run's budget. So the scan states its bound up front,
    and `cap_receipt` turns an overrun into a declared degradation instead of a
    silent truncation. Every number is per-scan, not per-run.

    Two of the three are MECHANICALLY ENFORCED and one is prompt guidance, and the
    split is the point (spec/06 admission test 2: a degradation earns its
    exemption only when its trigger is detectable from facts the ORCHESTRATOR
    holds):

    * `max_turns` and `max_usd` are checked against the transport envelope, which
      the orchestrator parses itself — `num_turns` and `total_cost_usd`. Nobody
      has to take the model's word for either.
    * `max_sources` is a BUDGET STATED TO THE MODEL, not a gate. How many sources
      a scan read is only knowable from the model's own report, and a model
      self-report can never trigger a degradation here — that is the same rule
      that keeps a researcher from authoring its own priority, applied to
      accounting. So it steers the prompt and is recorded on the receipt for
      context; it never fires one.
    """

    # Named `max_turns` because a turn is what the envelope actually counts. The
    # `max_searches` alias survives for the prompt renderer and the config that
    # spell it that way — one number, two names, never two numbers.
    max_turns: int
    max_sources: int
    max_usd: float = 4.0

    @property
    def max_searches(self) -> int:
        return self.max_turns


# The default dossier ceiling. Generous enough to assemble a real company history
# in one pass, small enough that one company cannot outspend the cycle's actual
# intelligence — which is what dossier gathering is subordinate to (spec #92: "a
# failed or dormant dossier scan degrades the run, never fails it").
DOSSIER_COST_CAP = CostCap(max_turns=24, max_sources=40, max_usd=4.0)


@dataclass(frozen=True)
class Aperture:
    """One scan the run will fan out (or skip, when dormant).

    `id` is the stable slug the findings file is keyed by
    (`runs/<run_id>/findings/<id>.json`) and the value `sources_and_method.apertures_run`
    records. `kind` is the template family; `scope` is the human-readable scope
    string interpolated into the shared researcher prompt and echoed into
    `apertures_run[].scope`. `active` is False only for a dormant arena scan — it
    stays in the roster (so the dormancy renders) but is not run.

    `window_exempt` and `cost_cap` exist so that the two things making a dossier
    scan different are PROPERTIES OF THE APERTURE, askable by any caller, rather
    than `if kind == "dossier_scan"` branches scattered through the prompt
    renderer, the researcher and the validator (spec #92: "This exemption must be
    explicit in the aperture's own definition rather than a special case in the
    researcher prompt"). All three are last, with defaults, so every existing
    constructor call and every existing equality assertion keeps working.

    `trigger` is empty for the three cycle apertures — they need no reason, they
    run every cycle by construction. Only a dossier scan carries one.
    """

    id: str
    kind: str
    scope: str
    active: bool
    # Bounded by the run's coverage window unless stated otherwise: False for the
    # three cycle apertures, True for a dossier scan, whose subject is history.
    window_exempt: bool = False
    # None means "no explicit ceiling declared" — a cycle aperture is bounded by
    # its window instead, which is a ceiling of a different kind.
    cost_cap: CostCap | None = None
    trigger: str = ""

    @property
    def dormant(self) -> bool:
        return not self.active

    @property
    def window_bounded(self) -> bool:
        """The positive form of the exemption, so a caller can ask the question
        either way round without writing `not a.window_exempt` and inverting it
        by accident at the one call site that matters."""
        return not self.window_exempt


def plan_apertures(program: Program) -> list[Aperture]:
    """The `1 + N + 1` aperture roster for a program — a total function of config.

    One biology scan (target + moa, indication-blind), one arena scan per
    indication (active only for `active_arena` roles), and one house sweep. The
    ordering is stable — biology, then arenas in config order, then house — so the
    run's fan-out and the audit trail read the same way every cycle.

    Dossier scans are deliberately NOT here: see `plan_dossier_scans`.
    """
    apertures = [
        Aperture(
            id=BIOLOGY_SCAN,
            kind=BIOLOGY_SCAN,
            scope=f"target={program.target}, moa={program.moa}",
            active=True,
        )
    ]
    for indication in program.indications:
        apertures.append(
            Aperture(
                id=f"{ARENA_SCAN}:{indication.id}",
                kind=ARENA_SCAN,
                scope=indication.id,
                active=indication.role == ACTIVE_ARENA_ROLE,
            )
        )
    apertures.append(
        Aperture(
            id=HOUSE_SWEEP,
            kind=HOUSE_SWEEP,
            scope="partnership_bd + threat_financing + blind_spots",
            active=True,
        )
    )
    return apertures


def active_apertures(program: Program) -> list[Aperture]:
    """Just the apertures that actually fan out this cycle — the cost driver.

    Dormant arena scans are excluded: they render a dormancy degradation in the
    section they would have fed, but spend no model budget. So `len(active_apertures)`
    is the run's real agent count, `1 + (active arenas) + 1`.
    """
    return [a for a in plan_apertures(program) if a.active]


# ---------------------------------------------------------------------------
# dossier_scan — the fourth kind (spec #92)
# ---------------------------------------------------------------------------
#
# Kept OUT of `plan_apertures` on purpose. `plan_apertures` answers "what does
# this program scan every cycle", and its answer must stay `1 + N + 1`: it is a
# total function of CONFIG, while a dossier scan is a function of STATE (which
# companies we know, how old their records are, what just happened to them).
# Folding a state-dependent scan into a config-only planner would make the cycle
# roster non-deterministic from config — the very property the rest of the
# pipeline (cost accounting, the prompt renderer, the audit trail) is built on.


def dossier_aperture(
    entity_id: str,
    trigger: str,
    *,
    cost_cap: CostCap = DOSSIER_COST_CAP,
) -> Aperture:
    """Build one company's dossier scan: window-exempt and cost-capped.

    This is the single place the two load-bearing differences are stated, so
    there is exactly one definition to read and no way to construct a dossier
    scan that is accidentally window-bounded or accidentally uncapped.
    """
    return Aperture(
        id=f"{DOSSIER_SCAN}:{entity_id}",
        kind=DOSSIER_SCAN,
        scope=entity_id,
        active=True,
        window_exempt=True,
        cost_cap=cost_cap,
        trigger=trigger,
    )


def dossier_trigger(
    entity_id: str,
    *,
    dossiers: Mapping | None = None,
    today: date | None = None,
    material_events: Iterable | None = None,
    refresh_days: int = DOSSIER_REFRESH_DAYS,
) -> str | None:
    """Why this company needs a dossier scan THIS run — or None for "it doesn't".

    The three triggers, in precedence order (spec #92 "It is not scheduled per
    cycle"):

      1. `first_sighting` — no usable dossier exists. Building on first sighting
         is what makes a competitor's SECOND appearance arrive with history
         attached instead of from a standing start.
      2. `material_event` — an acquisition or a discontinuation must not wait for
         the dial.
      3. `refresh_due` — the slow ⚑ quarterly dial, so background gathering does
         not consume every cycle's budget.

    Precedence matters because the reason IS the audit trail: a company with no
    record that is also in the news is a first build, not a refresh, and saying
    "refresh" of a record that never existed would be a lie in the receipt.

    TOTAL AND CRASH-PROOF by contract. Every input here is machine-assembled from
    state files and model output, which in this repo has repeatedly meant null,
    prose where a mapping was expected, a list where a dict was expected, or a
    dict nested one level too deep. A planner that raises takes the run down
    AFTER the cycle's real intelligence was gathered, which is strictly worse
    than one that plans a redundant scan. So: anything unreadable resolves toward
    scanning (a wasted capped scan is cheap; a missing dossier is the product),
    and an unparseable `as_of` reads as STALE — the same rule the interest list
    already uses for an unknowable edit date (`programs.InterestList.is_stale`).
    """
    if not isinstance(entity_id, str) or not entity_id.strip():
        return None

    record = _dossier_record(dossiers, entity_id)
    if record is None:
        return DOSSIER_TRIGGER_FIRST_SIGHTING
    if entity_id in _material_event_ids(material_events):
        return DOSSIER_TRIGGER_MATERIAL_EVENT
    if _refresh_due(record.get("as_of"), today, refresh_days):
        return DOSSIER_TRIGGER_REFRESH_DUE
    return None


def plan_dossier_scans(
    company_ids: Iterable | None,
    *,
    dossiers: Mapping | None = None,
    today: date | None = None,
    material_events: Iterable | None = None,
    refresh_days: int = DOSSIER_REFRESH_DAYS,
    cost_cap: CostCap = DOSSIER_COST_CAP,
) -> list[Aperture]:
    """Which companies need a dossier scan this run — the fourth kind's roster.

    Deliberately takes the company id set rather than a `Program`: dossiers are
    program-agnostic (spec #92 "a dossier is shared; an opinion is not"), so the
    planner must not be able to reach a program's read-throughs at all. The
    caller supplies the ids — `company_ids_from_entities` derives them from the
    shared fact layer, which is also how a newly discovered competitor queues its
    own scan and the roster deepens as it widens (story 40).

    Order follows the caller's order, deduplicated, so a run's fan-out and its
    audit trail read the same way twice. Returns `[]` — never raises — when
    nothing is due, which is the ordinary case on most cycles.
    """
    apertures: list[Aperture] = []
    seen: set[str] = set()
    events = _material_event_ids(material_events)
    for entity_id in _iter_strings(company_ids):
        if entity_id in seen:
            continue
        seen.add(entity_id)
        trigger = dossier_trigger(
            entity_id,
            dossiers=dossiers,
            today=today,
            material_events=events,
            refresh_days=refresh_days,
        )
        if trigger is not None:
            apertures.append(dossier_aperture(entity_id, trigger, cost_cap=cost_cap))
    return apertures


def company_ids_from_entities(
    entities: Mapping | None,
    *,
    extra: Iterable | None = None,
) -> list[str]:
    """The company entity_ids in the shared fact layer, plus any extras.

    A record counts as a company when it SAYS so (`kind == "company"`, the split
    spec #92 introduces into `state/entities/`), falling back to the `co_` id
    convention for records written before the split — a legacy record must not
    become invisible to the planner just because it predates the field. A record
    that states some OTHER kind is taken at its word: an asset is not a company,
    however it happens to be named.

    `extra` is the discovery path: companies sighted this run that have no entity
    record yet. They are exactly the first-sighting case, so they must be
    plannable before anything at all has been written about them.
    """
    ids: list[str] = []
    seen: set[str] = set()

    def _add(entity_id: str) -> None:
        if entity_id not in seen:
            seen.add(entity_id)
            ids.append(entity_id)

    if isinstance(entities, Mapping):
        for key, record in entities.items():
            if not isinstance(key, str) or not key.strip():
                continue
            kind = record.get("kind") if isinstance(record, Mapping) else None
            if kind == COMPANY_KIND or (
                kind is None and key.startswith(COMPANY_ID_PREFIX)
            ):
                _add(key)
    for entity_id in _iter_strings(extra):
        _add(entity_id)
    return ids


def cap_receipt(aperture: Aperture, spend: Mapping | None) -> dict | None:
    """The receipt a dossier scan writes when it hits its ceiling — or None.

    Spec #92: "exceeding it degrades with a receipt rather than truncating
    silently". The receipt is what makes a capped scan legible as UNMEASURED
    rather than as a small company — the same distinction the thin-sections
    mechanism draws inside the record itself. It names the cap, the spend and the
    degradation id, so the register entry and the inline render can both be
    derived from it without re-deriving the decision.

    `spend` IS THE TRANSPORT ENVELOPE'S ACCOUNTING, not the model's — the caller
    passes `{"turns": envelope num_turns, "usd": envelope total_cost_usd}`. This
    used to read a `spend` key off the model's own payload, and that made the cap
    dead code twice over: nothing produced the key, and nothing could have been
    allowed to. Spec/06 admission test 2 says a degradation earns its exemption
    only when its trigger is MECHANICALLY DETECTABLE FROM FACTS THE ORCHESTRATOR
    HOLDS, and a model self-reporting the cost of its own overrun never qualifies
    — a scan that blew its budget is exactly the scan least likely to say so. The
    envelope is a fact the orchestrator parses for itself, so the cap fires on
    evidence rather than on cooperation. It is the same rule that keeps a
    researcher from authoring its own priority, applied one level down to
    accounting.

    Returns None when the scan stayed inside its cap, or when there is no cap to
    exceed. Never raises: a crash here would kill the run over an ACCOUNTING
    detail after the intelligence had already been gathered.
    """
    cap = getattr(aperture, "cost_cap", None)
    if not isinstance(cap, CostCap):
        return None
    readable = spend if isinstance(spend, Mapping) else {}
    turns = _as_int(readable.get("turns"))
    usd = _as_float(readable.get("usd"))
    exceeded = []
    if turns > _as_int(cap.max_turns):
        exceeded.append("turns")
    if usd > _as_float(cap.max_usd):
        exceeded.append("usd")
    if not exceeded:
        return None
    return {
        "aperture": getattr(aperture, "id", ""),
        "degradation": DOSSIER_SCAN_COST_CAPPED,
        "exceeded": exceeded,
        # `sources` rides along on both sides as context — it is the budget the
        # prompt stated, never a trigger, because only the model could report it.
        "cap": {"turns": cap.max_turns, "usd": cap.max_usd, "sources": cap.max_sources},
        "spend": {"turns": turns, "usd": usd},
    }


# ---------------------------------------------------------------------------
# Defensive readers — every one of these takes untrusted input
# ---------------------------------------------------------------------------
#
# These exist because the crashes-on-adversarial-input bug has shipped five times
# in this repo. The rule they encode: coerce and degrade, never raise.


def _dossier_record(dossiers: Mapping | None, entity_id: str) -> Mapping | None:
    """The usable dossier for an entity, or None when there is nothing to use.

    A record that is null, prose, a list, or otherwise not a mapping is treated
    as ABSENT rather than as an error: we cannot read an `as_of` out of it, so we
    do not know this company, so this is a first sighting.
    """
    if not isinstance(dossiers, Mapping):
        return None
    try:
        record = dossiers.get(entity_id)
    except Exception:  # a Mapping look-alike with a hostile __getitem__
        return None
    return record if isinstance(record, Mapping) else None


def _refresh_due(as_of, today: date | None, refresh_days: int) -> bool:
    """Has the slow dial come round? An unknowable `as_of` reads as due.

    Staleness is the safe direction: re-scanning a fresh record costs one capped
    scan, while trusting an undated record can leave a permanently stale dossier
    presented as current — exactly the "age mistaken for absence of activity"
    failure (story 16).
    """
    if not isinstance(today, date):
        return False  # no clock to judge against — the dial cannot fire blind
    if not isinstance(as_of, str):
        return True
    try:
        stamped = date.fromisoformat(as_of.strip())
    except (AttributeError, TypeError, ValueError):
        return True
    return (today - stamped).days >= _as_int(refresh_days, default=DOSSIER_REFRESH_DAYS)


def _material_event_ids(material_events: Iterable | None) -> frozenset[str]:
    """The entity_ids flagged with a material event this run.

    Accepts either bare ids or event mappings carrying an `entity_id`, because
    the caller may hold either shape depending on where the event came from, and
    the planner should not force a conversion on it. Anything else is skipped —
    an unreadable event set means the dial and first-sighting rules still decide.
    """
    if material_events is None or isinstance(material_events, (str, bytes)):
        return frozenset()
    try:
        items = list(material_events)
    except TypeError:
        return frozenset()
    ids: set[str] = set()
    for item in items:
        if isinstance(item, str) and item.strip():
            ids.add(item)
        elif isinstance(item, Mapping):
            entity_id = item.get("entity_id")
            if isinstance(entity_id, str) and entity_id.strip():
                ids.add(entity_id)
    return frozenset(ids)


def _iter_strings(values: Iterable | None) -> list[str]:
    """Non-empty strings out of anything iterable — a bare string is NOT a list.

    A caller passing `"co_remegen"` where a collection was expected means one
    company, never eleven single characters, so a lone string is read as one id.
    """
    if values is None:
        return []
    if isinstance(values, str):
        return [values] if values.strip() else []
    if isinstance(values, Mapping):
        values = list(values.keys())
    try:
        items = list(values)
    except TypeError:
        return []
    return [v for v in items if isinstance(v, str) and v.strip()]


def _as_float(value, *, default: float = 0.0) -> float:
    """A dollar amount out of untrusted input, defaulting rather than raising.

    The envelope is machine-written, but it is still parsed JSON: a missing
    `total_cost_usd` arrives as None, and a cap that raised on it would take the
    run down over an accounting detail. Booleans are rejected for the same reason
    as in `_as_int`, and nan/inf default rather than comparing surprisingly.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return default
    else:
        return default
    return number if number == number and number not in (float("inf"), float("-inf")) else default


def _as_int(value, *, default: int = 0) -> int:
    """A count out of untrusted input, defaulting rather than raising.

    Booleans are rejected on purpose: `True` is an int in Python, and a spend of
    `True` searches is a shape error, not a count of one.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except (ValueError, OverflowError):  # nan / inf
            return default
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default
