"""Stage 1 — the gate on a persisted company dossier (#92).

A dossier is a STATE file, not an issue section: `run.py` writes it through the
state-edit path and every future run, and every OTHER program, reads it back
([03] the shared fact layer). That is what makes this gate worth having on top
of `findings.validate_dossier_findings` — the findings gate gets one look at
one payload at the model seam, while this one guards the layer that is actually
read, after a merge that may have happened runs ago.

Three things are being asserted, in descending order of how much they matter:

1. **The table does not drift from the contract.** `DOSSIER_RECORD_V2` is
   derived from `findings.DOSSIER_SECTIONS` and must agree with
   `dossiers.DOSSIER_SECTIONS` — the module that actually writes the record and
   which this module cannot import (it sits above us, via `state_edits`). The
   drift tests are the substitute for that import, and they have teeth: delete
   a section from either contract, or a row from the table, and they go red.
2. **The walker never crashes.** Null, prose, wrong container, wrong depth,
   anywhere. This has shipped as a live bug repeatedly; a gate that dies after
   publishing decides nothing.
3. **Real defects are named.** A fact with no provenance, an overwrite with no
   drift entry, an out-of-vocabulary enum.

The happy-path record is built by the REAL builder rather than hand-typed,
following the publish-v2 pattern of asserting against the real thing where the
real thing is the subject: a hand-typed fixture could agree with the table while
both disagree with what the writer emits, which is the only disagreement that
can hurt a run. Fully deterministic — the builder is pure, takes its date as an
argument, and touches no clock, no filesystem, no model and no network.
"""

from __future__ import annotations

import copy

import pytest

from researchswarm.dossiers import build_company_dossier_record
from researchswarm.validator import check_dossier_record, validate_dossier_record

RUN_ID = "run_20260719_0900"
ISSUE_ID = "issue_2026_07_19"
DATE = "2026-07-19"

# A payload exercising every section, including the two differentiated ones
# (#92: `pivots[]` and `setbacks[]` are the fields no vendor sells) and every
# closed vocabulary, so the enum rows are actually walked rather than skipped
# over an absent key.
FULL_PAYLOAD = {
    "identity": {
        "legal_name": "RemeGen Co., Ltd.",
        "aliases": ["RemeGen", "荣昌生物"],
        "founded": "2008",
        "hq": "Yantai, China",
        "status": "public",
        "listings": [{"exchange": "HKEX", "ticker": "9995"}],
    },
    "origin": {
        "founding_story": "Spun out of a Yantai protein-engineering group.",
        "founders": ["Wang Weidong", "Fang Jianmin"],
        "spun_out_of": "RemeGen Biosciences",
        "founding_thesis": "ADCs and fusion proteins for Chinese-prevalence tumours.",
    },
    "funding": {
        "total_raised": "USD 1.1B",
        "rounds": [
            {
                "date": "2020-03-01",
                "stage": "Series C",
                "amount": "USD 100M",
                "currency": "USD",
                "lead": "Lilly Asia Ventures",
                "investors": ["Lilly Asia Ventures", "Loyal Valley"],
                "pre_money": "USD 900M",
                "post_money": "USD 1.0B",
            }
        ],
        "ipo": {
            "date": "2020-11-09",
            "exchange": "HKEX",
            "raised": "USD 515M",
            "price": "HKD 52.10",
        },
    },
    "pipeline": [
        {
            "asset_entity_id": "asset_disitamab_vedotin",
            "indication": "urothelial carcinoma",
            "phase": "3",
            "status": "active",
            "first_disclosed": "2016-05-01",
        }
    ],
    "deals": [
        {
            "date": "2021-08-09",
            "type": "license",
            "counterparty": "Seagen",
            "direction": "out",
            "upfront": "USD 200M",
            "milestones": "USD 2.4B",
            "royalty": "tiered double-digit",
            "territory": "ex-Asia",
        }
    ],
    "people": [
        {
            "name": "Fang Jianmin",
            "role": "CEO",
            "since": "2008",
            "until": None,
            "prior": "Tongji University",
            "departure_signal": None,
        }
    ],
    "pivots": [
        {
            "date": "2023-01-01",
            "from": "broad ADC portfolio",
            "to": "urothelial and gastric focus",
            "trigger": "capital constraint after the 2022 drawdown",
            "evidence": ["2023 annual report"],
            "outcome": "two programmes deprioritised",
        }
    ],
    "setbacks": [
        {
            "date": "2024-06-01",
            "kind": "discontinuation",
            "detail": "RC28 wet-AMD arm halted.",
            "program": "RC28",
        }
    ],
}


