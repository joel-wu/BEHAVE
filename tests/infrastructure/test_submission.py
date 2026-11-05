import json
from dataclasses import FrozenInstanceError

import pytest

from behave.core.records import RecordError, canonical_json
from behave.task_execution import ArtifactSelection, SubmissionRequest


def test_request_selects_explicit_multifile_artifacts_not_conventional_filenames():
    behavior = ArtifactSelection(("models/chosen.py", "lib/helper.py"), "models/chosen.py", "ChosenBehavior")
    rtl = ArtifactSelection(("rtl/compute.sv", "rtl/cells.sv"), "rtl/compute.sv", "chosen_top")
    request = SubmissionRequest(behavior, rtl)
    restored = SubmissionRequest.from_bytes(request.to_bytes())
    assert restored == request
    assert restored.testbench is None
    assert restored.behavior.entry_symbol == "ChosenBehavior"
    assert restored.rtl.entry_file == "rtl/compute.sv"
    with pytest.raises(FrozenInstanceError):
        restored.rtl = None


def test_request_record_can_be_formatted_but_cannot_supply_unknown_claims():
    request = SubmissionRequest(None, None, None)
    assert SubmissionRequest.from_bytes(json.dumps(request.as_dict(), indent=2).encode()) == request
    row = request.as_dict()
    row["passed"] = True
    with pytest.raises(RecordError):
        SubmissionRequest.from_bytes(canonical_json(row))


def test_tool_arguments_omit_record_metadata():
    request = SubmissionRequest(ArtifactSelection(("model.py",), "model.py", "M"), None)
    assert request.as_arguments() == {
        "behavior": {"files": ["model.py"], "entry_file": "model.py", "entry_symbol": "M"},
        "rtl": None,
        "testbench": None,
    }
    assert json.loads(request.to_bytes()) == {"schema": request.SCHEMA, **request.as_arguments()}
    assert SubmissionRequest.from_bytes(request.to_bytes()) == request


@pytest.mark.parametrize("parts", [(None, None), (ArtifactSelection(("b.py",), "b.py", "B"), None)])
def test_absent_components_are_explicit_not_a_tb_validity_gate(parts):
    request = SubmissionRequest(*parts)
    assert SubmissionRequest.from_bytes(request.to_bytes()) == request
    # Parsing is neither file capture nor judging: a path need not exist yet.
    assert not hasattr(request, "score") and not hasattr(request, "committed")


@pytest.mark.parametrize("paths,entry", [
    (("../private.py",), "../private.py"), (("/tmp/b.py",), "/tmp/b.py"),
    (("a", "a/b"), "a"), (("a.py", "a.py"), "a.py"), (("a.py",), "missing.py"),
])
def test_selection_rejects_unsafe_or_ambiguous_names(paths, entry):
    with pytest.raises(RecordError):
        ArtifactSelection(paths, entry, "Entry")


@pytest.mark.parametrize("change", ["missing", "version", "duplicate", "truncated", "nested-unknown"])
def test_bad_final_action_is_not_silently_accepted(change):
    row = SubmissionRequest(ArtifactSelection(("b.py",), "b.py", "B"), None).as_dict()
    if change == "missing":
        del row["behavior"]
    elif change == "version":
        row["schema"] = "unsupported"
    elif change == "nested-unknown":
        row["behavior"]["score"] = 1
    payload = canonical_json(row)
    if change == "duplicate":
        payload = payload[:-1] + b',"rtl":null}'
    elif change == "truncated":
        payload = payload[:-2]
    with pytest.raises(RecordError):
        SubmissionRequest.from_bytes(payload)
