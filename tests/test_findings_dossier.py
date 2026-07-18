"""The dossier findings contract (spec #92) — the seam a dossier_scan validates at.

A dossier payload rides the v2 envelope but is a different shape: no coverage
window (the scan's subject is history), one `dossier` record, and a hard refusal
of interpretation. These tests exercise the seam as external behaviour — a
payload in, a verdict out — and never reach a model: the module is pure, and the
autouse offline guard in conftest holds for the whole file.

Three groups, in the order they matter:

1. the happy shape, so the contract is known to be satisfiable at all;
2. the refusals — manager-only fields, and interpretation, which is the one this
   spec adds;
3. adversarial shape, which is the group with the most weight. A gate that
   CRASHES is strictly worse than one that misses, because it takes the run down
   after publishing. This repo has shipped that bug five times.
"""

from __future__ import annotations

import pytest

from researchswarm.findings import (
    DOSSIER_INTERPRETATION_FIELDS,
    DOSSIER_SCAN_KIND,
    DOSSIER_SECTIONS,
    INTERPRETATION_FIELDS_V2,
    FindingsInvalid,
    dossier_subject,
    validate_dossier_findings,
    validate_findings_v2,
)

RUN_ID = "run_20260718_0700"
PROGRAM_ID = "hmbd-001"
ENTITY_ID = "co_remegen"
APERTURE_ID = f"{DOSSIER_SCAN_KIND}:{ENTITY_ID}"


# ---------------------------------------------------------------------------
# builders — one valid payload, mutated per test, so a test names exactly the
# one thing it is about
# ---------------------------------------------------------------------------


def a_source(**overrides) -> dict:
    source = {
        "url": "https://www.hkexnews.hk/example",
        "publisher": "HKEX",
        "tier": "primary",
        "published_at": "2021-03-31",
        "paywalled": False,
    }
    source.update(overrides)
    return source


def provenance(**overrides) -> dict:
    block = {"established_by": RUN_ID, "sources": [a_source()]}
    block.update(overrides)
    return block


def a_dossier(**overrides) -> dict:
    dossier = {
        "entity_id": ENTITY_ID,
        "kind": "company",
        "as_of": "2026-07-19",
        "identity": {
            "legal_name": "RemeGen Co., Ltd.",
            "aliases": ["RemeGen"],
            "founded": "2008",
            "hq": "Yantai, China",
            "status": "public",
            "listings": [{"exchange": "HKEX", "ticker": "9995"}],
            "provenance": provenance(),
        },
        "funding": {
            "total_raised": "USD 1.1B",
            "rounds": [],
            "provenance": provenance(),
        },
        "pipeline": [
            {
                "asset_entity_id": "asset_disitamab_vedotin",
                "indication": "gastric cancer",
                "phase": "3",
                "provenance": provenance(),
            }
        ],
        "deals": [
            {
                "date": "2021-08-09",
                "type": "license",
                "counterparty": "Seagen",
                "direction": "out",
                "upfront": "USD 200M",
                "provenance": provenance(),
            }
        ],
        "people": [],
        "pivots": [
            {
                "date": "2023-01-01",
                "from": "broad ADC platform",
                "to": "autoimmune-first",
                "trigger": "financing pressure",
                "provenance": provenance(),
            }
        ],
        "setbacks": [
            {
                "date": "2024-06-01",
                "kind": "discontinuation",
                "detail": "an early ADC programme was dropped",
                "provenance": provenance(),
            }
        ],
        "coverage": {"thin_sections": ["people"], "degradation": None},
    }
    dossier.update(overrides)
    return dossier


def a_payload(**overrides) -> dict:
    payload = {
        "aperture": APERTURE_ID,
        "program_id": PROGRAM_ID,
        "run_id": RUN_ID,
        "coverage_window": None,
        "quiet": False,
        "findings": [],
        "dossier": a_dossier(),
        "coverage_notes": {
            "scope_run": ["HKEX filings", "ClinicalTrials.gov sponsor history"],
            "entities_checked": [ENTITY_ID],
            "notes": "HKEX coverage thin on people",
        },
        "errors": [],
    }
    payload.update(overrides)
    return payload