def _record(payload=FULL_PAYLOAD, **kwargs) -> dict:
    """The real writer's output. Pure, so this is a fixture with no IO."""
    record, _ = build_company_dossier_record(
        None,
        payload,
        entity_id="co_remegen",
        run_id=RUN_ID,
        issue_id=ISSUE_ID,
        date=DATE,
        **kwargs,
    )
    return record


class TestTheRealWriterProducesALegalRecord:
    """The end the whole table serves: what `run.py` actually persists passes.

    If this fails, either the table is wrong or the writer is — and the pair
    disagreeing is exactly the state that would otherwise be discovered by a
    later run crashing on a record it read.
    """

    def test_a_full_dossier_passes_cleanly(self):
        assert validate_dossier_record(_record()).blocking == ()

    def test_a_sparse_dossier_passes_cleanly(self):
        """Silence is legal. A first-sighting dossier holds one section and
        marks the other seven thin (story 27); it must not be gated as if a
        missing section were a malformed one."""
        record = _record({"identity": {"legal_name": "Akeso, Inc.", "status": "public"}})
        assert validate_dossier_record(record).blocking == ()

    def test_a_degraded_scan_still_produces_a_legal_record(self):
        """Story 24: a capped or failed scan degrades, it does not fail the run.
        The record it leaves behind must therefore still pass the gate."""
        record = _record(degradation="history-search cap hit at 40 sources")
        assert validate_dossier_record(record).blocking == ()

    def test_a_second_run_correcting_a_fact_stays_legal(self):
        """Corrections APPEND (story 14) — the drift entry the merge adds is
        itself table-covered, so an append must not break the gate."""
        first = _record()
        corrected = copy.deepcopy(FULL_PAYLOAD)
        corrected["identity"]["hq"] = "Yantai, Shandong, China"
        record, changed = build_company_dossier_record(
            first,
            corrected,
            entity_id="co_remegen",
            run_id="run_20261019_0900",
            issue_id="issue_2026_10_19",
            date="2026-10-19",
        )
        assert changed
        assert validate_dossier_record(record).blocking == ()


