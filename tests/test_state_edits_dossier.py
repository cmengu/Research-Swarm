"""The dossier state-edit path — provenance, append-only correction, quiet cycles.

Covers `state_edits.apply_dossier_edits_v2`: the shell that takes the run's
research corpus and writes company dossiers (and their asset->company links)
through the state-edit discipline every other v2 writer follows — cite the run,
append to the file's own log, rewrite only when something actually changed.

These tests exercise EXTERNAL BEHAVIOUR at one seam: what the function returns
and what lands on disk, given an injected corpus and an injected date. Nothing
here reaches a model, the network, or git — this path makes no calls, so there is
no runner to inject; the autouse `offline` fixture in conftest guards the suite
regardless. The date is always passed explicitly, because the writer refuses to
read the clock and a test that let it would not be replayable.

The weight is deliberately unbalanced toward the adversarial-shape class, per
#92's testing decisions: this path runs AFTER the issue is published, so a crash
here costs the run its commit with the artifacts already on disk. That bug has
shipped five times in this repo. `test_no_shape_of_corpus_crashes` therefore
walks a hostile corpus through the one public entry point.

Spec: docs/spec/03-state-and-governance.md, issue #92
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from researchswarm.state_edits import apply_dossier_edits_v2

RUN = "run_20260719_0700"
LATER_RUN = "run_20260820_0700"
ISSUE = "2026-07-19"
DATE = "2026-07-19"
LATER_DATE = "2026-08-20"
NOW = datetime(2026, 7, 19, 7, 0)
ENTITY = "co_remegen"
APERTURE = f"dossier_scan:{ENTITY}"


# ---------------------------------------------------------------------------
# Fixtures — envelopes in the shape the findings gate lets through
# ---------------------------------------------------------------------------


def _dossier(**overrides) -> dict:
    """A dossier record as a validated `dossier_scan` payload carries it.

    A China-listed competitor on purpose: the China blind spot is what #92's
    coverage decision is about, and this path must not privilege US issuers.
    """
    record = {
        "entity_id": ENTITY,
        "kind": "company",
        "as_of": DATE,
        "identity": {
            "legal_name": "RemeGen Co., Ltd.",
            "founded": "2008",
            "hq": "Yantai, China",
            "status": "public",
            "listings": [{"exchange": "HKEX", "ticker": "9995"}],
        },
        "pipeline": [
            {"asset_entity_id": "as_rc48", "indication": "urothelial", "phase": "3"},
        ],
    }
    record.update(overrides)
    return record


def _envelope(dossier=None, **overrides) -> dict:
    """The v2 aperture envelope a dossier scan returns, window explicitly null."""
    envelope = {
        "aperture": APERTURE,
        "program_id": "hmbd-001",
        "run_id": RUN,
        "coverage_window": None,
        "quiet": False,
        "findings": [],
        "dossier": _dossier() if dossier is None else dossier,
        "coverage_notes": [],
        "errors": [],
    }
    envelope.update(overrides)
    return envelope


def _corpus(envelope=None) -> dict:
    return {APERTURE: _envelope() if envelope is None else envelope}


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _company(root: Path, entity_id: str = ENTITY) -> dict:
    return _read(root / "state" / "entities" / "companies" / f"{entity_id}.json")


# ---------------------------------------------------------------------------
# What it writes
# ---------------------------------------------------------------------------


def test_writes_the_company_dossier_and_returns_the_path(tmp_path):
    touched = apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, date=DATE)

    company = tmp_path / "state" / "entities" / "companies" / f"{ENTITY}.json"
    assert company in touched
    assert company.exists()
    assert _company(tmp_path)["kind"] == "company"


def test_every_field_carries_the_run_that_established_it(tmp_path):
    """Story 13 — any claim is auditable back to the run and issue that made it."""
    apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, issue_id=ISSUE, date=DATE)

    identity = _company(tmp_path)["facts"]["identity"]
    assert identity["established_by"] == RUN
    assert identity["issue"] == ISSUE
    assert identity["value"]["legal_name"] == "RemeGen Co., Ltd."


def test_a_pipeline_row_links_its_asset_to_the_company(tmp_path):
    """Story 31 — a readout is traversable to its sponsor's balance sheet."""
    touched = apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, date=DATE)

    asset = tmp_path / "state" / "entities" / "assets" / "as_rc48.json"
    assert asset in touched
    assert _read(asset)["facts"]["held_by"]["value"] == ENTITY


def test_a_dossier_scan_writes_nothing_outside_the_entity_store(tmp_path):
    """The dossier is program-agnostic: no edge, no thesis, no queue is touched."""
    apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, date=DATE)

    written = {p.relative_to(tmp_path).parts[:2] for p in tmp_path.rglob("*.json")}
    assert written == {("state", "entities")}