def validate(payload, **overrides):
    kwargs = {
        "aperture_id": APERTURE_ID,
        "program_id": PROGRAM_ID,
        "run_id": RUN_ID,
        "known_entity_ids": {ENTITY_ID},
    }
    kwargs.update(overrides)
    return validate_dossier_findings(payload, **kwargs)


# ---------------------------------------------------------------------------
# the shape
# ---------------------------------------------------------------------------


class TestTheDossierPayloadShape:
    def test_a_dossier_payload_is_accepted(self):
        validate(a_payload())

    def test_the_kind_is_the_string_the_aperture_roster_uses(self):
        """findings.py spells the kind literally, like HOUSE_SWEEP_KIND, to keep
        the dependency pointing the right way — the seam contract is the bottom of
        the stack and must not import the planner to learn one string. Pinned here
        so the literal cannot be edited casually; the planner's own constant is
        asserted against it from the aperture tests, which own that module."""
        assert DOSSIER_SCAN_KIND == "dossier_scan"
        assert APERTURE_ID == "dossier_scan:co_remegen"

    def test_the_seam_dispatches_on_the_aperture_kind(self):
        """One contract governs all model output: the v2 entry point routes a
        dossier payload rather than the call site branching."""
        validate_findings_v2(
            a_payload(),
            aperture_id=APERTURE_ID,
            program_id=PROGRAM_ID,
            run_id=RUN_ID,
            window={"from": "2026-07-11", "to": "2026-07-18"},
            known_entity_ids={ENTITY_ID},
            aperture_kind=DOSSIER_SCAN_KIND,
        )

    def test_a_v2_shaped_payload_is_rejected_at_the_dossier_seam(self):
        payload = a_payload(
            coverage_window={"from": "2026-07-11", "to": "2026-07-18"}, dossier=None
        )
        with pytest.raises(FindingsInvalid) as exc:
            validate(payload)
        assert "window-exempt" in str(exc.value)

    def test_the_window_exemption_is_explicit_not_incidental(self):
        """A missing key and a null value read the same to a careless gate. The
        key is required so the exemption is a declared fact in the payload."""
        payload = a_payload()
        del payload["coverage_window"]
        with pytest.raises(FindingsInvalid, match="coverage_window"):
            validate(payload)

    def test_a_dossier_for_another_company_is_rejected(self):
        """A dossier of the wrong company is worse than a missing one — it looks
        like coverage."""
        payload = a_payload(dossier=a_dossier(entity_id="co_akeso"))
        with pytest.raises(FindingsInvalid, match="not this aperture's subject"):
            validate(payload)

    def test_findings_answering_for_another_program_are_rejected(self):
        payload = a_payload(program_id="some-other-drug")
        with pytest.raises(FindingsInvalid, match="does not match this program"):
            validate(payload)

    def test_an_asset_record_is_not_a_dossier(self):
        payload = a_payload(dossier=a_dossier(kind="asset"))
        with pytest.raises(FindingsInvalid, match="company record"):
            validate(payload)

    @pytest.mark.parametrize("as_of", ["last quarter", "2026", 20260719, None])
    def test_as_of_must_be_a_comparable_date(self, as_of):
        """Staleness is only detectable if `as_of` is a date a check can compare."""
        payload = a_payload(dossier=a_dossier(as_of=as_of))
        with pytest.raises(FindingsInvalid, match="as_of"):
            validate(payload)

    @pytest.mark.parametrize("section", ["identity", "pivots", "setbacks", "coverage"])
    def test_the_required_sections_must_be_present(self, section):
        """pivots and setbacks are required precisely because they are the fields
        a model would otherwise skip: required-but-empty is a claim, absent is
        silence, and the receipt rule needs the difference."""
        dossier = a_dossier()
        del dossier[section]
        with pytest.raises(FindingsInvalid, match=section):
            validate(a_payload(dossier=dossier))

    def test_empty_pivots_and_setbacks_are_a_legitimate_answer(self):
        validate(a_payload(dossier=a_dossier(pivots=[], setbacks=[])))

    @pytest.mark.parametrize(
        "section,entry",
        [
            ("identity", {"legal_name": "X", "status": "listed-ish"}),
            ("deals", {"type": "handshake"}),
            ("deals", {"direction": "sideways"}),
            ("setbacks", {"kind": "bad news"}),
        ],
    )
    def test_the_shape_tables_enums_are_range_checked(self, section, entry):
        dossier = a_dossier()
        payload_entry = {**entry, "provenance": provenance()}
        dossier[section] = payload_entry if section == "identity" else [payload_entry]
        with pytest.raises(FindingsInvalid, match="not in"):
            validate(a_payload(dossier=dossier))

    def test_an_omitted_enum_is_honest_reporting_not_a_breach(self):
        """The ternary receipt rule: an unsourceable claim is omitted, so a
        missing `status` is honest while 'probably public' is a broken contract."""
        identity = a_dossier()["identity"]
        del identity["status"]
        validate(a_payload(dossier=a_dossier(identity=identity)))

    def test_a_thin_section_must_name_a_real_section(self):
        """The China-coverage gap is marked at the point of the absence; a
        thin_section naming nothing is a marker pointing nowhere."""
        dossier = a_dossier(coverage={"thin_sections": ["accounting"], "degradation": None})
        with pytest.raises(FindingsInvalid, match="not a dossier section"):
            validate(a_payload(dossier=dossier))

    def test_every_shape_table_row_is_gated(self):
        """The table IS the coverage: a row added without a gate is the drift this
        mechanism exists to prevent, so every row must be reachable by name."""
        names = [section.name for section in DOSSIER_SECTIONS]
        assert len(names) == len(set(names))
        for section in DOSSIER_SECTIONS:
            assert section.container in {"object", "array"}
            dossier = a_dossier()
            dossier[section.name] = "prose where a container belongs"
            with pytest.raises(FindingsInvalid, match=section.name):
                validate(a_payload(dossier=dossier))


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_a_fact_without_provenance_is_rejected(self):
        identity = a_dossier()["identity"]
        del identity["provenance"]
        with pytest.raises(FindingsInvalid, match="provenance is required"):
            validate(a_payload(dossier=a_dossier(identity=identity)))

    def test_an_array_entry_carries_its_own_provenance(self):
        """Per field, not per record: a refresh appends, and without a per-fact
        citation the reader cannot tell which claim came from which scan."""
        dossier = a_dossier(setbacks=[{"date": "2024-06-01", "kind": "layoff"}])
        with pytest.raises(FindingsInvalid, match=r"setbacks\[0\]"):
            validate(a_payload(dossier=dossier))

    def test_provenance_must_cite_this_run(self):
        identity = a_dossier()["identity"]
        identity["provenance"] = provenance(established_by="run_19990101_0000")
        with pytest.raises(FindingsInvalid, match="does not match this run"):
            validate(a_payload(dossier=a_dossier(identity=identity)))

    def test_a_dossier_claim_with_no_source_does_not_exist(self):
        identity = a_dossier()["identity"]
        identity["provenance"] = provenance(sources=[])
        with pytest.raises(FindingsInvalid, match="provenance.sources"):
            validate(a_payload(dossier=a_dossier(identity=identity)))

    def test_dossier_sources_obey_the_same_tiering_as_every_other_claim(self):
        identity = a_dossier()["identity"]
        identity["provenance"] = provenance(sources=[a_source(tier="a blog i like")])
        with pytest.raises(FindingsInvalid, match="tier"):
            validate(a_payload(dossier=a_dossier(identity=identity)))

    def test_the_coverage_section_is_the_one_exempt_from_provenance(self):
        """thin-section marking is self-assessment about the scan, not a sourced
        fact about the company."""
        validate(a_payload(dossier=a_dossier(coverage={"thin_sections": [], "degradation": None})))


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


