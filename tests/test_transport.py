"""The `claude -p --output-format json` wire format — one parser, one place.

Covers `researchswarm.transport`, which every model-calling subsystem shares:
the researcher fan-out, the calendar verifier and the manager synthesis. That
sharing is the point of the module and also its risk — a misleading message here
appears simultaneously in three unrelated-looking places, which is exactly what
happened on the first live v2 cycle.

Fully deterministic: no subprocess, no network, no model call.
"""

from __future__ import annotations

import pytest

from researchswarm.transport import TransportInvalid, parse_envelope, parse_result_json


class TestTheEnvelope:
    def test_a_well_formed_envelope_parses(self):
        assert parse_envelope('{"result": "{}", "is_error": false}')["result"] == "{}"

    def test_non_json_stdout_is_a_wire_failure(self):
        with pytest.raises(TransportInvalid) as exc:
            parse_envelope("not json at all")
        assert "did not return a JSON envelope" in str(exc.value)

    def test_a_reported_error_is_raised_with_its_payload(self):
        """`is_error` is the CLI telling us the call failed. Parsing on would
        treat an error string as a final message."""
        with pytest.raises(TransportInvalid) as exc:
            parse_envelope('{"is_error": true, "result": "API Error: overloaded"}')
        assert "API Error: overloaded" in str(exc.value)


class TestTheFinalMessage:
    def test_one_json_object_parses(self):
        assert parse_result_json('{"found": true}') == {"found": True}

    def test_a_json_fence_is_stripped(self):
        """The prompts forbid fences, but burning an expensive retry to
        re-punctuate otherwise good output is a bad trade."""
        assert parse_result_json('```json\n{"found": true}\n```') == {"found": True}




class TestAnEmptyFinalMessage:
    """`claude -p` can return a clean envelope with no final message at all.

    It happened four times in one live cycle — three calendar windows and a
    researcher — and every one reported "final message was not one JSON object",
    because `json.loads("")` raises a JSONDecodeError that reads like a
    punctuation problem. Three subsystems share this parser, so one transport
    failure looked like three unrelated bugs in three unrelated places.
    """

    def test_empty_is_reported_as_empty_not_as_bad_json(self):
        with pytest.raises(TransportInvalid) as exc:
            parse_result_json("")
        assert "empty final message" in str(exc.value)
        assert "not one JSON object" not in str(exc.value)

    def test_whitespace_only_is_also_empty(self):
        with pytest.raises(TransportInvalid) as exc:
            parse_result_json("   \n\t ")
        assert "empty final message" in str(exc.value)

    def test_genuinely_malformed_output_still_says_so(self):
        """The distinction only helps if the other branch keeps its own wording."""
        with pytest.raises(TransportInvalid) as exc:
            parse_result_json("this is prose, not json")
        assert "not one JSON object" in str(exc.value)

    def test_a_missing_result_key_reaches_the_empty_branch(self):
        """Every caller does `envelope.get("result", "")`, so an absent key and an
        empty string must land in the same, correctly-named failure."""
        with pytest.raises(TransportInvalid) as exc:
            parse_result_json({}.get("result", ""))
        assert "empty final message" in str(exc.value)
