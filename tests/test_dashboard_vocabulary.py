"""The dashboard's vocabularies must not drift from the spec that declares them (#82).

`dashboard/index.html` hand-mirrors four reader-facing vocabularies in JS: STATUS,
FLAG_LABEL, FINDING and DEGRADATION, plus the relation vocabulary in RELSCOPES.

WHY MIRROR AT ALL, rather than serving the strings from Python? Because these are
reader-facing ENGLISH, one fixed string per kind. Serving them would repeat the
same prose in every published issue — bytes on every fetch, forever — to spare a
test file. The mirror is the right call.

But a mirror without a drift test is how three declared kinds (`thesis_unseeded`,
`quiet_cycle`, `dossier_scan_cost_capped`) came to be missing from DEGRADATION
while the register in docs/spec/06 listed them. The page has a fail-visible path
for an unknown kind, so nothing crashed — it just rendered the raw slug at the
reader instead of the sentence, which is exactly the silent degradation the
register exists to prevent.

So: the spec table is the source of truth, parsed here, and the JS must cover it.
Adding a degradation to the register without teaching the page its wording now
fails in CI rather than in front of a reader.

These tests parse rather than execute — the suite has no JS runtime, and the
alternative (asserting nothing about the page) is what let the drift happen.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DASHBOARD = REPO / "dashboard" / "index.html"
REGISTER = REPO / "docs" / "spec" / "06-validator-and-critic.md"
RELATIONS = REPO / "docs" / "spec" / "03-state-and-governance.md"


def _js_object_keys(source: str, name: str) -> set[str]:
    """The keys of a top-level `const <name> = { ... }` literal in the page.

    Brace-counted rather than regex-matched to the closing brace, because these
    objects contain nested literals and a lazy `.*?` would stop at the first one.
    """
    start = source.index(f"const {name} = {{")
    depth, i = 0, source.index("{", start)
    for end in range(i, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                body = source[i + 1 : end]
                break
    else:  # pragma: no cover - a malformed page fails the parse tests first
        raise AssertionError(f"unbalanced braces in {name}")
    # keys at depth 1 only: `foo:` or `foo :`, not keys of nested objects
    keys, depth = set(), 0
    for token in re.finditer(r"[{}]|([A-Za-z_][A-Za-z0-9_]*)\s*:", body):
        if token.group(0) == "{":
            depth += 1
        elif token.group(0) == "}":
            depth -= 1
        elif depth == 0 and token.group(1):
            keys.add(token.group(1))
    return keys


def _table_column(markdown: str, heading: str, column: int) -> list[str]:
    """Every backticked value in one column of the markdown table under `heading`."""
    section = markdown.split(heading, 1)[1]
    values, in_body = [], False
    for line in section.splitlines():
        if not line.startswith("|"):
            if in_body:  # the table ended
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):  # the |---|---| separator
            in_body = True
            continue
        if not in_body:
            # The HEADER row, skipped deliberately: this register's own header
            # cell is literally `kind`, which the first run of this parser
            # happily collected as a degradation named "kind".
            continue
        if len(cells) > column:
            values.extend(re.findall(r"`([a-z_]+)`", cells[column]))
    return values


@pytest.fixture(scope="module")
def page() -> str:
    return DASHBOARD.read_text()


@pytest.fixture(scope="module")
def declared_degradations() -> set[str]:
    """The `kind` column of the degradation register (docs/spec/06)."""
    kinds = set(_table_column(REGISTER.read_text(), "### The register", 3))
    # The register is the reason this test exists; an empty parse would make every
    # assertion below vacuously true, which is the failure mode of drift tests.
    assert len(kinds) >= 8, f"parsed only {kinds} from the register — parser is broken"
    return kinds


@pytest.fixture(scope="module")
def declared_relations() -> list[str]:
    """The five typed relations (docs/spec/03 "The competitor model")."""
    rels = _table_column(RELATIONS.read_text(), "## The competitor model", 0)
    assert len(rels) == 5, f"expected 5 relations, parsed {rels}"
    return rels


class TestTheDegradationVocabulary:
    def test_every_declared_kind_has_reader_facing_wording(self, page, declared_degradations):
        known = _js_object_keys(page, "DEGRADATION")
        missing = declared_degradations - known
        assert not missing, (
            f"docs/spec/06 declares {sorted(missing)} but dashboard/index.html's "
            "DEGRADATION has no wording for them — the reader would see the raw slug"
        )

    def test_the_page_invents_no_kinds_the_spec_does_not_declare(self, page, declared_degradations):
        known = _js_object_keys(page, "DEGRADATION")
        # `source_unreachable` is an ADVISORY finding (spec/04 "errors[] is a
        # different animal"), not a register entry — it earns no exemption but is
        # still rendered, so it is legitimately in the page and not in the table.
        invented = known - declared_degradations - {"source_unreachable"}
        assert not invented, (
            f"dashboard styles {sorted(invented)} which no spec table declares — "
            "either the register is missing a row or the page is guessing"
        )

    def test_a_flag_exists_for_every_degradation(self, page, declared_degradations):
        """Manifest flags are how a marker reaches the reader BEFORE they open the
        issue (spec/08). A degradation with no flag label is invisible until the
        issue is already on screen."""
        flags = _js_object_keys(page, "FLAG_LABEL")
        missing = declared_degradations - flags
        assert not missing, f"no FLAG_LABEL wording for {sorted(missing)}"


class TestTheRelationVocabulary:
    """#80 — the five relations must all be renderable, and all be grouped."""

    def test_every_relation_has_a_badge_style(self, page, declared_relations):
        for rel in declared_relations:
            assert f".rel-{rel}" in page, f"no badge style for {rel} — it would render unstyled"

    def test_every_relation_is_placed_in_a_scope(self, page, declared_relations):
        """A relation missing from RELSCOPES does not render at all on the
        Competitor Set tab — it is filtered into no group and silently vanishes,
        which is worse than rendering it unstyled."""
        scopes = page.index("const RELSCOPES")
        body = page[scopes : page.index("const rows = allCompetitors()", scopes)]
        for rel in declared_relations:
            assert f"'{rel}'" in body, f"{rel} is in no scope — it would not render"

    def test_the_two_program_level_relations_are_visually_distinct(self, page):
        """The whole point of #50's refinement: an ADC win validates the target,
        not the mechanism. If those two badges paint the same, the page throws
        away the distinction the competitor model exists to carry."""
        mech = re.search(r"\.rel-mechanism_twin \{([^}]*)\}", page).group(1)
        targ = re.search(r"\.rel-target_twin \{([^}]*)\}", page).group(1)
        assert mech.strip() != targ.strip()
        # specifically: one is filled and one is not, which reads without colour
        assert "background:" in mech and "background:" not in targ

    def test_the_page_names_no_program(self, page):
        """dashboard/index.html is shared by every program. A hardcoded program
        name in the chrome is a bug the moment a second program is added — this
        caught real prose about HMBD-001 in the mechanism_twin empty state."""
        chrome = page[page.index("const RELSCOPES") :]
        assert "HMBD" not in chrome, "the shared chrome names a specific program"