class TestManagerOnlyFieldsAreRejected:
    @pytest.mark.parametrize("field", INTERPRETATION_FIELDS_V2)
    def test_every_manager_only_field_is_rejected_in_the_record(self, field):
        dossier = a_dossier()
        dossier[field] = "x"
        with pytest.raises(FindingsInvalid, match="manager's to author"):
            validate(a_payload(dossier=dossier))

    def test_a_leaked_read_through_keeps_the_existing_message(self):
        """The wording is the contract's voice and other apertures already use
        it; a second phrasing for the same breach teaches two lessons."""
        dossier = a_dossier()
        dossier["read_through"] = "bad for us"
        with pytest.raises(
            FindingsInvalid,
            match="'read_through' is the manager's to author — researchers report facts",
        ):
            validate(a_payload(dossier=dossier))

    def test_the_ban_reaches_a_dossier_scans_findings_too(self):
        payload = a_payload(
            quiet=False,
            findings=[
                {
                    "summary": "RemeGen disclosed a restructuring in its interim report.",
                    "entity_ids": [ENTITY_ID],
                    "sources": [a_source()],
                    "priority_hint": "medium",
                    "read_through": "they are retreating",
                }
            ],
        )
        with pytest.raises(FindingsInvalid, match="read_through"):
            validate(payload)


