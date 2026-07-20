"""Rendering the shared researcher template.

prompts/researcher.md is a document ABOUT the template, with the template itself
fenced inside it. The fence is what we render; the surrounding design notes stay
out of the model's context.

The rule this module exists to enforce: state is interpolated FRESH at run time
and never baked into the template file. Stance text especially — a template that
inlines a stance means an owner can change their worldview and the next issue
still argues the old one, with nothing to show for it. That is the single
failure the thesis propagation contract exists to prevent.

Spec: docs/spec/04-researchers.md, docs/spec/03-state-and-governance.md
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from researchswarm.apertures import (
    ARENA_SCAN,
    BIOLOGY_SCAN,
    DOSSIER_COST_CAP,
    HOUSE_SWEEP,
    Aperture,
)
from researchswarm.beats import Beat
from researchswarm.calendar import SurgeState
from researchswarm.critic import REAFFIRMED
from researchswarm.dossiers import DOSSIER_SECTIONS, assets_of_company
from researchswarm.programs import Edge, InterestList, Program, program_roster
from researchswarm.state import State

# The factual fields a catalyst-queue item carries into the published snapshot.
# what_it_would_prove is DELIBERATELY absent: the manager authors it (thesis-
# gated), so handing it the pre-existing value would invite a copy where an
# argument belongs. seed_note is internal scaffolding and never rendered.
QUEUE_SNAPSHOT_FIELDS = (
    "id", "asset", "entity_ids", "holders", "catalyst", "first_expected_window",
    "expected_window", "window_source", "status", "slip_log", "bears_on_thesis_slot",
    "sources",
)

# The v2 queue snapshot carries one extra factual field the manager reproduces
# verbatim: `fed_by` (competitor_discovery｜scheduled｜manual), the provenance of
# how a prediction reached the one governed surface (spec/07, #54 — discovery
# feeds the queue rather than growing a `next_catalyst` field on each competitor).
# what_it_would_prove stays absent for the same reason as v1: the manager authors
# it, thesis-gated, so handing it the state's value would invite a copy where an
# argument belongs.
QUEUE_SNAPSHOT_FIELDS_V2 = QUEUE_SNAPSHOT_FIELDS + ("fed_by",)

# The opening fence's LENGTH is captured and back-referenced by the closing one,
# so a template may contain shorter fences without being cut short by them.
#
# This is not a nicety. The old pattern was ```text\n(.*?)``` — non-greedy, blind
# to fence length — so the first nested ``` ended the match. manager-v2.md opens a
# ```json worked example two-thirds of the way down its template, and EVERYTHING
# from that line on was silently dropped from the prompt the manager actually
# received: the worked example itself, and the authored contract for
# quiet_this_cycle, newly_discovered, house_view, thesis_updates, critic_report
# and sources_and_method. The model was left to infer six sections' shapes, then
# blocked by a validator holding it to the shapes it had never been shown. It cost
# several live runs, and it was invisible because the template still LOOKED right
# in the document — only the extraction was short.
#
# A doc that needs nested fences opens with four backticks (CommonMark: a fence is
# closed only by one at least as long). Three-backtick templates with no nesting
# are unaffected, which is every other prompt file.
TEMPLATE_FENCE = re.compile(r"^(`{3,})text\n(.*?)\n\1[ \t]*$", re.DOTALL | re.MULTILINE)
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

DORMANT_SLOT = "(no stance seeded)"
NO_CARVE_OUT = "No carve-outs."


class UnresolvedPlaceholder(ValueError):
    """A {{placeholder}} survived rendering.

    Never let this reach the model: a literal {{watchlist_roster}} in a prompt
    is an invitation to invent one.
    """


@dataclass(frozen=True)
class RunContext:
    run_id: str
    coverage_window_from: str
    coverage_window_to: str
    surge: SurgeState | None = None

    @property
    def window(self) -> dict:
        """The window in the {from, to} shape the findings contract echoes."""
        return {"from": self.coverage_window_from, "to": self.coverage_window_to}


def load_template(path: Path) -> str:
    """Extract the fenced template from the prompt document.

    Fence-length aware — see TEMPLATE_FENCE for the truncation this closes.
    """
    path = Path(path)
    match = TEMPLATE_FENCE.search(path.read_text())
    if not match:
        raise ValueError(f"{path}: no fenced ```text template block found")
    return match.group(2).strip()


def _watchlist_roster(state: State) -> str:
    """Compact roster: entity_id · name · tier · priority · watch_for.

    why_tracked is deliberately excluded. It is a summary, and summaries are the
    manager's job — handing one to a researcher hands it an interpretation.
    """
    return "\n".join(
        "- {entity_id} · {name} · {tier} · {priority} · {watch_for}".format(
            entity_id=e["entity_id"],
            name=e["name"],
            tier=e["tier"],
            priority=e["priority"],
            watch_for=", ".join(e.get("watch_for", [])) or "—",
        )
        for e in state.watchlist.get("entities", [])
    )


def _thesis_slots(state: State) -> str:
    """Per slot: id · title · [provenance], then the stance on its own line.

    A dormant slot renders a marker rather than an invention. Provenance rides
    along because four of six stances are provisional, and a lens the reader
    knows is provisional is safer than one presented as settled.

    This is the one deliberate departure from the placeholder-notes table, which
    reads `id · title · stance`. A stance is a paragraph — the seeded ones run to
    ~400 characters — and inlining that after a `·` produces a wall the model has
    to parse a delimiter out of. The table's shorthand doesn't survive the real
    field, so the stance gets its own line. Roster and queue follow the table
    exactly, because there the fields are short enough that it works.

    The actual rendering is `_render_thesis_slots(beliefs)` so the v2 manager
    prompt can reuse it: the thesis lives in the same `state/thesis.json`, but the
    v2 render path holds it as a plain dict, not a v1 `State`. One renderer, one
    dormant marker, so v1 and v2 never drift on how a stance is shown.
    """
    return _render_thesis_slots(state.thesis.get("beliefs", []))


def _render_thesis_slots(beliefs: list[dict]) -> str:
    """The stance-block renderer shared by the v1 and v2 manager prompts."""
    lines = []
    for belief in beliefs:
        stance = belief.get("stance") or DORMANT_SLOT
        provenance = belief.get("stance_provenance", "unknown")
        lines.append(f"- {belief['id']} · {belief['title']} [{provenance}]\n  {stance}")
    return "\n".join(lines)


def _catalyst_queue_active(state: State) -> str:
    """Active items only: pending or slipped. delivered and dead are terminal."""
    lines = [
        "- {id} · {asset} · {entity_ids} · {catalyst} · {window} · {status}".format(
            id=item["id"],
            asset=item.get("asset", "—"),
            entity_ids=", ".join(item.get("entity_ids", [])) or "—",
            catalyst=item.get("catalyst", "—"),
            window=item.get("expected_window") or "window unscheduled",
            status=item["status"],
        )
        for item in state.catalyst_queue.get("queue", [])
        if item.get("status") in ("pending", "slipped")
    ]
    return "\n".join(lines) if lines else "- (no active catalysts)"


def _surge_block(surge: SurgeState | None) -> str:
    """The surge line in a researcher's run context — empty outside a window.

    Inside a window it names the conference and the day, matching the placeholder
    contract in prompts/researcher.md exactly (`- surge: … conference window …`)."""
    if surge is None:
        return ""
    return (
        f"- surge: {surge.window} day {surge.day} of {surge.of}, "
        f"conference window {surge.starts} → {surge.ends}"
    )


def _window_carveout(surge: SurgeState | None) -> str:
    """Sourcing rule 4's carve-out — so a researcher does not self-censor an
    in-window story that lands outside the narrowed one-day coverage window.

    Outside surge there is nothing to carve out. Inside, anything published within
    the conference window is fair game even if outside this run's coverage window
    ([04](docs/spec/04-researchers.md)) — the same reference-window shift the
    critic's provenance_stale check gets, handed to the researcher so the two
    never disagree about what counts as in-window."""
    if surge is None:
        return NO_CARVE_OUT
    return (
        f"Carve-out: during the current {surge.window} window, anything published "
        f"within the conference window ({surge.starts} → {surge.ends}) is fair game "
        "even if outside this run's one-day coverage window."
    )


def render_researcher_prompt(
    template: str, beat: Beat, ctx: RunContext, state: State
) -> str:
    """Interpolate one beat's prompt. Raises if any placeholder is left over.

    surge_block and window_carveout come from ctx.surge — empty / "no carve-outs"
    on a baseline run, the conference window and its carve-out inside a verified
    surge window, so an in-window story that lands outside the narrowed one-day
    coverage window is not self-censored (spec/02, spec/04).
    """
    values = {
        "beat_id": beat.id,
        "beat_name": beat.name,
        "beat_charter": beat.charter,
        "beat_seed_angles": "\n".join(f"- {angle}" for angle in beat.seed_angles),
        "beat_notes": beat.notes,
        "max_turns": str(beat.max_turns),
        "run_id": ctx.run_id,
        "coverage_window_from": ctx.coverage_window_from,
        "coverage_window_to": ctx.coverage_window_to,
        "surge_block": _surge_block(ctx.surge),
        "window_carveout": _window_carveout(ctx.surge),
        "watchlist_roster": _watchlist_roster(state),
        "thesis_version": str(state.thesis.get("version", "?")),
        "thesis_slots": _thesis_slots(state),
        "queue_snapshot_date": state.catalyst_queue.get("last_recut_at") or "never re-cut",
        "catalyst_queue_active": _catalyst_queue_active(state),
    }

    return _substitute(template, values)


# ---------------------------------------------------------------------------
# The v2 researcher prompt — one template, N apertures (spec/04).
#
# Additive alongside the v1 render_researcher_prompt above, exactly like the v2
# manager renderer is additive alongside the v1 manager: the engine dispatches on
# schema_version, so the two prompts run side by side while the pipeline migrates.
# Nothing here touches the v1 path — render_researcher_prompt, its beat helpers and
# its tests are unchanged. What changed is the scope unit: the six fixed beats
# became three aperture KINDS (biology_scan / arena_scan / house_sweep), and the
# per-beat charter became a per-aperture SCOPE block. The rules below the scope —
# the read-only wall, the sourcing tiers, the coverage duty, the contract — are
# identical across apertures and live once in prompts/researcher-v2.md.
#
# The shared machinery is reused wholesale: _substitute (and its UnresolvedPlaceholder
# wall), _surge_block, _window_carveout, and _render_thesis_slots. So v1 and v2
# never drift on how a stance is shown, what counts as in-window, or how a leftover
# placeholder is caught.
# ---------------------------------------------------------------------------


def _aperture_scope_block(aperture: Aperture) -> str:
    """The per-kind SCOPE block — the ONE thing that differs across apertures.

    Everything else in the researcher template is identical for all three kinds
    (spec/04 "one template, N apertures"); this is where a biology scan, an arena
    scan and a house sweep part ways. The block names the relation classes each
    kind carries and — crucially for the house sweep — the `house_lens` tagging
    duty and the folded-in discovery + blind-spot work, so the one conditional
    field in the contract (`house_lens`, house_sweep-only) is grounded in prose the
    model actually read rather than left as a bare comment.

    `aperture.scope` (e.g. `target=HER3 (ERBB3), moa=signalling_blockade`, or an
    indication id) is echoed literally so the scope string the run planned and the
    scope the researcher scans are provably the same value.
    """
    if aperture.kind == BIOLOGY_SCAN:
        return (
            "BIOLOGY SCAN (1 per program) — target + MOA, INDICATION-BLIND.\n"
            f"Scope: {aperture.scope}.\n"
            "You carry the program's biology across EVERY indication. Chase two\n"
            "relation classes (report the fact; you do NOT type them — that is the\n"
            "manager's):\n"
            "- mechanism twins: same target AND same MOA — a true rival to the thesis.\n"
            "- target twins: same target, DIFFERENT MOA (e.g. a HER3 ADC vs a HER3\n"
            "  signalling antibody) — validates the target, not the mechanism.\n"
            "On an off-roster find, attach a proposed_relation as a PROPOSAL only.\n"
            "house_lens stays null for this aperture."
        )
    if aperture.kind == ARENA_SCAN:
        return (
            f"ARENA SCAN — ONE indication: {aperture.scope} (indication × line ×\n"
            "biomarker). You carry THIS setting's patients. Chase two relation\n"
            "classes (report the fact; the manager types them):\n"
            "- setting rivals: share the PATIENTS, not the biology.\n"
            "- benchmark / SOC: the bar the setting is measured against.\n"
            "Stay inside this indication — the biology scan carries the cross-\n"
            "indication target/mechanism twins, so you need not chase them here.\n"
            "house_lens stays null for this aperture."
        )
    if aperture.kind == HOUSE_SWEEP:
        return (
            "HOUSE SWEEP (1, fixed) — the wider oncology board, aimed.\n"
            f"Scope: {aperture.scope}.\n"
            "TWO LENSES on ONE scan (a shallow tag, NOT two scans) — set each\n"
            "finding's house_lens to the lens it answers:\n"
            "- partnership_bd: deals, licensing, collaborations, M&A that reshape\n"
            "  the board.\n"
            "- threat_financing: financings, competitive raises, and platform\n"
            "  threats (a modality engine that can be re-aimed at the program).\n"
            "PLUS blind-spot detection: name what the free-feed detective cannot\n"
            "see — China-first assets (CDE/chictr, HKEX financings), language-gated\n"
            "feeds, paywalled analyst interpretation — so the manager can RANK the\n"
            "gap rather than mistake silence for absence.\n"
            "Discovery is FOLDED IN here: an off-roster entity you surface carries\n"
            "entity_ids: [] and a proposed_entity — a CANDIDATE, never an edge.\n"
            "house_lens is REQUIRED on every house_sweep finding (it is null for the\n"
            "other apertures)."
        )
    # A defensive default: a new aperture kind should not silently ship a template
    # with no scope guidance. Better a visible, honest gap than an invented scope.
    return f"Scope: {aperture.scope}. (No kind-specific scope guidance for '{aperture.kind}'.)"


def _researcher_competitor_roster_v2(program: Program, edges: list[Edge]) -> str:
    """The coverage-duty roster the researcher is held against: `entity_id · relation`.

    Typed edges first (in edge order), then the cold-start `seed_competitors` not
    yet on an edge, rendered `(seed — untyped)` so the researcher knows it still
    covers them even though the manager has not typed them. Ordering is stable so
    the audit is diffable.

    The read-through is DELIBERATELY excluded — exactly the principle by which the
    v1 `_watchlist_roster` excludes `why_tracked`: a read-through is the manager's
    interpretation, and a researcher handed a summary is handed a conclusion. The
    researcher gets the vocabulary (the slug) and the coverage target (the relation
    it is checking against), not the argument. Names are absent too: the shared
    `state/entities/` layer is the manager's to resolve, and the entity_id slug IS
    the spine the researcher writes findings against.
    """
    lines: list[str] = []
    typed = {e.entity_id for e in edges}
    for edge in edges:
        lines.append(f"- {edge.entity_id} · {edge.relation}")
    for slug in program.seed_competitors:
        if slug in typed:
            continue  # already surfaced above as a typed edge
        lines.append(f"- {slug} · (seed — untyped)")
    return "\n".join(lines) if lines else "- (no competitors typed or seeded yet)"


def _researcher_interest_list_v2(interests: InterestList) -> str:
    """The steering wheel as `tier · note` lines — the researcher's slice of it.

    Only `tier · note`: the note steers what the researcher NOTICES and the tier
    marks its coverage-duty bar (a strong-tier interest in scope must be checked).
    The version stamp and the rot marker are omitted here on purpose — rot is a
    fail-visible degradation the MANAGER stamps on the digest (spec/06 register),
    not something a fact-gathering researcher acts on. Handing it here would be
    noise against a duty the researcher cannot discharge.
    """
    if not interests.interests:
        return "- (no interests seeded)"
    return "\n".join(f"- {i.tier} · {i.note}" for i in interests.interests)


def render_researcher_prompt_v2(
    template: str,
    aperture: Aperture,
    *,
    program: Program,
    interests: InterestList,
    edges: list[Edge],
    thesis: dict,
    ctx: RunContext,
) -> str:
    """Interpolate ONE aperture's researcher prompt. Raises on a leftover placeholder.

    The v2 counterpart of `render_researcher_prompt`: one shared template
    (`prompts/researcher-v2.md`), one aperture per call. The only thing that
    differs across the `1 + N + 1` apertures is `aperture_scope` — the biology /
    arena / house scope block; every rule below it is byte-identical, which is the
    whole point of "one template, N apertures" (spec/04).

    Reuses the shared machinery so v1 and v2 cannot drift: `_surge_block` /
    `_window_carveout` from `ctx.surge` (empty / "No carve-outs." on a baseline
    run, the conference window + carve-out inside a verified surge), and
    `_render_thesis_slots` for the lens (read fresh, dormant slots marked,
    provenance attached). The propagation contract binds the researcher exactly as
    it binds the manager: stance text is interpolated, never baked into the file,
    so an owner who edits a stance sees the next run's findings chased under the
    new lens.

    The competitor roster (`edges` + `program.seed_competitors`) and the interest
    list are the coverage duty; the catalyst queue is a STANDING DUTY stated as a
    rule in the template rather than interpolated — the researcher references any
    transition it observes by item id in `catalyst_refs`, and the manager holds the
    authoritative queue.
    """
    values = {
        "program_id": program.id,
        "program_name": program.name,
        "program_sponsor": program.sponsor,
        "program_modality": program.modality,
        "program_target": program.target,
        "program_moa": program.moa,
        "aperture_id": aperture.id,
        "aperture_kind": aperture.kind,
        "aperture_scope": _aperture_scope_block(aperture),
        "run_id": ctx.run_id,
        "coverage_window_from": ctx.coverage_window_from,
        "coverage_window_to": ctx.coverage_window_to,
        "surge_block": _surge_block(ctx.surge),
        "window_carveout": _window_carveout(ctx.surge),
        "competitor_roster": _researcher_competitor_roster_v2(program, edges),
        "interest_list": _researcher_interest_list_v2(interests),
        "thesis_version": str(thesis.get("version", "?")),
        "thesis_slots": _render_thesis_slots(thesis.get("beliefs", [])),
    }
    return _substitute(template, values)


def _substitute(template: str, values: dict[str, str]) -> str:
    """Fill every {{placeholder}}, or raise if one has no value.

    Shared by both renderers: a literal {{leftover}} reaching a model is a
    silent instruction to invent, and it must never happen for either role.
    """

    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise UnresolvedPlaceholder(
                f"template references {{{{{key}}}}}, which nothing renders"
            )
        return values[key]

    return PLACEHOLDER.sub(substitute, template)


# ---------------------------------------------------------------------------
# dossier_scan — the fourth aperture kind (#92)
#
# Its own renderer, for the same reason it has its own template file: the
# subject is a COMPANY rather than a molecule, and the aperture is EXEMPT from
# the coverage window. Reusing `render_researcher_prompt_v2` would have meant
# handing the dossier template a `coverage_window_from` / `coverage_window_to`
# pair it deliberately does not carry, and handing the shared template a scope
# block that cannot repeal the window rule stated above it.
#
# The program-relative values the v2 researcher renderer interpolates —
# `thesis_slots`, `interest_list`, `competitor_roster` — are DELIBERATELY absent
# here. A dossier is shared across every program (spec/03, #92 "A dossier is
# shared; an opinion is not"), so steering it with one program's stance would
# bake that program's lens into a record every other program then inherits. The
# absence is the same absence that keeps `read_through` and `priority` on the
# relation edge.
#
# TOTAL AND CRASH-PROOF by contract, exactly as `dossiers` and `apertures` are.
# Every input below (the prior record, the discovery candidate, the asset list)
# is machine-assembled from state files and model output, which in this repo has
# repeatedly meant null, prose where a mapping was expected, a list where a dict
# was expected, or a dict nested one level too deep. A renderer that raises on a
# malformed prior record takes the run down before the scan that would have
# corrected it ever ran — strictly worse than rendering an honest "(unknown)".
# The ONE thing that still raises is an unresolved placeholder, which is not
# adversarial input but a template/renderer contract break, and which must never
# reach a model (see `UnresolvedPlaceholder`).
# ---------------------------------------------------------------------------

NO_DOSSIER_HELD = "(no dossier held — first scan)"
UNKNOWN_FIELD = "(unknown — establish it)"
NO_LINKED_ASSETS = "(none linked yet)"

# The fallback tool-turn ceiling, used only when an aperture reaches here with no
# `cost_cap` declared. `dossier_aperture` always sets one; a hand-built Aperture
# might not, and rendering "{{tool_turn_cap}}" or "None" into a budget section
# would read to the model as "unbounded" — the exact failure the cap exists to
# prevent (#92 "Cost is bounded per scan").
DEFAULT_TOOL_TURN_CAP = DOSSIER_COST_CAP.max_searches


def _dossier_identity_facts(dossier: Any) -> dict:
    """The prior record's `identity` fact value, or `{}` — never a raise.

    A stored record is `{facts: {identity: {value: {...}, established_by, ...}}}`
    (`dossiers.build_company_dossier_record`). Every level of that is unwrapped
    defensively because every level has been seen malformed.
    """
    if not isinstance(dossier, Mapping):
        return {}
    facts = dossier.get("facts")
    if not isinstance(facts, Mapping):
        return {}
    fact = facts.get("identity")
    if not isinstance(fact, Mapping):
        return {}
    value = fact.get("value")
    return dict(value) if isinstance(value, Mapping) else {}


def _dossier_scalar(*candidates: Any) -> str:
    """The first candidate that reads as real text, else the honest unknown marker.

    "(unknown — establish it)" rather than an empty line: a blank after
    `name:` reads to a model as a rendering bug and invites it to invent one,
    which is the same failure `UnresolvedPlaceholder` exists to prevent one level
    up.
    """
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return UNKNOWN_FIELD


def _dossier_alias_line(*candidates: Any) -> str:
    """Known aliases as a comma-joined line — the Chinese legal name lives here.

    Aliases are what let a model searching HKEX/CSRC disclosure find a company
    whose filings are under a romanisation we do not use. Accepts a list or a
    bare string (a model asked for a list has returned a string), and de-dupes in
    first-seen order so the line is stable and diffable.
    """
    out: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, str):
            candidate = [candidate]
        if not isinstance(candidate, (list, tuple)):
            continue
        for alias in candidate:
            text = alias.strip() if isinstance(alias, str) else ""
            if text and text not in out:
                out.append(text)
    return ", ".join(out) if out else UNKNOWN_FIELD


def _dossier_listing_line(*candidates: Any) -> str:
    """Known listings as `EXCHANGE:TICKER` pairs.

    Rendered so the scan knows WHICH filings regime to work first — an SEC
    full-text search for an HKEX-only issuer is a wasted tool turn, and the
    China-listed names are exactly where the tool budget is scarcest (#92, the
    rank-1 blind spot).
    """
    out: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, (list, tuple)):
            continue
        for listing in candidate:
            if not isinstance(listing, Mapping):
                continue
            exchange = listing.get("exchange")
            ticker = listing.get("ticker")
            exchange = exchange.strip() if isinstance(exchange, str) else ""
            ticker = ticker.strip() if isinstance(ticker, str) else ""
            text = ":".join(part for part in (exchange, ticker) if part)
            if text and text not in out:
                out.append(text)
    return ", ".join(out) if out else UNKNOWN_FIELD


def _dossier_section_lines(dossier: Any) -> list[str]:
    """One line per section we already hold: `- <section>: <value>` as JSON.

    The extend-don't-restate block. The model is told to report a field again
    ONLY to correct or better-source it, which it can only honour if it can see
    what the current value actually IS — so values are rendered in full rather
    than summarised to a count. `default=str` keeps a non-serialisable value
    (a date object that reached state through a hand edit) from raising here.
    """
    facts = dossier.get("facts") if isinstance(dossier, Mapping) else None
    if not isinstance(facts, Mapping):
        return []
    lines: list[str] = []
    for name in DOSSIER_SECTIONS:
        fact = facts.get(name)
        if not isinstance(fact, Mapping) or "value" not in fact:
            continue
        try:
            rendered = json.dumps(fact["value"], ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover — default=str covers ~all
            rendered = str(fact["value"])
        established_by = fact.get("established_by")
        stamp = f" [established_by {established_by}]" if isinstance(established_by, str) and established_by else ""
        lines.append(f"- {name}{stamp}: {rendered}")
    return lines


def _dossier_thin_line(dossier: Any) -> str:
    """The prior scan's thin sections — this refresh's highest-value targets.

    Recomputed by the writer on every merge, so this reads what we NOW hold
    rather than what any one scan happened to see. A record with no coverage
    block at all says so, because "not marked" and "nothing thin" are different
    claims and the template's whole thin-section discipline rests on the
    difference.
    """
    coverage = dossier.get("coverage") if isinstance(dossier, Mapping) else None
    if not isinstance(coverage, Mapping):
        return "(prior record marked no coverage — treat every section as unverified)"
    thin = coverage.get("thin_sections")
    names = [x.strip() for x in thin if isinstance(x, str) and x.strip()] if isinstance(thin, list) else []
    if not names:
        return "(none marked thin)"
    return ", ".join(names)


def _linked_assets_line(dossier: Any, assets: Any) -> str:
    """The asset entity_ids that link to this company, from both directions.

    Two directions because the store splits by kind (#92): the dossier's own
    pipeline names its assets forward, and an asset record names its holder via
    `held_by` — a caller that has walked `state/entities/assets/` passes those in
    as `assets`. Rendering the union means an asset the pipeline has not caught
    up with is still visible to the scan, which is precisely the asset most
    likely to be missing from the dossier.
    """
    out: list[str] = []
    for asset_id in assets_of_company(dossier):
        if asset_id not in out:
            out.append(asset_id)
    if isinstance(assets, str):
        assets = [assets]
    if isinstance(assets, (list, tuple, set, frozenset)):
        for asset_id in assets:
            text = asset_id.strip() if isinstance(asset_id, str) else ""
            if text and text not in out:
                out.append(text)
    return ", ".join(out) if out else NO_LINKED_ASSETS


def _existing_dossier_block(dossier: Any, assets: Any) -> str:
    """The "what we already hold" block — or an explicit first-scan marker.

    The template's render-time notes are emphatic on one point: this must render
    an explicit "(no dossier held — first scan)" when absent, so a first sighting
    is never ambiguous with a FAILED RENDER. A blank block would read to the
    model as "we hold nothing", which is the right answer for a first sighting
    and a dangerous lie for a record we failed to load — and the model cannot
    tell the two apart from an absence. Hence a stated sentence, always.

    Interpretation is not rendered here for the same reason it is not stored:
    the record carries facts only, and anything program-relative stays on the
    relation edge (spec/03, #92).
    """
    lines = _dossier_section_lines(dossier)
    if not lines:
        return NO_DOSSIER_HELD

    as_of = dossier.get("as_of") if isinstance(dossier, Mapping) else None
    version = dossier.get("version") if isinstance(dossier, Mapping) else None
    header = [
        "A dossier IS held. Extend and correct it; do not restate it.",
        f"- record as_of: {_dossier_scalar(as_of)}",
        f"- record version: {version if isinstance(version, int) else '(unversioned)'}",
        f"- linked assets: {_linked_assets_line(dossier, assets)}",
        f"- marked thin by the prior scan (your highest-value targets): {_dossier_thin_line(dossier)}",
        "",
        "Sections held:",
    ]
    return "\n".join(header + lines)


def render_dossier_prompt(
    template: str,
    aperture: Aperture,
    *,
    program_id: str,
    dossier: Any = None,
    candidate: Any = None,
    assets: Any = None,
    as_of: str,
    ctx: RunContext,
) -> str:
    """Interpolate ONE company's dossier-scan prompt. Raises on a leftover placeholder.

    The fourth-kind counterpart of `render_researcher_prompt_v2`, and shaped like
    it on purpose: one template argument, one aperture argument, everything else
    keyword-only, a `RunContext` for the run's identity, and `_substitute` doing
    the fill so a leftover `{{placeholder}}` can never reach a model from either
    role (spec/04).

    The company under scan comes from `aperture.scope`, which `dossier_aperture`
    sets to the entity_id — so the company the run PLANNED to scan and the company
    the prompt NAMES are provably the same value, the same guarantee
    `aperture_scope` gives the v2 researcher renderer.

    Identity is resolved with the prior record FIRST and the discovery
    `candidate` second. That order is the propagation contract applied to
    identity: a name we have already established and provenanced outranks the one
    a discovery finding happened to spell, and the scan can still correct either
    (the template's `"corrects": true` path). On a first sighting there is no
    record and the candidate is all there is.

    `ctx.coverage_window_from` / `_to` are read here and deliberately NOT
    rendered. This aperture is window-exempt (`Aperture.window_exempt`, #92), the
    template carries no window placeholder, and interpolating one would re-import
    the exact rule the exemption exists to repeal — the same rule a seven-day
    window recently used to discard a $1.1B platform acquisition.

    Every argument except `template`, `as_of` and `ctx` may be null, prose, or
    the wrong container: they are machine-assembled, and this renderer degrades
    to an honest "(unknown — establish it)" rather than raising. The only raise
    is `UnresolvedPlaceholder`.
    """
    entity_id = _dossier_scalar(
        getattr(aperture, "scope", None), getattr(aperture, "id", None)
    )
    identity = _dossier_identity_facts(dossier)
    candidate = candidate if isinstance(candidate, Mapping) else {}
    cost_cap = getattr(aperture, "cost_cap", None)
    max_searches = getattr(cost_cap, "max_searches", None)

    values = {
        # The envelope's own identifiers. A dossier rides the shared v2 envelope,
        # so the payload must echo the aperture and the program the run fanned out
        # for — the crossed-fan-out check every other aperture gets (spec/04).
        # `program_id` is an IDENTIFIER here and nothing more: no thesis, interest
        # list or roster follows it, because a dossier is shared across programs
        # and program-relative steering must not reach it.
        "aperture_id": _dossier_scalar(getattr(aperture, "id", None)),
        "program_id": _dossier_scalar(program_id),
        "company_entity_id": entity_id,
        "company_name": _dossier_scalar(
            identity.get("legal_name"), candidate.get("name"), candidate.get("legal_name")
        ),
        "company_aliases": _dossier_alias_line(
            identity.get("aliases"), candidate.get("aliases")
        ),
        "company_listings": _dossier_listing_line(
            identity.get("listings"), candidate.get("listings")
        ),
        # The trigger is the audit trail: a model told it is refreshing a record
        # behaves differently from one told this is a first build, and #92 asks
        # for the reason to be legible rather than inferred.
        "scan_trigger": _dossier_scalar(getattr(aperture, "trigger", None)),
        "as_of": _dossier_scalar(as_of),
        "run_id": _dossier_scalar(getattr(ctx, "run_id", None)),
        "existing_dossier": _existing_dossier_block(dossier, assets),
        "tool_turn_cap": str(
            max_searches if isinstance(max_searches, int) and max_searches > 0
            else DEFAULT_TOOL_TURN_CAP
        ),
    }
    return _substitute(template, values)


def _manager_watchlist_roster(state: State) -> str:
    """Full roster: entity_id · name · tier · priority, one line each.

    Unlike the researcher roster, this keeps EVERY entity, not just the ones a
    beat touches: the manager's accounting duty is that every tracked entity
    lands in watchlist or quiet_this_cycle, so it needs the whole set in front
    of it. name and tier are what the manager authors each entry's name/type
    from — watch_for is dropped here because the manager is deciding placement,
    not running a coverage sweep.
    """
    return "\n".join(
        "- {entity_id} · {name} · {tier} · {priority}".format(
            entity_id=e["entity_id"],
            name=e["name"],
            tier=e["tier"],
            priority=e["priority"],
        )
        for e in state.watchlist.get("entities", [])
    )


def _catalyst_queue_snapshot(state: State) -> str:
    """The queue as indented JSON, not a table.

    The manager must reproduce the factual fields VERBATIM into the published
    snapshot, so it is handed JSON to copy rather than a compact line it would
    have to re-serialise (and could silently mangle). Every item is included
    regardless of status: the snapshot freezes the whole queue at publication,
    not just the active slice a researcher chases.
    """
    snapshot = {
        "snapshot_of": "state/catalyst-queue.json",
        "recut_at": state.catalyst_queue.get("last_recut_at"),
        "items": [
            {field: item.get(field) for field in QUEUE_SNAPSHOT_FIELDS}
            for item in state.catalyst_queue.get("queue", [])
        ],
    }
    # ensure_ascii=False keeps em-dashes and the like literal — the model reads
    # cleaner text, and the manager can reproduce the fields byte-for-byte.
    return json.dumps(snapshot, indent=2, ensure_ascii=False)


def _findings_corpus(
    findings_by_beat: dict[str, dict],
    beats_failed: list[str] | None = None,
    *,
    unit: str = "beat",
) -> str:
    """Each beat's findings.json as a labelled JSON block, in caller order.

    One corpus renderer, shared by the v1 manager prompt, the critic prompt, AND
    the v2 manager prompt, so none of the three drift on how a finding is
    presented. `beats_failed` is the manager's difference: it passes a list
    (possibly empty) and gets an explicit dead-scans line next to the facts — what
    lets it mark the hole inline rather than read a thin section as truth. The
    critic passes None and gets only the surviving findings; on an empty run that
    leaves nothing, so it renders an explicit marker rather than a blank.

    `unit` names what a block IS — "beat" for v1, "aperture" for v2 (the pivot
    replaced beats with apertures, spec/04). It only changes the labels; the JSON
    body is byte-identical, so the two schemas share one embed. Beat/aperture
    order is whatever the caller passes (run.py keeps roster order).
    """
    blocks = [
        f"=== findings from {unit}: {beat_id} ===\n"
        f"{json.dumps(findings, indent=2, ensure_ascii=False)}"
        for beat_id, findings in findings_by_beat.items()
    ]
    if beats_failed is not None:
        failed = ", ".join(beats_failed) if beats_failed else "(none)"
        blocks.append(f"=== {unit}s that failed (no findings this cycle): {failed} ===")
    if not blocks:
        return "(no findings on disk this run)"
    return "\n\n".join(blocks)


def _prior_quiet_counts(prior_quiet: dict[str, int]) -> str:
    """entity_id: cycles_quiet lines the manager increments from.

    An empty map renders as run #1's honest value: there is no previous issue to
    increment from, so every quiet entity this cycle starts at 1.
    """
    if not prior_quiet:
        return "(no previous issue)"
    return "\n".join(f"- {entity_id}: {count}" for entity_id, count in sorted(prior_quiet.items()))


def render_manager_prompt(
    template: str,
    ctx: RunContext,
    state: State,
    *,
    findings_by_beat: dict[str, dict],
    beats_failed: list[str],
    prior_quiet: dict[str, int],
    models: dict,
    issue_id: str,
    published_at: str,
) -> str:
    """Interpolate the manager prompt. Raises if any placeholder is left over.

    Stances arrive via _thesis_slots exactly as the researcher sees them — read
    fresh, dormant slots marked, provenance attached — because the propagation
    contract binds the manager as tightly as the researcher: an owner who edits
    a stance must see the next issue argue the new one, and a template that
    inlined stance text would break that silently.
    """
    values = {
        "run_id": ctx.run_id,
        "thesis_version": str(state.thesis.get("version", "?")),
        "issue_id": issue_id,
        "published_at": published_at,
        "coverage_window_from": ctx.coverage_window_from,
        "coverage_window_to": ctx.coverage_window_to,
        "models_json": json.dumps(models, indent=2),
        "watchlist_roster": _manager_watchlist_roster(state),
        "thesis_slots": _thesis_slots(state),
        "catalyst_queue_snapshot": _catalyst_queue_snapshot(state),
        "prior_quiet_counts": _prior_quiet_counts(prior_quiet),
        "beats_failed": ", ".join(beats_failed) if beats_failed else "(none)",
        "findings_corpus": _findings_corpus(findings_by_beat, beats_failed),
    }
    return _substitute(template, values)


# ---------------------------------------------------------------------------
# The v2 manager prompt — a per-program detective, not a market digest.
#
# Additive alongside the v1 render_manager_prompt above: the engine dispatches on
# schema_version, so the two prompts (and the two seam contracts) run side by side
# while the pipeline migrates. Nothing here touches the v1 path — the v1 renderer,
# its helpers and its tests are unchanged. What changed is the vocabulary the
# manager is handed: apertures not beats, a program subject, a typed competitor
# roster, and the interest list as a second interpolated owner surface.
# ---------------------------------------------------------------------------


def _program_block_v2(program: Program) -> str:
    """The program identity as indented JSON the manager authors the `program`
    block from. `moa` is the load-bearing field (spec/07): it is what separates a
    target_twin (same target, different MOA) from a mechanism_twin (same target
    AND MOA), so it is surfaced explicitly rather than buried in prose. The
    aperture is echoed so the emitted `program.aperture` matches what actually ran.
    """
    block = {
        "id": program.id,
        "name": program.name,
        "sponsor": program.sponsor,
        "modality": program.modality,
        "target": program.target,
        "moa": program.moa,
        "config_source": f"config/programs/{program.id}.toml",
        "indications": [
            {"id": i.id, "role": i.role} for i in program.indications
        ],
        "aperture": {
            "biology_scan": {"target": program.target, "moa": program.moa},
            "arena_scans": list(program.active_arena_ids),
        },
    }
    return json.dumps(block, indent=2, ensure_ascii=False)


def _competitor_roster_v2(
    program: Program, edges: list[Edge], entities: dict[str, dict]
) -> str:
    """The typed competitor roster the accounting duty holds the manager against.

    The v2 replacement for v1's flat watchlist roster: every promoted edge (with
    its typed relation and read-through provenance) plus every cold-start
    `seed_competitors` slug not yet on an edge, rendered `(seed — untyped)` so the
    manager knows it must type it this cycle. Names are lifted from the shared
    `state/entities/` layer when a record exists; at seed the layer is empty, so a
    slug carries no name and reads with a `—` placeholder — honest about what the
    machine actually knows, never a fabricated name.

    Ordering is stable — typed edges first (in edge order), then the untyped seeds
    — so the roster reads the same way every cycle and the audit is diffable.
    """
    lines: list[str] = []
    typed = {e.entity_id for e in edges}
    for edge in edges:
        name = entities.get(edge.entity_id, {}).get("name", "—")
        provenance = edge.promoted_by or "unknown"
        lines.append(
            f"- {edge.entity_id} · {edge.relation} · {name} · promoted_by={provenance}"
        )
    for slug in program.seed_competitors:
        if slug in typed:
            continue  # already surfaced above as a typed edge
        name = entities.get(slug, {}).get("name", "—")
        lines.append(f"- {slug} · (seed — untyped) · {name} · seed_competitors")
    return "\n".join(lines) if lines else "- (no competitors typed or seeded yet)"


def _interest_list_block_v2(interests: InterestList, *, today: date) -> str:
    """The steering wheel as `tier · note` lines plus its version and rot marker.

    The interest list is the second owner surface interpolated fresh (spec/03
    #55). The note steers attention/interpretation/the bar; the tier is a sort key
    and default bar, not a score. Rot is a fail-visible degradation, never silent:
    a list edited beyond the 6-month default renders STALE here so the manager
    stamps `interest_list.rot_status: "stale"` on the digest — the trigger is a
    date the orchestrator holds, so it passes admission test 2.
    """
    rot = "STALE — render rot_status: stale" if interests.is_stale(today) else "fresh"
    header = (
        f"version {interests.version} · last_edited {interests.last_edited or '(unknown)'} "
        f"by {interests.last_edited_by} · rot: {rot}"
    )
    if not interests.interests:
        return f"{header}\n- (no interests seeded)"
    lines = [f"- {i.tier} · {i.note}" for i in interests.interests]
    return header + "\n" + "\n".join(lines)


def _aperture_roster_v2(apertures: list[Aperture]) -> str:
    """The `1 + N + 1` aperture roster: `id · kind · scope · (active｜DORMANT)`.

    A DORMANT arena scan stays in the roster (it is not run, but the manager must
    render an `arena_scan_dormant` degradation in the section it would have fed);
    an active scan that later FAILED at the seam arrives via apertures_degraded,
    not here. This block tells the manager which sections need an inline dormancy
    marker at the point of the absence (spec/04, spec/05 degradation duties).
    """
    lines = [
        "- {id} · {kind} · {scope} · {state}".format(
            id=a.id,
            kind=a.kind,
            scope=a.scope,
            state="active" if a.active else "DORMANT",
        )
        for a in apertures
    ]
    return "\n".join(lines) if lines else "- (no apertures planned)"


def _catalyst_queue_snapshot_v2(queue: dict, *, program_id: str) -> str:
    """The per-program queue as indented JSON, not a table.

    Same rationale as v1 (`_catalyst_queue_snapshot`): the manager reproduces the
    factual fields VERBATIM, so it is handed JSON to copy rather than a compact
    line it would have to re-serialise and could mangle. The v2 field set adds
    `fed_by`; `what_it_would_prove` stays omitted (the manager authors it, thesis-
    gated). Every item is included regardless of status — the snapshot freezes the
    whole queue at publication. `snapshot_of` points at the per-program path.
    """
    snapshot = {
        "snapshot_of": f"state/programs/{program_id}/catalyst-queue.json",
        "recut_at": queue.get("last_recut_at"),
        "items": [
            {field: item.get(field) for field in QUEUE_SNAPSHOT_FIELDS_V2}
            for item in queue.get("queue", [])
        ],
    }
    return json.dumps(snapshot, indent=2, ensure_ascii=False)


def render_manager_prompt_v2(
    template: str,
    *,
    program: Program,
    interests: InterestList,
    apertures: list[Aperture],
    findings_by_aperture: dict[str, dict],
    apertures_degraded: list[str],
    thesis: dict,
    catalyst_queue: dict,
    edges: list[Edge],
    entities: dict[str, dict],
    prior_quiet: dict[str, int],
    run_id: str,
    issue_id: str,
    published_at: str,
    coverage_window_from: str,
    coverage_window_to: str,
    thesis_version,
    interest_list_version,
    models: dict,
) -> str:
    """Interpolate the v2 manager prompt. Raises if any placeholder is left over.

    The v2 counterpart of `render_manager_prompt`, assembling the per-program
    detective's context from the program config, the interest list, the aperture
    roster, the aperture findings corpus, the v2 state layers (thesis, per-program
    catalyst queue, typed-competitor roster) and the run identity.

    The propagation contract binds the manager twice here, not once: both the
    thesis stances (via `_render_thesis_slots`, read fresh, dormant slots marked)
    AND the interest list (via `_interest_list_block_v2`) arrive interpolated, so
    an owner who edits either sees the next issue argue the new one. A template
    that inlined stance or interest text would break that silently — the same
    single failure the whole propagation contract exists to prevent. The interest
    list's rot is computed against `published_at`'s date, the day the issue speaks
    for, so the staleness marker is honest to the moment of publication.
    """
    today = _published_date(published_at)
    values = {
        "run_id": run_id,
        "issue_id": issue_id,
        "program_id": program.id,
        "published_at": published_at,
        "coverage_window_from": coverage_window_from,
        "coverage_window_to": coverage_window_to,
        "thesis_version": str(thesis_version),
        "interest_list_version": str(interest_list_version),
        "models_json": json.dumps(models, indent=2),
        "program_block": _program_block_v2(program),
        "competitor_roster": _competitor_roster_v2(program, edges, entities),
        "thesis_slots": _render_thesis_slots(thesis.get("beliefs", [])),
        "interest_list": _interest_list_block_v2(interests, today=today),
        "aperture_roster": _aperture_roster_v2(apertures),
        "catalyst_queue_snapshot": _catalyst_queue_snapshot_v2(
            catalyst_queue, program_id=program.id
        ),
        "prior_quiet_counts": _prior_quiet_counts(prior_quiet),
        "apertures_degraded": ", ".join(apertures_degraded) if apertures_degraded else "(none)",
        "findings_corpus": _findings_corpus(
            findings_by_aperture, apertures_degraded, unit="aperture"
        ),
    }
    return _substitute(template, values)


def _published_date(published_at: str) -> date:
    """The calendar date `published_at` speaks for, for the interest-rot clock.

    `published_at` is an ISO timestamp (`2026-07-18T07:41:00+08:00`); the rot
    window is a coarse 6-month clock, so only the date prefix matters. A malformed
    or empty value falls back to `date.min`, which reads as maximally stale — an
    unknowable publication date is exactly the case the rot marker exists to
    surface, never to hide (mirrors `InterestList.is_stale` on a bad edit date).
    """
    try:
        return date.fromisoformat((published_at or "")[:10])
    except (TypeError, ValueError):
        return date.min


def _surge_window_block(surge: SurgeState | None) -> str:
    """The conference window the critic compares provenance_stale against in surge.

    `run.surge` in the issue carries only {window, day, of} (the dashboard's
    shape), so the critic cannot read the dates from the issue — it gets them here.
    Outside surge this says so, and the critic falls back to issue.coverage_window
    exactly as always (spec/02 the critic's bar does not move — with one fix)."""
    if surge is None:
        return "(no surge this cycle — compare provenance_stale against issue.coverage_window)"
    return (
        f"run.surge is present: {surge.window}. Compare provenance_stale against this "
        f"CONFERENCE window — published_at from {surge.starts} to {surge.ends} inclusive "
        "is in-window — NOT the run's narrowed one-day coverage_window."
    )


def render_critic_prompt(
    template: str,
    *,
    issue: dict,
    findings_by_beat: dict[str, dict],
    previous_issue: dict | None,
    watchlist: dict,
    thesis: dict,
    surge: SurgeState | None = None,
) -> str:
    """Interpolate the critic rubric with its five inputs. Raises on a leftover.

    The load-bearing decision of the whole rubric (spec/06): the critic sees FIVE
    things, not just the finished issue. A critic holding only the digest cannot
    audit an ABSENCE, because the absence was removed from the artifact it is
    reading — so it also gets the raw findings (the receipt source), the previous
    issue (continuity), the watchlist (entity accounting), and the thesis
    (thesis_impact honesty and dormant-slot exemptions). Widening the input set is
    what turns "you missed a story" from unanswerable into a diff.

    The same UnresolvedPlaceholder wall the other renderers use applies: a literal
    {{issue_json}} reaching Codex is an instruction to invent the thing it should
    be judging.
    """
    values = {
        "issue_json": json.dumps(issue, indent=2, ensure_ascii=False),
        "findings_corpus": _findings_corpus(findings_by_beat),
        "previous_issue_json": (
            json.dumps(previous_issue, indent=2, ensure_ascii=False)
            if previous_issue is not None
            else "(no previous issue)"
        ),
        "watchlist_json": json.dumps(watchlist, indent=2, ensure_ascii=False),
        "thesis_json": json.dumps(thesis, indent=2, ensure_ascii=False),
        "surge_window": _surge_window_block(surge),
    }
    return _substitute(template, values)


def render_critic_prompt_v2(
    template: str,
    *,
    issue: dict,
    findings_by_aperture: dict[str, dict],
    previous_issue: dict | None,
    program: Program,
    edges: list[Edge],
    entities: dict[str, dict],
    thesis: dict,
    surge: SurgeState | None = None,
) -> str:
    """Interpolate the v2 critic rubric with its inputs. Raises on a leftover.

    The v2 twin of `render_critic_prompt`, additive beside it: the two rubrics run
    side by side while the pipeline migrates, dispatched on the issue's own
    schema_version. The load-bearing decision is unchanged (spec/06 "what the
    critic sees") — the critic gets FIVE things, not just the finished issue,
    because a critic holding only the digest cannot audit an ABSENCE: the absence
    was removed from the artifact it is reading.

    Three of the five changed SHAPE with the pivot, and one input is new:

      - the findings corpus is keyed by APERTURE, not beat (spec/04). It is a
        "retained artifact with a critic-input duty" — the `dropped_story` receipt
        rule is enforced against exactly these files, so this is not context, it is
        evidence, and it is rendered through the same `_findings_corpus` the
        manager prompts use so the two can never present a finding differently.
      - entity accounting is the TYPED COMPETITOR ROSTER (`state/programs/<id>/
        edges.json` + the shared `state/entities/` layer) rather than v1's flat
        watchlist. It carries the relation the critic's `relation_miscast` check
        weighs the item's own facts against.
      - the previous issue is this PROGRAM's most recent covering issue (issues are
        stored per program, spec/07) — the caller resolves it, walking past stubs.
      - the program block is new, because a read-through argues what a competitor
        means FOR THIS PROGRAM; a critic that does not know the program's target and
        `moa` cannot judge either `weak_read_through` or `relation_miscast`.

    The roster and program blocks are the manager's renderers reused verbatim
    (`_competitor_roster_v2`, `_program_block_v2`) — the critic must weigh exactly
    the roster the manager was held against, and two renderers would be a place for
    the accounting duty and its audit to drift.

    The same UnresolvedPlaceholder wall applies: a literal {{issue_json}} reaching
    Codex is an instruction to invent the thing it should be judging.
    """
    values = {
        "program_block": _program_block_v2(program),
        "issue_json": json.dumps(issue, indent=2, ensure_ascii=False),
        "findings_corpus": _findings_corpus(findings_by_aperture, unit="aperture"),
        "previous_issue_json": (
            json.dumps(previous_issue, indent=2, ensure_ascii=False)
            if previous_issue is not None
            else "(no previous issue)"
        ),
        "competitor_roster": _competitor_roster_v2(program, edges, entities),
        "thesis_json": json.dumps(thesis, indent=2, ensure_ascii=False),
        "surge_window": _surge_window_block(surge),
    }
    return _substitute(template, values)


def _blocking_findings_block(findings) -> str:
    """The validator's blocking findings as one `- kind at where: note` line each.

    Only blocking findings reach here — advisories are the record, not a to-do
    list, and including them would invite the manager to churn sections the gate
    never faulted. An empty list should never be rendered (the loop only retries
    on a block), so it surfaces as an explicit marker rather than a blank.
    """
    if not findings:
        return "(no blocking findings — nothing to fix)"
    return "\n".join(f"- {f.kind} at {f.where}: {f.note}" for f in findings)


def render_manager_retry_prompt(template: str, *, prior_draft: dict, blocking_findings) -> str:
    """Interpolate the validation-retry prompt. Raises if a placeholder is left.

    The manager receives exactly two things — its own prior draft and the
    blocking findings — because it EDITS that draft rather than regenerating it
    ([05](docs/spec/05-manager.md#in-the-retry-loop)). The same
    UnresolvedPlaceholder wall the other renderers use applies: a literal
    {{prior_draft_json}} reaching the model is an instruction to invent a draft.
    """
    values = {
        "prior_draft_json": json.dumps(prior_draft, indent=2, ensure_ascii=False),
        "blocking_findings": _blocking_findings_block(blocking_findings),
    }
    return _substitute(template, values)


def _critic_findings_block(findings) -> str:
    """The critic's blocking findings as retry instructions, one per finding.

    Unlike the validator's findings (Finding objects with .kind/.where/.note),
    these are the critic's dicts, and each may carry a `rebuttal` the critic has
    already REAFFIRMED. A reaffirmed finding is marked so the manager COMPLIES
    (retry 2) rather than rebutting a second time — the critic had final say. A
    fresh finding is open to a fix OR a sourced rebuttal; the template states that
    rule, this block only flags which findings have already been through it. An
    empty list should never render (the loop only retries on a block), so it
    surfaces as an explicit marker rather than a blank."""
    if not findings:
        return "(no blocking findings — nothing to fix)"
    lines = []
    for finding in findings:
        lines.append(
            f"- {finding.get('kind')} at {finding.get('where')}: {finding.get('note', '')}"
        )
        rebuttal = finding.get("rebuttal") or {}
        if rebuttal.get("adjudication") == REAFFIRMED:
            lines.append(
                "  REAFFIRMED by the critic — it weighed your rebuttal and stood by "
                "this finding. COMPLY now: edit the draft to fix it. Do not rebut again."
            )
    return "\n".join(lines)


# The per-round directive, filled into {{round_directive}}. Retry 1 opens the
# rebuttal channel; retry 2 (the final round) closes it — comply-only, so a
# reaffirmed finding cannot be rebutted a second time (spec/06 rebut-once).
_ROUND_REBUT = (
    "- For each FRESH finding you have a choice:\n"
    "    1. FIX it — edit the draft so the claim no longer outruns its sources. If\n"
    "       you fix a finding by removing a claim, record it in\n"
    "       quiet_this_cycle.critic_catches so the cut leaves a trace.\n"
    "    2. REBUT it — if you believe the finding is wrong, attach a `rebuttal` to\n"
    "       that finding inside critic_report.blocking_findings, of the shape\n"
    '       {"text": "...", "sources": [ <source objects> ]}: a sourced argument,\n'
    "       not an assertion. You may NOT silently ignore a finding.\n"
    "- For a finding marked REAFFIRMED, the critic already overruled your rebuttal\n"
    "  — COMPLY: fix it, do not rebut it again."
)
_ROUND_COMPLY = (
    "- This is your FINAL retry: the rebuttal channel is CLOSED. COMPLY with EVERY\n"
    "  finding below by editing the draft to fix it — the critic has had its say,\n"
    "  and any rebuttal you file now is ignored. A finding you do not fix publishes\n"
    "  with the dispute printed under a reader-visible banner."
)


def render_critic_retry_prompt(
    template: str, *, prior_draft: dict, blocking_findings, final_round: bool
) -> str:
    """Interpolate the critic-retry prompt. Raises if a placeholder is left.

    The manager receives exactly two things — its own prior draft and the critic's
    blocking findings — and EDITS the draft ([05](docs/spec/05-manager.md#the-rebuttal-channel)).
    `final_round` swaps the directive: retry 1 lets it fix OR file a sourced
    rebuttal; retry 2 is comply-only, so a reaffirmed finding cannot be rebutted
    twice. The same UnresolvedPlaceholder wall the other renderers use applies."""
    values = {
        "prior_draft_json": json.dumps(prior_draft, indent=2, ensure_ascii=False),
        "blocking_findings": _critic_findings_block(blocking_findings),
        "round_directive": _ROUND_COMPLY if final_round else _ROUND_REBUT,
    }
    return _substitute(template, values)