class TestTheRecordTableDoesNotDriftFromTheContract:
    """THE point of the work: coverage that cannot silently fall behind #92.

    Prose coverage drifts because nothing fails when it does. These are what
    fails. Each test states the direction it guards — table→contract catches a
    row written against a misremembered schema, contract→table catches the far
    more common case of the schema growing while the gate does not.
    """

    def _paths(self):
        from researchswarm.validator import DOSSIER_RECORD_V2

        return {shape.path for shape in DOSSIER_RECORD_V2}

    def test_every_fact_section_has_a_wrapper_row_and_a_value_row(self):
        """contract → table. Add a ninth section to #92's schema block and this
        is the test that turns a 3am AttributeError into a red build."""
        from researchswarm.validator import DOSSIER_FACT_SECTIONS

        paths = self._paths()
        missing = [
            name
            for name in DOSSIER_FACT_SECTIONS
            if f"facts.{name}" not in paths or f"facts.{name}.value" not in paths
        ]
        assert missing == [], f"fact sections with no table row: {missing}"

    def test_the_validator_and_the_writer_agree_on_the_section_set(self):
        """The import this module cannot make, asserted instead.

        `dossiers` sits ABOVE the validator (it reaches `state_edits`, which
        imports this module), so taking the section tuple from it would be a
        cycle. The table derives from `findings` instead, and this is the
        equality that makes that safe: three modules, one contract.
        """
        from researchswarm.dossiers import DOSSIER_SECTIONS as WRITER_SECTIONS
        from researchswarm.validator import DOSSIER_FACT_SECTIONS

        assert tuple(DOSSIER_FACT_SECTIONS) == tuple(WRITER_SECTIONS)

    def test_the_validator_and_the_writer_agree_on_which_sections_are_lists(self):
        """A section typed object here and list there would pass both gates and
        still hand the page a record it cannot render."""
        from researchswarm.dossiers import DOSSIER_LIST_SECTIONS as WRITER_LISTS
        from researchswarm.validator import DOSSIER_LIST_SECTIONS

        assert DOSSIER_LIST_SECTIONS == frozenset(WRITER_LISTS)

    def test_list_sections_are_typed_as_arrays_and_object_sections_are_not(self):
        from researchswarm.validator import (
            DOSSIER_FACT_SECTIONS,
            DOSSIER_LIST_SECTIONS,
            DOSSIER_RECORD_V2,
        )

        by_path = {shape.path: shape for shape in DOSSIER_RECORD_V2}
        for name in DOSSIER_FACT_SECTIONS:
            row = by_path[f"facts.{name}.value"]
            expected = "array" if name in DOSSIER_LIST_SECTIONS else "object"
            assert row.container == expected, f"{name} typed {row.container}"

    def test_every_closed_vocabulary_is_policed_somewhere_in_the_table(self):
        """The enums the writer drops unknown values into (`IDENTITY_STATUSES`,
        `DEAL_TYPES`, `DEAL_DIRECTIONS`, `SETBACK_KINDS`) must each be enforced
        by a row, or a hand-edited record could carry a status no reader
        understands."""
        from researchswarm.dossiers import (
            DEAL_DIRECTIONS,
            DEAL_TYPES,
            IDENTITY_STATUSES,
            SETBACK_KINDS,
        )
        from researchswarm.validator import DOSSIER_RECORD_V2

        policed = {
            frozenset(allowed)
            for shape in DOSSIER_RECORD_V2
            for allowed in shape.enums.values()
        }
        for vocabulary in (IDENTITY_STATUSES, DEAL_TYPES, DEAL_DIRECTIONS, SETBACK_KINDS):
            assert frozenset(vocabulary) in policed, f"unpoliced vocabulary: {vocabulary}"

    def test_every_row_resolves_against_the_real_writers_output(self):
        """table → contract. A row naming a path the writer never emits is a row
        written against a misremembered record — a typo'd key, or a field that
        moved — and it would then police nothing forever."""
        from researchswarm.validator import DOSSIER_RECORD_V2, _MISSING, _walk_path

        record = _record()
        unresolved = [
            shape.path
            for shape in DOSSIER_RECORD_V2
            if not [
                value
                for value, _ in _walk_path(record, shape.path)
                if value is not _MISSING and value is not None
            ]
        ]
        assert unresolved == [], f"rows naming paths the writer never emits: {unresolved}"

    def test_no_written_path_is_missing_from_the_table_without_a_reason(self):
        """The 'the writer grew, the table didn't' direction.

        Walks the real record's own structure and demands every container path
        either has a row or is exempt with a stated reason. An exemption is a
        decision on the record, not an absence nobody noticed.
        """
        from researchswarm.validator import DOSSIER_RECORD_V2

        # Leaf keys carrying no shape duty, each with the reason it is somebody
        # else's job. This is the list a reviewer argues with.
        exempt = {
            "value": "the section's own row types it; nested free-form facts are open by design",
            "ipo": "#92 shows it; a private company legally has none, so requiring it would file a finding on a fact",
            "listings": "policed as a container by its own row; an element's fields are open",
        }
        # A drift entry's `from`/`to` are SNAPSHOTS of a value we used to hold.
        # They are deliberately un-gated: re-checking a historical belief against
        # today's contract would file findings on the record's own memory, and an
        # append-only log that can be invalidated by a later schema change is not
        # a log. The live value is policed at `facts.<section>.value`.
        snapshots = ("drift_log[].from", "drift_log[].to")
        paths: set[str] = set()

        def walk(node, path):
            if isinstance(node, dict):
                if path:
                    paths.add(path)
                for key, value in node.items():
                    walk(value, f"{path}.{key}" if path else key)
            elif isinstance(node, list):
                if path:
                    paths.add(path)
                for element in node:
                    walk(element, path + "[]")

        walk(_record(), "")
        covered = {shape.path for shape in DOSSIER_RECORD_V2}
        uncovered = sorted(
            path
            for path in paths - covered
            if path.split(".")[-1].removesuffix("[]") not in exempt
            and not path.startswith(snapshots)
        )
        assert uncovered == [], (
            "written paths with neither a table row nor a stated exemption — "
            f"the record grew and the gate did not: {uncovered}"
        )

    def test_the_drift_test_has_teeth(self):
        """Proof the mechanism above is not decorative.

        Removing a row from the table must make `test_every_fact_section_has_a
        _wrapper_row_and_a_value_row` fail. Asserted here rather than verified
        by hand once, because a drift test that stopped biting would be
        indistinguishable from one that passes.
        """
        import researchswarm.validator as validator

        original = validator.DOSSIER_RECORD_V2
        thinned = tuple(
            shape for shape in original if shape.path != "facts.setbacks.value"
        )
        try:
            validator.DOSSIER_RECORD_V2 = thinned
            with pytest.raises(AssertionError):
                self.test_every_fact_section_has_a_wrapper_row_and_a_value_row()
        finally:
            validator.DOSSIER_RECORD_V2 = original
        # And the table is restored, so no other test inherits the damage.
        assert validator.DOSSIER_RECORD_V2 is original