class TestInterpretationIsRefused:
    """The refusal this spec adds.

    A dossier is SHARED across programs; a read-through is not. A researcher
    saying what a company MEANS is not merely early — it is authoring an opinion
    that a second program would inherit as if it were a fact.
    """

    @pytest.mark.parametrize("field", DOSSIER_INTERPRETATION_FIELDS)
    def test_every_interpretive_field_is_refused(self, field):
        dossier = a_dossier()
        dossier[field] = "they are the biggest threat to HMBD-001"
        with pytest.raises(FindingsInvalid, match="relation edge"):
            validate(a_payload(dossier=dossier))

    def test_the_refusal_speaks_in_the_same_voice_as_the_read_through_ban(self):
        dossier = a_dossier()
        dossier["threat_level"] = "high"
        with pytest.raises(FindingsInvalid, match="manager's to author") as exc:
            validate(a_payload(dossier=dossier))
        message = str(exc.value)
        assert "a dossier holds facts" in message
        assert "every program shares" in message

    def test_interpretation_hiding_at_depth_is_still_refused(self):
        """The opinion a top-level gate catches reappears as
        `setbacks[0].what_it_means`. Nesting is not a loophole."""
        dossier = a_dossier(
            setbacks=[
                {
                    "date": "2024-06-01",
                    "kind": "discontinuation",
                    "what_it_means": "their ADC platform is failing",
                    "provenance": provenance(),
                }
            ]
        )
        with pytest.raises(FindingsInvalid, match=r"setbacks\[0\].what_it_means"):
            validate(a_payload(dossier=dossier))

    def test_facts_that_merely_sound_evaluative_are_still_facts(self):
        """The ban is on named interpretive SLOTS, not on vocabulary. A pivot's
        `trigger` and `outcome` are what a company did, and must survive."""
        dossier = a_dossier(
            pivots=[
                {
                    "date": "2023-01-01",
                    "from": "broad ADC platform",
                    "to": "autoimmune-first",
                    "trigger": "a failed phase 3",
                    "outcome": "two programmes discontinued",
                    "evidence": ["https://www.hkexnews.hk/example"],
                    "provenance": provenance(),
                }
            ]
        )
        validate(a_payload(dossier=dossier))


# ---------------------------------------------------------------------------
# silence vs nothing
# ---------------------------------------------------------------------------


class TestSilenceIsDistinguishableFromNothing:
    def test_a_scan_that_found_nothing_is_quiet_and_valid(self):
        validate(a_payload(quiet=True, dossier=None, findings=[]))

    def test_a_populated_dossier_under_quiet_is_the_ambiguity_we_forbid(self):
        with pytest.raises(FindingsInvalid, match="quiet is true"):
            validate(a_payload(quiet=True))

    def test_a_scan_claiming_it_was_busy_must_show_something(self):
        with pytest.raises(FindingsInvalid, match="returned nothing"):
            validate(a_payload(quiet=False, dossier=None, findings=[]))

    def test_coverage_notes_are_required_quiet_or_busy(self):
        with pytest.raises(FindingsInvalid, match="coverage_notes"):
            validate(a_payload(quiet=True, dossier=None, coverage_notes=None))


# ---------------------------------------------------------------------------
# adversarial shape — the group with the most weight
# ---------------------------------------------------------------------------


