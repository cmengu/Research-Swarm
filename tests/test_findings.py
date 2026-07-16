"""The findings contract — validation at the seam.

Deterministic checks are decidable by a script with perfect accuracy, for free,
in milliseconds. This is that principle applied one stage earlier than the
validator, where it costs nothing.

The contract is shaped so a researcher CANNOT emit a stance: there is no field
for one. These tests guard that shape.
"""

import pytest

from researchswarm.findings import FindingsInvalid, validate_findings

WINDOW = {"from": "2026-07-13", "to": "2026-07-16"}


def _finding(**overrides):
    finding = {
        "summary": "Merck acquired Verastem for $9B.",
        "entity_ids": ["merck"],
        "proposed_entity": None,
        "sources": [
            {
                "url": "https://example.com/pr",
                "publisher": "Merck IR",
                "tier": "primary",
                "published_at": "2026-07-15",
                "paywalled": False,
            }
        ],
        "catalyst_refs": [],
        "beat_priority": "high",
        "unconfirmed": False,
    }
    finding.update(overrides)
    return finding


def _payload(**overrides):
    payload = {
        "beat": "ma_dealmaking",
        "run_id": "run_20260716_0700",
        "coverage_window": dict(WINDOW),
        "quiet": False,
        "findings": [_finding()],
        "coverage_notes": {
            "angles_run": ["merck oncology acquisition"],
            "entities_checked": ["merck"],
            "notes": "Swept the charter; deal flow was light.",
        },
        "errors": [],
    }
    payload.update(overrides)
    return payload


def _validate(payload, known=frozenset({"merck", "asset_daraxonrasib"})):
    return validate_findings(
        payload, beat_id="ma_dealmaking", run_id="run_20260716_0700",
        window=WINDOW, known_entity_ids=known,
    )


class TestHappyPath:
    def test_a_well_formed_payload_passes(self):
        _validate(_payload())  # does not raise

    def test_a_genuinely_quiet_beat_passes(self):
        _validate(_payload(quiet=True, findings=[]))


class TestShape:
    def test_rejects_a_non_object(self):
        with pytest.raises(FindingsInvalid, match="object"):
            _validate([])

    def test_rejects_a_mismatched_beat(self):
        """A researcher answering for the wrong beat means the fan-out is
        crossed — worse than a missing beat, because it looks covered."""
        with pytest.raises(FindingsInvalid, match="beat"):
            _validate(_payload(beat="policy_regulation"))

    def test_rejects_a_mismatched_run_id(self):
        with pytest.raises(FindingsInvalid, match="run_id"):
            _validate(_payload(run_id="run_19990101_0000"))

    def test_rejects_a_mismatched_window(self):
        with pytest.raises(FindingsInvalid, match="coverage_window"):
            _validate(_payload(coverage_window={"from": "2020-01-01", "to": "2020-01-02"}))

    @pytest.mark.parametrize("missing", ["beat", "run_id", "findings", "coverage_notes"])
    def test_rejects_missing_required_keys(self, missing):
        payload = _payload()
        del payload[missing]
        with pytest.raises(FindingsInvalid, match=missing):
            _validate(payload)


class TestQuietMustBeHonest:
    def test_quiet_true_with_findings_is_a_contradiction(self):
        with pytest.raises(FindingsInvalid, match="quiet"):
            _validate(_payload(quiet=True))

    def test_quiet_false_with_no_findings_is_a_contradiction(self):
        with pytest.raises(FindingsInvalid, match="quiet"):
            _validate(_payload(quiet=False, findings=[]))

    @pytest.mark.parametrize("bogus", ["false", "true", 0, 1, None])
    def test_quiet_must_be_an_actual_boolean(self, bogus):
        """The regression: `is True` / `is False` against the STRING "false"
        matches neither, so both consistency checks skipped and the guard
        vanished silently — on exactly the sloppy payload that needed it."""
        with pytest.raises(FindingsInvalid, match="quiet"):
            _validate(_payload(quiet=bogus, findings=[]))


class TestPaywalled:
    def test_paywalled_is_required(self):
        """It drives the paywalled_primary advisory. A missing flag reads as
        'not paywalled', turning an unassessable claim into a solid-looking one."""
        source = _finding()["sources"][0]
        del source["paywalled"]
        with pytest.raises(FindingsInvalid, match="paywalled"):
            _validate(_payload(findings=[_finding(sources=[source])]))

    def test_paywalled_must_be_boolean(self):
        source = _finding()["sources"][0] | {"paywalled": "yes"}
        with pytest.raises(FindingsInvalid, match="paywalled"):
            _validate(_payload(findings=[_finding(sources=[source])]))