class TestTheGateNamesRealDefects:
    """A table that never files a finding is a table that is not wired up."""

    def _kinds(self, record):
        return {(f.kind, f.where) for f in validate_dossier_record(record).blocking}

    def test_a_fact_with_no_provenance_is_named(self):
        """Story 13: every field cites the run that established it. A fact
        wrapper with no `established_by` is a claim with no audit trail."""
        record = _record()
        del record["facts"]["identity"]["established_by"]
        wheres = {f.where for f in validate_dossier_record(record).blocking}
        assert "facts.identity" in wheres

    def test_an_out_of_vocabulary_setback_kind_is_named(self):
        record = _record()
        record["facts"]["setbacks"]["value"][0]["kind"] = "vibes"
        wheres = {f.where for f in validate_dossier_record(record).blocking}
        assert any(w.startswith("facts.setbacks.value[") and w.endswith(".kind") for w in wheres)

    def test_an_out_of_vocabulary_deal_direction_is_named(self):
        record = _record()
        record["facts"]["deals"]["value"][0]["direction"] = "sideways"
        wheres = {f.where for f in validate_dossier_record(record).blocking}
        assert any(w.endswith(".direction") for w in wheres)

    def test_a_wrong_entity_kind_is_named(self):
        """#92: a company dossier is never an asset record. Conflating them is
        the one confusion the kind split exists to prevent."""
        record = _record()
        record["kind"] = "asset"
        assert ("malformed_dossier", "<dossier>.kind") in self._kinds(record)

    def test_a_list_section_emitted_as_an_object_is_named(self):
        record = _record()
        record["facts"]["pivots"]["value"] = {"date": "2023-01-01"}
        wheres = {f.where for f in validate_dossier_record(record).blocking}
        assert "facts.pivots.value" in wheres

    def test_an_as_of_that_is_not_a_string_is_named(self):
        """Story 15: `as_of` separates fresh intelligence from a stale record.
        A dict there renders as a date and silently claims freshness."""
        record = _record()
        record["as_of"] = {"date": DATE}
        assert ("malformed_dossier", "as_of") in self._kinds(record)

    def test_a_drift_entry_missing_its_run_is_named(self):
        """The append-only correction log is only auditable if each entry says
        which run wrote it (story 14)."""
        record = _record()
        del record["drift_log"][0]["run_id"]
        wheres = {f.where for f in validate_dossier_record(record).blocking}
        assert any(w.startswith("drift_log[") for w in wheres)

    def test_a_missing_coverage_block_is_named(self):
        """A dossier with no thin-section marking cannot be read as unmeasured
        rather than small (story 27), which is the China blind spot exactly."""
        record = _record()
        del record["coverage"]
        assert ("malformed_dossier", "<dossier>") in self._kinds(record)

    def test_thin_sections_as_prose_is_named(self):
        record = _record()
        record["coverage"]["thin_sections"] = "most of it, honestly"
        assert ("malformed_dossier", "coverage.thin_sections") in self._kinds(record)

    def test_a_clean_record_files_nothing(self):
        """The other half of every test above: the gate is not simply loud."""
        assert self._kinds(_record()) == set()