ADVERSARIAL = [
    None,
    "the dossier is attached",
    42,
    [],
    ["a", "b"],
    {},
    {"dossier": None},
    {"dossier": "RemeGen is a Chinese biotech."},
    {"dossier": []},
    {"dossier": [{"identity": {}}]},
    {"dossier": {"identity": "RemeGen Co., Ltd."}},
    {"dossier": {"identity": ["RemeGen"]}},
    {"dossier": {"identity": {"legal_name": None}}},
    {"dossier": {"identity": {"provenance": "HKEX"}}},
    {"dossier": {"identity": {"provenance": {"sources": "https://x"}}}},
    {"dossier": {"identity": {"provenance": {"sources": ["https://x"]}}}},
    {"dossier": {"setbacks": "none found"}},
    {"dossier": {"setbacks": ["a discontinuation"]}},
    {"dossier": {"setbacks": [None]}},
    {"dossier": {"setbacks": [["nested"]]}},
    {"dossier": {"coverage": {"thin_sections": "everything"}}},
    {"dossier": {"coverage": []}},
    {"dossier": {"entity_id": {"id": ENTITY_ID}}},
    {"dossier": {"as_of": ["2026-07-19"]}},
    {"dossier": {"kind": 7}},
    {"quiet": "false"},
    {"findings": "none"},
    {"findings": [None]},
    {"findings": ["a finding"]},
    {"findings": [{"sources": "https://x"}]},
    {"coverage_notes": []},
    {"coverage_notes": {"scope_run": None}},
    {"aperture": None},
    {"run_id": None},
]


class TestAdversarialShapeNeverCrashes:
    """The load-bearing group.

    A gate that crashes is strictly worse than one that misses: it takes the run
    down AFTER publishing, which is how this failure has shipped five times. Every
    case below must produce a verdict — a FindingsInvalid or a clean pass — and
    never a traceback.
    """

    @pytest.mark.parametrize("case", ADVERSARIAL, ids=lambda c: repr(c)[:60])
    def test_it_returns_a_verdict_never_a_traceback(self, case):
        payload = case if not isinstance(case, dict) else a_payload(**case)
        try:
            validate(payload)
        except FindingsInvalid:
            pass

    def test_deep_nesting_does_not_blow_the_stack(self):
        deep = inner = {}
        for _ in range(400):
            inner["next"] = {}
            inner = inner["next"]
        dossier = a_dossier()
        dossier["origin"] = {"founding_story": "spun out", "nest": deep, "provenance": provenance()}
        with pytest.raises(FindingsInvalid, match="nested deeper"):
            validate(a_payload(dossier=dossier))

    def test_non_string_keys_do_not_crash_the_walk(self):
        """`json.loads` cannot produce these, but a caller handing over a
        hand-built dict can, and the gate is the last thing that may assume."""
        dossier = a_dossier()
        dossier["origin"] = {1: "one", None: "none", "provenance": provenance()}
        validate(a_payload(dossier=dossier))

    @pytest.mark.parametrize(
        "aperture_id", [None, 42, "house_sweep", "dossier_scan:", "dossier_scan"]
    )
    def test_the_subject_parser_is_total(self, aperture_id):
        assert dossier_subject(aperture_id) is None

    def test_the_subject_parser_reads_a_real_aperture_id(self):
        assert dossier_subject(APERTURE_ID) == ENTITY_ID

    def test_a_wholly_broken_payload_reports_every_problem_at_once(self):
        """One retry should fix everything rather than peel an onion."""
        with pytest.raises(FindingsInvalid) as exc:
            validate({"aperture": None, "dossier": {"identity": "prose"}})
        message = str(exc.value)
        assert message.count(";") >= 3


# ---------------------------------------------------------------------------
# The sixth crash — unhashable values at a membership test
# ---------------------------------------------------------------------------


UNHASHABLE = pytest.mark.parametrize(
    "value", [{"oops": 1}, ["oops"], {"oops"}], ids=["dict", "list", "set"]
)