class TestErrorsIsRequired:
    def test_errors_key_must_be_present(self):
        payload = _payload()
        del payload["errors"]
        with pytest.raises(FindingsInvalid, match="errors"):
            _validate(payload)

    def test_empty_errors_list_is_fine(self):
        _validate(_payload(errors=[]))


class TestCoverageNotes:
    def test_required_even_when_quiet(self):
        """coverage_notes is what makes quiet:true falsifiable — the difference
        between 'these findings are everything' and 'this is what one query
        surfaced'."""
        payload = _payload(quiet=True, findings=[])
        del payload["coverage_notes"]
        with pytest.raises(FindingsInvalid, match="coverage_notes"):
            _validate(payload)

    @pytest.mark.parametrize("field", ["notes", "angles_run", "entities_checked"])
    def test_every_sub_field_is_required(self, field):
        """entities_checked especially: the coverage duty says every
        high-priority roster entity in scope is checked and RECORDED either way.
        Leave it unvalidated and the one field proving the duty was honoured is
        the one that can be quietly omitted."""
        payload = _payload()
        payload["coverage_notes"][field] = [] if field != "notes" else ""
        with pytest.raises(FindingsInvalid, match=field):
            _validate(payload)


class TestSources:
    def test_a_finding_with_no_source_does_not_exist(self):
        with pytest.raises(FindingsInvalid, match="sources"):
            _validate(_payload(findings=[_finding(sources=[])]))

    @pytest.mark.parametrize("field", ["url", "publisher", "tier", "published_at"])
    def test_all_four_source_fields_are_required(self, field):
        source = _finding()["sources"][0]
        del source[field]
        with pytest.raises(FindingsInvalid, match=field):
            _validate(_payload(findings=[_finding(sources=[source])]))

    def test_rejects_a_bogus_tier(self):
        source = _finding()["sources"][0] | {"tier": "blog"}
        with pytest.raises(FindingsInvalid, match="tier"):
            _validate(_payload(findings=[_finding(sources=[source])]))

    def test_rejects_a_string_source(self):
        """Sources are objects, never strings: the critic checks claims against
        tier, and published_at is what catches recycled news."""
        with pytest.raises(FindingsInvalid, match="sources"):
            _validate(_payload(findings=[_finding(sources=["https://example.com"])]))


class TestTheSpine:
    def test_rejects_an_unknown_entity_id(self):
        with pytest.raises(FindingsInvalid, match="ghost_pharma"):
            _validate(_payload(findings=[_finding(entity_ids=["ghost_pharma"])]))

    def test_accepts_an_asset_ref(self):
        _validate(_payload(findings=[_finding(entity_ids=["asset_daraxonrasib"])]))

    def test_off_roster_find_carries_no_refs_and_a_proposal(self):
        _validate(
            _payload(
                findings=[
                    _finding(
                        entity_ids=[],
                        proposed_entity={"name": "Callio", "type": "startup",
                                         "what_they_do": "dual-payload ADC"},
                    )
                ]
            )
        )

    def test_a_proposal_does_not_excuse_a_named_dangling_ref(self):
        """Same hole the state join check had: one proposal must not smuggle
        arbitrary dangling references past the spine."""
        with pytest.raises(FindingsInvalid, match="ghost_pharma"):
            _validate(
                _payload(
                    findings=[
                        _finding(
                            entity_ids=["merck", "ghost_pharma"],
                            proposed_entity={"name": "NewCo"},
                        )
                    ]
                )
            )


class TestNoInterpretationLeaks:
    """Researchers report facts; the manager authors interpretation. The
    contract has no field for a stance — so a researcher inventing one is
    caught here rather than reaching the manager dressed as a fact."""

    @pytest.mark.parametrize(
        "field", ["thesis_impact", "research_angle", "so_what", "priority"]
    )
    def test_rejects_an_interpretation_field(self, field):
        with pytest.raises(FindingsInvalid, match=field):
            _validate(_payload(findings=[_finding(**{field: "confirms"})]))

    def test_beat_priority_is_allowed_as_a_within_beat_hint(self):
        _validate(_payload(findings=[_finding(beat_priority="low")]))

    def test_rejects_a_bogus_beat_priority(self):
        with pytest.raises(FindingsInvalid, match="beat_priority"):
            _validate(_payload(findings=[_finding(beat_priority="critical")]))