class TestTheCompanyResolverMirror:
    """The dashboard resolves a holder name to a `co_` id IN THE BROWSER (#98).

    It has to: the page turns "Daiichi Sankyo" into a link with no server round
    trip. That means the slug rules exist twice, and two lists that drift produce
    dead links for exactly the companies whose names are written two ways — the
    case the resolver exists for. So the mirror is tested, like DEGRADATION.
    """

    def test_the_legal_form_tokens_match_the_planner(self, page):
        from researchswarm.apertures import _LEGAL_FORM_TOKENS

        block = page[page.index("const LEGAL_FORM_TOKENS") : page.index("function companyIdFromName")]
        in_page = set(re.findall(r"'([a-z]+)'", block))
        assert in_page == set(_LEGAL_FORM_TOKENS), (
            "dashboard and apertures disagree on legal-form tokens; "
            f"page-only={in_page - set(_LEGAL_FORM_TOKENS)}, "
            f"python-only={set(_LEGAL_FORM_TOKENS) - in_page}"
        )

    def test_kgaa_is_absent_from_the_page_too(self, page):
        """Stripping it maps "Merck KGaA" onto `co_merck` — the same id as
        "Merck & Co.", two entirely different companies. The omission is
        load-bearing in both copies or it is load-bearing in neither."""
        block = page[page.index("const LEGAL_FORM_TOKENS") : page.index("function companyIdFromName")]
        assert "kgaa" not in block

    def test_the_prefix_matches(self, page):
        from researchswarm.apertures import COMPANY_ID_PREFIX

        block = page[page.index("function companyIdFromName") : page.index("let COMPANIES")]
        assert f"'{COMPANY_ID_PREFIX}'" in block

    def test_a_holder_without_a_dossier_is_not_a_link(self, page):
        """"We have not looked yet" is a rendered state. A link promising a page
        that does not exist is worse than plain text."""
        block = page[page.index("function holderHTML") : page.index("function holdersHTML")]
        assert "COMPANIES.has(id)" in block
        assert "return esc(name)" in block

    def test_the_drift_log_is_rendered(self, page):
        """The append-only history is the entire reason the company view exists;
        a view that showed only current values would be an about-page."""
        assert "function coDrift" in page
        assert "coDrift(dossier.drift_log)" in page

    def test_thin_sections_render_at_the_point_of_the_absence(self, page):
        """A sparse dossier must read as unmeasured, never as a small company —
        the rank-1 blind spot is China-listed competitors."""
        block = page[page.index("function coSection") : page.index("function coDrift")]
        assert "thin" in block and "not measured" in block