class TestUnhashableValuesAtEveryMembershipTest:
    """`x not in frozenset` raises TypeError when x is a dict, list or set.

    That is a real payload: JSON objects and arrays can appear anywhere a model
    chooses to put them, including where an enum belongs. Before this was fixed
    the TypeError escaped `validate_findings_v2` — whose whole contract is that
    it raises nothing but `FindingsInvalid` — and `researcher.py` catches only
    `(TransportInvalid, FindingsInvalid)`, so the escape SKIPPED THE RETRY LOOP
    and killed the run. It affected every v2 aperture, not just this one.

    The rule now: an unhashable value is not a member, and the caller files its
    ordinary typed finding naming the offending value. A verdict the model can
    act on, never a traceback.
    """

    @UNHASHABLE
    def test_a_dossier_enum(self, value):
        dossier = a_dossier()
        dossier["identity"]["status"] = value
        with pytest.raises(FindingsInvalid, match="status"):
            validate(a_payload(dossier=dossier))

    @UNHASHABLE
    def test_a_section_enum_inside_an_array(self, value):
        dossier = a_dossier()
        dossier["setbacks"] = [
            {"date": "2023-03-02", "kind": value, "provenance": provenance()}
        ]
        with pytest.raises(FindingsInvalid, match="kind"):
            validate(a_payload(dossier=dossier))

    @UNHASHABLE
    def test_a_thin_section_name(self, value):
        dossier = a_dossier()
        dossier["coverage"] = {"thin_sections": [value]}
        with pytest.raises(FindingsInvalid, match="not a dossier section"):
            validate(a_payload(dossier=dossier))

    @UNHASHABLE
    def test_a_source_tier(self, value):
        dossier = a_dossier()
        dossier["identity"]["provenance"] = provenance(sources=[a_source(tier=value)])
        with pytest.raises(FindingsInvalid, match="tier"):
            validate(a_payload(dossier=dossier))

    @UNHASHABLE
    def test_a_priority_hint_on_a_finding(self, value):
        finding = {
            "summary": "a dated event surfaced while reading history",
            "priority_hint": value,
            "entity_ids": [ENTITY_ID],
            "sources": [a_source()],
        }
        with pytest.raises(FindingsInvalid, match="priority_hint"):
            validate(a_payload(findings=[finding], quiet=False))

    @UNHASHABLE
    def test_an_entity_id_on_a_finding(self, value):
        finding = {
            "summary": "a dated event",
            "priority_hint": "medium",
            "entity_ids": [value],
            "sources": [a_source()],
        }
        with pytest.raises(FindingsInvalid, match="not on the roster"):
            validate(a_payload(findings=[finding], quiet=False))

    @UNHASHABLE
    def test_through_the_public_seam(self, value):
        """The seam is what `researcher.py` calls, so it is the surface that
        matters — `validate_findings_v2`, dispatching on the aperture kind."""
        dossier = a_dossier()
        dossier["identity"]["status"] = value
        with pytest.raises(FindingsInvalid):
            validate_findings_v2(
                a_payload(dossier=dossier),
                aperture_id=APERTURE_ID,
                program_id=PROGRAM_ID,
                run_id=RUN_ID,
                window={"from": "2026-07-12", "to": "2026-07-18"},
                known_entity_ids={ENTITY_ID},
                aperture_kind=DOSSIER_SCAN_KIND,
            )

    @UNHASHABLE
    def test_the_crash_no_longer_masks_the_interpretation_ban(self, value):
        """The A/B that proves D4 is closed.

        The unhashable enum used to raise inside the record walk, where a blanket
        `except Exception` turned it into "dossier: unreadable (TypeError…)" and
        ABORTED the rest of the walk — including `_check_banned_fields`, the
        interpretation ban that is this aperture's stated reason to exist. So a
        payload smuggling a banned field could hide it behind one malformed enum,
        and the retry message degraded to something no model could act on.

        Both problems must now be reported, together, in one verdict.
        """
        dossier = a_dossier()
        dossier["identity"]["status"] = value
        dossier["identity"]["threat_level"] = "high"
        with pytest.raises(FindingsInvalid) as exc:
            validate(a_payload(dossier=dossier))
        message = str(exc.value)
        assert "threat_level" in message
        assert "unreadable" not in message
        assert "TypeError" not in message

    def test_the_ban_alone_still_reports(self):
        """The other half of the A/B: without the enum, the ban reported fine.
        Kept beside it so the pair reads as one experiment."""
        dossier = a_dossier()
        dossier["identity"]["threat_level"] = "high"
        with pytest.raises(FindingsInvalid, match="threat_level"):
            validate(a_payload(dossier=dossier))