def test_now_is_accepted_instead_of_date(tmp_path):
    apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, now=NOW)
    assert _company(tmp_path)["first_seen"] == DATE


def test_the_writer_refuses_to_read_the_clock(tmp_path):
    """No `now`, no `date` — a writer that defaulted to today would be unreplayable."""
    with pytest.raises(ValueError):
        apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN)


# ---------------------------------------------------------------------------
# The discipline: quiet cycles stage nothing, corrections append
# ---------------------------------------------------------------------------


def test_an_unchanged_refresh_stages_nothing(tmp_path):
    """A quarterly refresh that finds nothing new must produce an empty diff."""
    apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, date=DATE)
    before = _company(tmp_path)

    touched = apply_dossier_edits_v2(tmp_path, _corpus(), run_id=LATER_RUN, date=LATER_DATE)

    assert touched == []
    assert _company(tmp_path) == before  # not even a version bump


def test_a_correction_appends_a_drift_entry_and_keeps_the_prior_value(tmp_path):
    """Story 14 — corrections append; what we believed before survives in the record."""
    apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, issue_id=ISSUE, date=DATE)

    corrected = _dossier(identity={"legal_name": "RemeGen Biosciences", "status": "public"})
    apply_dossier_edits_v2(
        tmp_path, _corpus(_envelope(corrected)), run_id=LATER_RUN, date=LATER_DATE
    )

    record = _company(tmp_path)
    assert record["facts"]["identity"]["value"]["legal_name"] == "RemeGen Biosciences"
    assert record["facts"]["identity"]["established_by"] == LATER_RUN

    entries = [e for e in record["drift_log"] if e["field"] == "identity"]
    assert [e["action"] for e in entries] == ["established", "corrected"]
    assert entries[-1]["from"]["legal_name"] == "RemeGen Co., Ltd."
    assert entries[-1]["to"]["legal_name"] == "RemeGen Biosciences"
    assert entries[-1]["run_id"] == LATER_RUN


def test_a_section_the_refresh_did_not_mention_survives(tmp_path):
    """An absent section is silence, never a deletion — a thin scan cannot blank a deep one."""
    apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, date=DATE)

    partial = {"entity_id": ENTITY, "kind": "company", "as_of": LATER_DATE,
               "setbacks": [{"date": "2026-08-01", "kind": "layoff", "detail": "12% cut"}]}
    apply_dossier_edits_v2(
        tmp_path, _corpus(_envelope(partial)), run_id=LATER_RUN, date=LATER_DATE
    )

    facts = _company(tmp_path)["facts"]
    assert facts["identity"]["established_by"] == RUN       # untouched
    assert facts["setbacks"]["established_by"] == LATER_RUN  # added


def test_a_partial_dossier_marks_its_thin_sections(tmp_path):
    """Story 27 — an HKEX gap is visible at the point of the gap, not inferred."""
    apply_dossier_edits_v2(tmp_path, _corpus(), run_id=RUN, date=DATE)

    coverage = _company(tmp_path)["coverage"]
    assert "identity" not in coverage["thin_sections"]
    assert {"origin", "funding", "deals", "people", "pivots", "setbacks"} <= set(
        coverage["thin_sections"]
    )


def test_the_scans_own_receipt_lands_on_coverage(tmp_path):
    """`thin_sections` says where the gap is; `degradation` says why it is there."""
    envelope = _envelope(errors=["HKEX full-text archive unreachable"])
    apply_dossier_edits_v2(tmp_path, _corpus(envelope), run_id=RUN, date=DATE)

    assert _company(tmp_path)["coverage"]["degradation"] == "HKEX full-text archive unreachable"


def test_an_error_outranks_a_coverage_note(tmp_path):
    """An error explains an absence; a note merely annotates one."""
    envelope = _envelope(errors=["scan cap hit at 40 filings"], coverage_notes=["read 3 years"])
    apply_dossier_edits_v2(tmp_path, _corpus(envelope), run_id=RUN, date=DATE)

    assert _company(tmp_path)["coverage"]["degradation"] == "scan cap hit at 40 filings"


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


def test_interpretation_never_reaches_the_shared_record(tmp_path):
    """A dossier holds facts only — a leaked opinion would be inherited by the next program."""
    leaky = _dossier(read_through={"relation": "direct_rival"}, priority="high")
    leaky["setbacks"] = [
        {"date": "2026-05-01", "kind": "CRL", "detail": "CMC", "read_through": "bad for them"}
    ]
    apply_dossier_edits_v2(tmp_path, _corpus(_envelope(leaky)), run_id=RUN, date=DATE)

    text = json.dumps(_company(tmp_path))
    assert "read_through" not in text
    assert "priority" not in text
    # …and the real facts in the same payload still landed.
    assert _company(tmp_path)["facts"]["setbacks"]["value"][0]["kind"] == "CRL"