class TestTheManagerPromptVocabulary:
    """The manager prompt must offer the SAME closed vocabulary the register declares.

    The first live v2 run coined `no_in_window_item` — a kind that exists nowhere,
    reaching the dashboard as a raw string. The prompt now lists the kinds inline,
    and a list in a prompt drifts from the register exactly the way the dashboard's
    did, so it is pinned the same way.
    """

    def test_the_prompt_offers_exactly_the_declared_kinds(self, declared_degradations):
        prompt = (REPO / "prompts" / "manager-v2.md").read_text()
        block = prompt[prompt.index("`kind` IS A CLOSED VOCABULARY") :]
        block = block[: block.index("If a window simply contained")]
        offered = set(re.findall(r"[a-z][a-z_]{6,}", block)) & declared_degradations
        missing = declared_degradations - offered
        assert not missing, (
            f"the manager prompt does not offer {missing}; a kind the manager is "
            "never shown is a kind it will coin a replacement for"
        )

    def test_the_prompt_names_holders_as_required(self):
        """`holders` is the only path a company has into the roster. The first live
        run returned it as None on every competitor, which is why it is now stated
        as REQUIRED in the schema block rather than listed among the fields."""
        prompt = (REPO / "prompts" / "manager-v2.md").read_text()
        assert '"holders"' in prompt, "the worked competitor example must show holders"
        assert "holders` is REQUIRED" in prompt

    def test_the_prompt_forbids_the_invented_fields(self):
        """The first live run emitted `relationship`, `movements` and
        `why_it_matters` — a schema it made up. Naming them as forbidden is
        cheaper than another failed run discovering it again."""
        prompt = (REPO / "prompts" / "manager-v2.md").read_text()
        for invented in ("relationship", "movements", "why_it_matters"):
            assert invented in prompt, f"the prompt should explicitly rule out {invented}"

    def test_the_prompt_forbids_double_accounting(self):
        """Listing an entity in competitors[] AND no_news reads as "this moved" and
        "this did not move" in the same issue. The live run did it for every entity."""
        prompt = (REPO / "prompts" / "manager-v2.md").read_text()
        assert "EXACTLY ONE of those places, never both" in prompt
