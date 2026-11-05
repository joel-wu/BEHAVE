"""Live eligibility reuses decoded metadata without reading cumulative tokens."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_slime_records import data

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, metadata
from behave.train.recorded import capture_token_statuses, record_rollouts, token_status
from behave.train.training_data import TrainingRequirements


@pytest.mark.parametrize("variant,expected", [
    ("normal", ("eligible", None)),
    ("excluded", ("excluded", "original-exclusion")),
    ("missing", ("unavailable", "token-data-missing")),
    ("unavailable", ("unavailable", "native-counter-mismatch")),
    ("schema", ("unavailable", "token-schema-unsupported")),
    ("identity", ("unavailable", "token-provenance-mismatch")),
    ("signals", ("unavailable", "training-signals-missing")),
    ("mask", ("excluded", "no-eligible-response-tokens")),
])
def test_live_metadata_matches_cold_export_without_payload_reads(tmp_path, variant, expected):
    store, ref, _, candidates = data(tmp_path)
    candidate = candidates[0]
    requirements = candidate.requirements.as_dict()
    tokens = candidate.turns[0].tokens.as_dict()
    exclusion = None
    if variant == "excluded":
        exclusion = "original-exclusion"
    elif variant == "missing":
        tokens = None
    elif variant == "unavailable":
        tokens = {"schema": "behave.native-token-unavailable/1", "reason": "native-counter-mismatch"}
    elif variant == "schema":
        tokens["schema"] = "unknown"
    elif variant == "identity":
        tokens["checkpoint"] = BlobRef.of(b"different").as_dict()
    elif variant == "signals":
        tokens["signals"] = {}
    elif variant == "mask":
        tokens["response_mask"] = [0, 0]
    assert token_status(tokens, exclusion, requirements, object()) == expected
    response = store.put(canonical_json({"token_data": tokens, "usage": {"raw": None}}))
    account = store.put(canonical_json({"turns": [{"turn": 0, "request": ref.as_dict(),
        "response": response.as_dict(), "exclusion": exclusion}]}))
    result = replace(candidate.candidate.result, accounting=account)
    result_ref = store.put(result.to_bytes())
    plan = SimpleNamespace(requirements=TrainingRequirements.from_bytes(canonical_json(requirements)),
                           group_ids=("task",), to_bytes=lambda: b"fixture-plan")
    report = SimpleNamespace(head=ref, summary=metadata({"candidates": [{
        "result": result_ref.as_dict(), "task_id": result.task_id,
        "attempt_id": result.attempt_id, "error": None}], "billing": {}}))
    expected_report = record_rollouts(plan, store, report, object())
    cached = {}
    capture_token_statuses(result_ref, SimpleNamespace(turns=(SimpleNamespace(
        turn=0, token_data=tokens, exclusion=exclusion),)), cached, requirements, object())
    assert cached == {result_ref.sha256: {0: expected}}
    class Reader:
        def read(self, reference):
            assert reference in (result_ref, account)
            return store.read(reference)
    actual = record_rollouts(plan, Reader(), report, object(), token_statuses=cached)
    assert actual.summary == expected_report.summary
    assert "input_ids" not in json.dumps(cached)


def test_recorded_tokens_still_require_declared_verifier(tmp_path):
    _, _, _, candidates = data(tmp_path)
    example = candidates[0]
    assert token_status(example.turns[0].tokens.as_dict(), None,
                        example.requirements.as_dict(), None) == (
                            "unavailable", "token-verifier-unavailable")