def test_a_non_dossier_aperture_cannot_write_to_the_store(tmp_path):
    """Filtered by aperture KIND: a stray `dossier` key on a biology scan is a refused shape."""
    corpus = {"biology_scan:her3": _envelope(_dossier())}

    assert apply_dossier_edits_v2(tmp_path, corpus, run_id=RUN, date=DATE) == []
    assert not (tmp_path / "state").exists()


def test_the_subject_comes_from_the_aperture_not_the_payload(tmp_path):
    """A dossier filed under the wrong company is worse than a missing one."""
    corpus = {APERTURE: _envelope(_dossier(entity_id="co_someone_else"))}
    apply_dossier_edits_v2(tmp_path, corpus, run_id=RUN, date=DATE)

    assert (tmp_path / "state" / "entities" / "companies" / f"{ENTITY}.json").exists()
    assert not (tmp_path / "state" / "entities" / "companies" / "co_someone_else.json").exists()


def test_a_quiet_scan_writes_nothing(tmp_path):
    """Story 38 — silence in the store; the distinction lives in the envelope."""
    envelope = _envelope()
    envelope.update({"quiet": True, "dossier": None})
    corpus = {APERTURE: envelope}

    assert apply_dossier_edits_v2(tmp_path, corpus, run_id=RUN, date=DATE) == []
    assert not (tmp_path / "state").exists()


# ---------------------------------------------------------------------------
# Adversarial shape — the class that has taken this repo down five times
# ---------------------------------------------------------------------------

HOSTILE_CORPORA = [
    None,
    [],
    "the researcher wrote prose instead of JSON",
    42,
    {},
    {APERTURE: None},
    {APERTURE: "prose where an envelope belongs"},
    {APERTURE: []},
    {APERTURE: {"dossier": None}},
    {APERTURE: {"dossier": "RemeGen is a Chinese ADC company."}},
    {APERTURE: {"dossier": []}},
    {APERTURE: {"dossier": [{"entity_id": ENTITY}]}},
    {APERTURE: {"dossier": {}}},
    {APERTURE: {"dossier": {"identity": "public, Yantai"}}},
    {APERTURE: {"dossier": {"identity": {"listings": "HKEX 9995"}}}},
    {APERTURE: {"dossier": {"pipeline": {"asset_entity_id": "as_rc48"}}}},
    {APERTURE: {"dossier": {"pipeline": [None, "prose", 7, {"asset_entity_id": None}]}}},
    {APERTURE: {"dossier": {"setbacks": [[{"kind": "CRL"}]]}}},
    {APERTURE: {"dossier": _dossier(), "errors": "everything broke"}},
    {APERTURE: {"dossier": _dossier(), "errors": [None, 7, {}]}},
    {APERTURE: {"dossier": _dossier(), "coverage_notes": None}},
    {"dossier_scan:": _envelope()},
    {None: _envelope()},
    {7: _envelope()},
]


@pytest.mark.parametrize("corpus", HOSTILE_CORPORA, ids=range(len(HOSTILE_CORPORA)))
def test_no_shape_of_corpus_crashes(tmp_path, corpus):
    """A crash here takes the run down AFTER publishing — strictly worse than a miss.

    Every case must return a list of paths (possibly empty) and leave a store
    that is either absent or readable JSON. Nothing asserts on WHICH cases write,
    because that is the merge layer's contract, not this shell's; what is pinned
    is that no shape escapes as an exception.
    """
    touched = apply_dossier_edits_v2(tmp_path, corpus, run_id=RUN, date=DATE)

    assert isinstance(touched, list)
    assert all(isinstance(p, Path) for p in touched)
    for path in tmp_path.rglob("*.json"):
        json.loads(path.read_text())  # anything written is still readable


def test_one_broken_envelope_does_not_starve_a_good_one(tmp_path):
    """Degradation is per-envelope: background gathering is subordinate, never contagious."""
    corpus = {
        "dossier_scan:co_broken": "prose",
        APERTURE: _envelope(),
    }

    apply_dossier_edits_v2(tmp_path, corpus, run_id=RUN, date=DATE)

    assert _company(tmp_path)["facts"]["identity"]["established_by"] == RUN
    assert not (tmp_path / "state" / "entities" / "companies" / "co_broken.json").exists()
