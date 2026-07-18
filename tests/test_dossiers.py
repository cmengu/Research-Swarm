"""The company dossier store — provenance, append-only correction, thin marking.

These tests exercise EXTERNAL BEHAVIOUR at the two seams this module owns: what
`build_company_dossier_record` returns given a prior record and a payload, and
what `apply_company_dossier_v2` writes given the same. Nothing here asserts on
internals, and nothing here reaches a model, the network, or git —
`researchswarm.dossiers` has no runner to inject because it makes no calls; the
autouse `offline` fixture in conftest still guards the suite.

The weight is deliberately unbalanced toward the adversarial-shape class, per
#92's testing decisions: "the gate must not crash on null, prose, wrong container
or wrong depth anywhere in the dossier. A gate that crashes is worse than one
that misses, since it takes the run down after publishing." This repo has shipped
that bug five times. Every public entry point is therefore called with the same
hostile corpus in `test_no_public_entry_point_crashes_on_hostile_input`.

Spec: docs/spec/03-state-and-governance.md, issue #92
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from researchswarm.dossiers import (
    DOSSIER_KIND,
    DOSSIER_SECTIONS,
    INTERPRETIVE_FIELDS,
    Coverage,
    Deal,
    Identity,
    Person,
    Pivot,
    Setback,
    apply_asset_company_link_v2,
    apply_company_dossier_v2,
    asset_record_path,
    assets_of_company,
    build_asset_company_link,
    build_company_dossier_record,
    company_dossier_path,
    company_for_asset,
    interpretation_violations,
    load_asset_record,
    load_company_dossier,
    load_company_dossiers,
    mentioned_sections,
    normalize_dossier_payload,
    thin_sections,
)

RUN = "run_20260719_0700"
LATER_RUN = "run_20260820_0700"
ISSUE = "2026-07-19"
DATE = "2026-07-19"
NOW = datetime(2026, 7, 19, 7, 0)
ENTITY = "co_remegen"


def _full_payload() -> dict:
    """A dossier with all eight sections populated — the not-thin baseline.

    Modelled on a China-listed competitor on purpose: the China blind spot is
    what #92's coverage decision is about, and the format must not privilege US
    issuers (story 26).
    """
    return {
        "identity": {
            "legal_name": "RemeGen Co., Ltd.",
            "aliases": ["RemeGen", "荣昌生物"],
            "founded": "2008",
            "hq": "Yantai, China",
            "status": "public",
            "listings": [{"exchange": "HKEX", "ticker": "9995"}],
        },
        "origin": {
            "founding_story": "Spun out of a protein-engineering group.",
            "founders": ["Fang Jianmin"],
            "spun_out_of": "RemeGen Biosciences",
            "founding_thesis": "ADC platform for China-first oncology.",
        },
        "funding": {
            "total_raised": "$1.2B",
            "rounds": [{"date": "2019-11-01", "stage": "Series C", "amount": "$100M", "lead": "Lilly Asia"}],
            "ipo": {"date": "2020-11-09", "exchange": "HKEX", "raised": "$515M", "price": "HK$52.10"},
        },
        "pipeline": [
            {"asset_entity_id": "asset_rc148", "indication": "squamous NSCLC", "phase": "3", "status": "enrolling"}
        ],
        "deals": [
            {
                "date": "2026-01-05",
                "type": "license",
                "counterparty": "AbbVie",
                "direction": "out",
                "upfront": "$150M",
            }
        ],
        "people": [{"name": "Fang Jianmin", "role": "CEO", "since": "2008", "prior": ["Tongji University"]}],
        "pivots": [
            {
                "date": "2024-06-01",
                "from": "autoimmune-first",
                "to": "oncology ADC-first",
                "trigger": "telitacicept readout",
                "evidence": ["https://example.invalid/filing"],
            }
        ],
        "setbacks": [
            {"date": "2025-03-01", "kind": "discontinuation", "detail": "RC88 halted", "program": "RC88"}
        ],
    }


# ---------------------------------------------------------------------------
# Shape — the schema block of #92, made executable
# ---------------------------------------------------------------------------


def test_normalize_returns_exactly_the_eight_spec_sections():
    """The section table IS the contract. If it drifts, gate coverage drifts."""
    assert set(normalize_dossier_payload(_full_payload())) == set(DOSSIER_SECTIONS)
    assert DOSSIER_SECTIONS == (
        "identity",
        "origin",
        "funding",
        "pipeline",
        "deals",
        "people",
        "pivots",
        "setbacks",
    )


def test_pivot_serializes_to_the_spec_key_names_not_the_python_ones():
    """`from`/`to` are the JSON contract; `from_`/`to_` are a Python workaround."""
    row = Pivot.from_payload({"from": "A", "to": "B"}).to_dict()
    assert row == {"from": "A", "to": "B"}


def test_closed_vocabularies_drop_unknown_values_rather_than_defaulting():
    """A hallucinated enum must read as absent, never as a confident wrong answer."""
    assert Identity.from_payload({"status": "semi-public"}).status is None
    assert Deal.from_payload({"type": "handshake", "direction": "sideways"}).to_dict() == {}
    assert Setback.from_payload({"kind": "vibes", "detail": "d"}).to_dict() == {"detail": "d"}


def test_a_bare_string_is_promoted_where_a_list_of_strings_belongs():
    """`"founders": "Jane Doe"` is a container mistake, not a missing fact."""
    assert Person.from_payload({"name": "X", "prior": "Genentech"}).prior == ["Genentech"]


# ---------------------------------------------------------------------------
# Provenance and append-only correction — stories 13 and 14
# ---------------------------------------------------------------------------


def test_every_section_carries_the_run_and_issue_that_established_it():
    record, changed = build_company_dossier_record(
        None, _full_payload(), entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    assert changed is True
    assert record["entity_id"] == ENTITY
    assert record["kind"] == DOSSIER_KIND
    assert record["as_of"] == DATE
    for name in DOSSIER_SECTIONS:
        fact = record["facts"][name]
        assert set(fact) == {"value", "established_by", "issue"}, name
        assert fact["established_by"] == RUN
        assert fact["issue"] == ISSUE


def test_a_correction_appends_a_drift_entry_and_keeps_the_prior_value_readable():
    """Story 14: corrections append, so what we believed before survives."""
    first, _ = build_company_dossier_record(
        None, {"identity": {"legal_name": "RemeGen Ltd"}}, entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    second, changed = build_company_dossier_record(
        first,
        {"identity": {"legal_name": "RemeGen Co., Ltd."}},
        entity_id=ENTITY,
        run_id=LATER_RUN,
        issue_id="2026-08-20",
        date="2026-08-20",
    )
    assert changed is True
    assert second["facts"]["identity"]["value"]["legal_name"] == "RemeGen Co., Ltd."
    assert second["facts"]["identity"]["established_by"] == LATER_RUN

    corrections = [e for e in second["drift_log"] if e["action"] == "corrected"]
    assert len(corrections) == 1
    assert corrections[0]["from"] == {"legal_name": "RemeGen Ltd"}
    assert corrections[0]["to"] == {"legal_name": "RemeGen Co., Ltd."}
    assert corrections[0]["run_id"] == LATER_RUN
    # And the establishing entry is still there: the log is append-only.
    assert [e["action"] for e in second["drift_log"]] == ["established", "corrected"]


def test_an_unchanged_refresh_is_a_no_op():
    """A quarterly refresh that finds nothing new must produce an empty diff.

    Otherwise the drift log fills with restatements and stops being readable as
    a history, which is the only thing it is for.
    """
    payload = _full_payload()
    first, _ = build_company_dossier_record(
        None, payload, entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    second, changed = build_company_dossier_record(
        first, payload, entity_id=ENTITY, run_id=LATER_RUN, issue_id="2026-08-20", date="2026-08-20"
    )
    assert changed is False
    assert second["drift_log"] == first["drift_log"]
    assert second["facts"]["identity"]["established_by"] == RUN  # not rewritten
    assert second["as_of"] == DATE  # a no-op refresh does not refresh the date


def test_an_absent_section_is_silence_and_never_a_deletion():
    """A shallow refresh must not blank out what a deeper earlier scan found."""
    first, _ = build_company_dossier_record(
        None, _full_payload(), entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    second, _ = build_company_dossier_record(
        first, {"identity": {"legal_name": "RemeGen Co., Ltd.", "hq": "Yantai"}},
        entity_id=ENTITY, run_id=LATER_RUN, issue_id="x", date="2026-08-20",
    )
    assert second["facts"]["setbacks"]["value"], "setbacks were dropped by a payload that never mentioned them"
    assert second["facts"]["setbacks"]["established_by"] == RUN


def test_an_explicitly_empty_section_is_a_claim_and_does_overwrite():
    """`"deals": []` says "we looked, there are none" — different from silence."""
    first, _ = build_company_dossier_record(
        None, _full_payload(), entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    second, changed = build_company_dossier_record(
        first, {"deals": []}, entity_id=ENTITY, run_id=LATER_RUN, issue_id="x", date="2026-08-20"
    )
    assert changed is True
    assert second["facts"]["deals"]["value"] == []
    assert mentioned_sections({"deals": []}) == ("deals",)
    assert mentioned_sections({}) == ()


# ---------------------------------------------------------------------------
# Facts only — the split that makes a dossier shareable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", INTERPRETIVE_FIELDS)
def test_interpretation_is_reported_at_the_record_level(name):
    assert interpretation_violations({name: "anything"}) == [name]


def test_interpretation_is_reported_inside_a_section_row():
    """The realistic leak is a read_through hung off one setback, not the record."""
    payload = {"setbacks": [{"date": "2025-03-01", "kind": "CRL", "read_through": "bad for them"}]}
    assert interpretation_violations(payload) == ["setbacks[0].read_through"]


def test_interpretation_is_dropped_from_what_gets_persisted():
    """A dossier is shared across programs; an opinion is not ([03], #92).

    Dropping rather than refusing the whole write is the deliberate choice: one
    bad field must not cost us the eight real facts beside it, and keeping it
    would let program B silently inherit program A's judgement.
    """
    payload = _full_payload()
    payload["read_through"] = "they are cornered"
    payload["priority"] = "high"
    payload["setbacks"][0]["so_what"] = "expect a restructuring"

    record, _ = build_company_dossier_record(
        None, payload, entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    blob = json.dumps(record)
    for name in INTERPRETIVE_FIELDS:
        assert f'"{name}"' not in blob, name
    assert "cornered" not in blob
    assert set(record["facts"]) <= set(DOSSIER_SECTIONS)


# ---------------------------------------------------------------------------
# Thin sections — the China coverage decision, story 27
# ---------------------------------------------------------------------------


def test_a_complete_dossier_marks_nothing_thin():
    record, _ = build_company_dossier_record(
        None, _full_payload(), entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    assert record["coverage"]["thin_sections"] == []
    assert "degradation" not in record["coverage"]


def test_a_partial_dossier_marks_exactly_the_sections_it_could_not_fill():
    """The HKEX case: identity is findable, financing and deals are not."""
    record, _ = build_company_dossier_record(
        None,
        {"identity": {"legal_name": "Shengdi Pharma", "status": "private"}},
        entity_id="co_shengdi",
        run_id=RUN,
        issue_id=ISSUE,
        date=DATE,
        degradation="dossier_scan_partial: no HKEX full-text coverage",
    )
    thin = record["coverage"]["thin_sections"]
    assert "identity" not in thin
    assert set(thin) == set(DOSSIER_SECTIONS) - {"identity"}
    assert record["coverage"]["degradation"] == "dossier_scan_partial: no HKEX full-text coverage"


def test_a_section_present_but_empty_still_counts_as_thin():
    """A record that exists and says nothing must not render as a small company."""
    assert "identity" in thin_sections({"identity": {"legal_name": None, "aliases": []}})
    assert "people" in thin_sections({"people": [{}, {"name": None}]})
    assert thin_sections(_full_payload()) == []


def test_thin_marking_is_recomputed_from_the_merged_record_not_the_payload():
    """A later scan that fills one gap must shrink the marker, not restate it."""
    first, _ = build_company_dossier_record(
        None, {"identity": {"legal_name": "RemeGen"}}, entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    assert "funding" in first["coverage"]["thin_sections"]
    second, _ = build_company_dossier_record(
        first,
        {"funding": {"total_raised": "$1.2B"}},
        entity_id=ENTITY,
        run_id=LATER_RUN,
        issue_id="x",
        date="2026-08-20",
    )
    assert "funding" not in second["coverage"]["thin_sections"]
    assert "identity" not in second["coverage"]["thin_sections"]


def test_coverage_always_states_thin_sections_even_when_empty():
    """Story 38 applied to the record: computed-and-clean != nobody-computed-it."""
    assert Coverage().to_dict() == {"thin_sections": []}


# ---------------------------------------------------------------------------
# The asset <-> company link — stories 30 and 31
# ---------------------------------------------------------------------------


def test_an_asset_record_points_at_the_company_that_holds_it_with_provenance():
    record, changed = build_asset_company_link(
        None, asset_entity_id="asset_rc148", company_entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    assert changed is True
    assert record["kind"] == "asset"
    assert record["facts"]["held_by"] == {"value": ENTITY, "established_by": RUN, "issue": ISSUE}
    assert company_for_asset(record) == ENTITY


def test_a_change_of_holder_appends_rather_than_overwrites():
    """An acquisition is exactly where the previous answer must stay auditable."""
    first, _ = build_asset_company_link(
        None, asset_entity_id="asset_rc148", company_entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    second, changed = build_asset_company_link(
        first, asset_entity_id="asset_rc148", company_entity_id="co_abbvie",
        run_id=LATER_RUN, issue_id="x", date="2026-08-20",
    )
    assert changed is True
    assert company_for_asset(second) == "co_abbvie"
    assert second["drift_log"][-1]["from"] == ENTITY
    assert second["drift_log"][-1]["to"] == "co_abbvie"
    assert len(second["drift_log"]) == 2


def test_relinking_to_the_same_holder_is_a_no_op():
    first, _ = build_asset_company_link(
        None, asset_entity_id="asset_rc148", company_entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    _, changed = build_asset_company_link(
        first, asset_entity_id="asset_rc148", company_entity_id=ENTITY,
        run_id=LATER_RUN, issue_id="x", date="2026-08-20",
    )
    assert changed is False


def test_the_reverse_traversal_reads_the_pipeline():
    record, _ = build_company_dossier_record(
        None, _full_payload(), entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    assert assets_of_company(record) == ["asset_rc148"]


# ---------------------------------------------------------------------------
# The writers — same (path, changed) contract as state_edits
# ---------------------------------------------------------------------------


def test_the_writer_writes_only_when_something_changed(tmp_path: Path):
    """A quiet cycle stages nothing, so the diff is exactly the edit."""
    path, changed = apply_company_dossier_v2(
        tmp_path, ENTITY, _full_payload(), run_id=RUN, issue_id=ISSUE, now=NOW
    )
    assert changed is True
    assert path == company_dossier_path(tmp_path, ENTITY)
    assert path.relative_to(tmp_path).as_posix() == f"state/entities/companies/{ENTITY}.json"
    written = json.loads(path.read_text())
    assert written["version"] == 1
    assert written["last_edited_by"] == "loop"

    path2, changed2 = apply_company_dossier_v2(
        tmp_path, ENTITY, _full_payload(), existing=written, run_id=LATER_RUN, issue_id="x", now=NOW
    )
    assert (path2, changed2) == (path, False)
    assert json.loads(path.read_text()) == written  # untouched on disk


def test_a_no_op_against_a_missing_dossier_writes_no_file(tmp_path: Path):
    """Absence must stay absence — story 25's "we have not looked yet"."""
    path, changed = apply_company_dossier_v2(tmp_path, ENTITY, None, run_id=RUN, now=NOW)
    assert changed is False
    assert not path.exists()
    assert load_company_dossier(tmp_path, ENTITY) is None
    assert load_company_dossiers(tmp_path) == {}


def test_the_writer_never_reads_the_clock(tmp_path: Path):
    """A writer that called datetime.now() would be un-replayable and untestable."""
    with pytest.raises(ValueError, match="never reads the clock"):
        apply_company_dossier_v2(tmp_path, ENTITY, _full_payload(), run_id=RUN)
    with pytest.raises(ValueError, match="never reads the clock"):
        apply_asset_company_link_v2(tmp_path, "asset_rc148", ENTITY, run_id=RUN)


def test_the_asset_writer_round_trips_through_the_loader(tmp_path: Path):
    path, changed = apply_asset_company_link_v2(
        tmp_path, "asset_rc148", ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    assert changed is True
    assert path == asset_record_path(tmp_path, "asset_rc148")
    assert company_for_asset(load_asset_record(tmp_path, "asset_rc148")) == ENTITY


def test_load_company_dossiers_keys_by_entity_id(tmp_path: Path):
    apply_company_dossier_v2(tmp_path, ENTITY, _full_payload(), run_id=RUN, date=DATE)
    apply_company_dossier_v2(tmp_path, "co_akeso", {"identity": {"legal_name": "Akeso"}}, run_id=RUN, date=DATE)
    loaded = load_company_dossiers(tmp_path)
    assert set(loaded) == {ENTITY, "co_akeso"}
    assert loaded[ENTITY]["kind"] == DOSSIER_KIND


def test_a_corrupt_record_on_disk_degrades_to_absence(tmp_path: Path):
    """One bad file must not take down a run that has nothing to do with it."""
    path = company_dossier_path(tmp_path, ENTITY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all")
    assert load_company_dossier(tmp_path, ENTITY) is None
    assert load_company_dossiers(tmp_path) == {}

    # A JSON document of the wrong TYPE is the same kind of absence.
    path.write_text('["a list where a record belongs"]')
    assert load_company_dossier(tmp_path, ENTITY) is None


# ---------------------------------------------------------------------------
# Adversarial shape — the class this repo has shipped as a bug five times
# ---------------------------------------------------------------------------

HOSTILE = [
    None,
    "",
    "RemeGen is a Chinese ADC company founded in 2008.",
    0,
    False,
    [],
    ["identity", "funding"],
    {},
    {"identity": None},
    {"identity": "a Chinese ADC company"},
    {"identity": ["RemeGen"]},
    {"identity": {"listings": "HKEX:9995"}},
    {"identity": {"listings": [None, "HKEX", {"exchange": ["HKEX"]}]}},
    {"identity": {"status": {"nested": "public"}}},
    {"funding": {"rounds": {"date": "2019", "amount": 100}}},
    {"funding": {"rounds": [[{"amount": "$1"}]]}},
    {"funding": {"ipo": "listed in 2020"}},
    {"funding": {"total_raised": {"usd": 1}}},
    {"pipeline": "we have four programs"},
    {"pipeline": [None, 3, "asset_rc148", {"asset_entity_id": {"deep": "nope"}}]},
    {"deals": [{"upfront": True, "milestones": [1, 2]}]},
    {"people": {"name": "Fang"}},
    {"pivots": [{"from": {"a": {"b": {"c": {"d": "deep"}}}}, "evidence": {"url": "x"}}]},
    {"setbacks": [{"kind": None, "detail": ["a", "b"]}]},
    {"coverage": {"thin_sections": "everything"}},
    {"identity": {"aliases": [["nested"], {"n": 1}, "RemeGen"]}},
    {"unknown_section": {"anything": 1}},
]


@pytest.mark.parametrize("payload", HOSTILE, ids=range(len(HOSTILE)))
def test_no_public_entry_point_crashes_on_hostile_input(payload, tmp_path: Path):
    """A gate that crashes is strictly worse than one that misses (#92).

    Every public entry point is driven with null, prose, the wrong container, the
    wrong scalar type and excess nesting, at the record level and inside each
    section. None may raise; all must return well-formed values that the next
    caller can index without a guard.
    """
    normalized = normalize_dossier_payload(payload)
    assert set(normalized) == set(DOSSIER_SECTIONS)
    for name in DOSSIER_SECTIONS:
        assert isinstance(normalized[name], list if name not in ("identity", "origin", "funding") else dict)

    assert isinstance(thin_sections(payload), list)
    assert isinstance(interpretation_violations(payload), list)
    assert isinstance(mentioned_sections(payload), tuple)

    record, changed = build_company_dossier_record(
        payload, payload, entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    assert isinstance(record, dict)
    assert isinstance(changed, bool)
    json.dumps(record)  # whatever survived must still be serializable

    path, changed = apply_company_dossier_v2(
        tmp_path, ENTITY, payload, existing=payload, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    if changed:
        json.loads(path.read_text())

    assert company_for_asset(payload) is None or isinstance(company_for_asset(payload), str)
    assert isinstance(assets_of_company(payload), list)
    build_asset_company_link(
        payload, asset_entity_id="asset_x", company_entity_id=ENTITY, run_id=RUN, date=DATE
    )


def test_a_hostile_prior_record_cannot_corrupt_a_later_good_write(tmp_path: Path):
    """Recovery, not just survival: garbage on disk must not block a real refresh."""
    record, changed = build_company_dossier_record(
        {"facts": "this used to be a dict", "drift_log": "and this a list"},
        _full_payload(),
        entity_id=ENTITY,
        run_id=RUN,
        issue_id=ISSUE,
        date=DATE,
    )
    assert changed is True
    assert record["facts"]["identity"]["value"]["legal_name"] == "RemeGen Co., Ltd."
    assert [e["action"] for e in record["drift_log"]] == ["established"] * len(DOSSIER_SECTIONS)


def test_an_empty_holder_id_is_silence_not_an_unlinking():
    """No holder named is not a claim that the asset is unheld."""
    first, _ = build_asset_company_link(
        None, asset_entity_id="asset_rc148", company_entity_id=ENTITY, run_id=RUN, issue_id=ISSUE, date=DATE
    )
    second, changed = build_asset_company_link(
        first, asset_entity_id="asset_rc148", company_entity_id="",
        run_id=LATER_RUN, issue_id="x", date="2026-08-20",
    )
    assert changed is False
    assert company_for_asset(second) == ENTITY