class TestTheWalkerNeverCrashes:
    """It validates adversarial input BY DEFINITION — that is its whole job.

    A gate that raises is strictly worse than one that misses: it takes the run
    down at the moment the record is most malformed, and it does so AFTER
    publishing. That exact bug has shipped repeatedly in this repo. So the walk
    is total at every depth, for every kind of garbage — and unlike the issue
    gate, this one reads a file some earlier run wrote, so "the caller upstream
    is careful" is not available as a defence.
    """

    GARBAGE = (
        None,
        "prose the model wrote instead",
        7,
        0,
        True,
        False,
        [],
        {},
        [None],
        ["x"],
        {"k": None},
        [[{"deep": [None]}]],
        {"value": "not a wrapper"},
    )

    def _run(self, record):
        problems: list = []
        check_dossier_record(record, problems)  # must not raise
        return problems

    @pytest.mark.parametrize("garbage", GARBAGE)
    def test_the_whole_record_can_be_garbage(self, garbage):
        """Not merely survives it — DECIDES against it. None of these is a legal
        dossier, so every one must come back with a finding rather than an empty
        list, which is what a swallowed subtree would look like."""
        assert self._run(garbage) != []

    def test_every_top_level_key_survives_every_kind_of_garbage(self):
        record = _record()
        for key in list(record):
            for value in self.GARBAGE:
                mutant = copy.deepcopy(record)
                mutant[key] = value
                self._run(mutant)

    def test_every_row_survives_garbage_at_its_own_depth(self):
        """Not just the top level: a null three levels down, inside a list
        element, is the shape a half-written merge actually leaves behind, and
        the walk must step over it."""
        from researchswarm.validator import DOSSIER_RECORD_V2, _walk_path

        for shape in DOSSIER_RECORD_V2:
            for value in self.GARBAGE:
                mutant = _record()
                self._plant(mutant, shape.path.split("."), value)
                self._run(mutant)
                assert _walk_path(mutant, shape.path) is not None

    def test_garbage_planted_at_one_row_does_not_hide_the_others(self):
        """Totality is not enough on its own — a walk that swallowed a subtree
        silently would also never crash. The gate must still be deciding."""
        record = _record()
        record["facts"]["funding"] = "we never looked"
        problems = self._run(record)
        assert any(p.where == "facts.funding" for p in problems)
        assert self._run(_record()) == []

    def test_deeply_nested_garbage_does_not_recurse_forever(self):
        """A model can emit arbitrarily nested JSON. The walk is iterative over
        a fixed path, so depth is bounded by the table, not by the input — this
        pins that property rather than trusting it."""
        record = _record()
        node: dict = {}
        deep = node
        for _ in range(200):
            deep["value"] = {"nested": {}}
            deep = deep["value"]["nested"]
        record["facts"]["origin"] = node
        self._run(record)

    @staticmethod
    def _plant(node, segments, value):
        segment, rest = segments[0], segments[1:]
        key, is_list = segment.removesuffix("[]"), segment.endswith("[]")
        if not isinstance(node, dict):
            return
        if not rest:
            if is_list and isinstance(node.get(key), list):
                node[key] = [value for _ in node[key]] or [value]
            else:
                node[key] = value
            return
        target = node.get(key)
        if is_list and isinstance(target, list):
            for element in target:
                TestTheWalkerNeverCrashes._plant(element, rest, value)
        else:
            TestTheWalkerNeverCrashes._plant(target, rest, value)


class TestTheIssueGateIsUnaffected:
    """v2-alongside-v1, and dossier-alongside-issue. The new table must not have
    reached into the issue contract on its way in."""

    def test_the_issue_table_has_no_dossier_rows(self):
        from researchswarm.validator import ISSUE_SHAPE_V2

        assert [s.path for s in ISSUE_SHAPE_V2 if s.path.startswith("facts")] == []

    def test_no_issue_row_uses_the_new_scalar_container(self):
        """The `string` container was added for the dossier root. If an issue row
        acquires one, that is a deliberate decision someone should make on
        purpose — not a side effect of this build."""
        from researchswarm.validator import ISSUE_SHAPE_V2

        assert [s.path for s in ISSUE_SHAPE_V2 if s.container == "string"] == []

    def test_the_sample_issue_still_passes_its_own_table(self):
        import json
        from pathlib import Path

        from researchswarm.validator import _check_issue_shape_v2

        sample_path = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "schema"
            / "sample-issue-hmbd-001-2026-07-18.json"
        )
        issue = json.loads(sample_path.read_text())
        issue.pop("_comment", None)
        problems: list = []
        _check_issue_shape_v2(issue, problems)
        assert problems == []
