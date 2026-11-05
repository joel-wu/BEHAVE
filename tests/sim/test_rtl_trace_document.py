"""No reader may turn a malformed or contradictory response into one good trace."""

import json

import pytest

from behave.sim.core.failure import RunFailure
from behave.sim.core.refusal import Refusal
from behave.sim.rtl.trace_document import read_trace_document, write_trace_document


def _startup():
    return RunFailure.verifier("rtl-analysis-startup", Refusal("unavailable", "not installed"))


@pytest.mark.parametrize("document", [
    [], {"traces": {}}, {"traces": [0]}, {"traces": [None]},
    {"traces": [{}], "unknown": 1}, {"traces": [{}, {}]},
    {"traces": [{}], "startup_failure": _startup().as_dict()},
    {"traces": [], "startup_failure": None},
    {"traces": [], "startup_failure": RunFailure.candidate(
        "rtl-analysis-startup", Refusal("candidate", "not a startup refusal")).as_dict()},
    {"traces": [], "startup_failure": RunFailure.infrastructure(
        "rtl-analysis-startup", Refusal("infra", "not a verifier startup refusal")).as_dict()},
    {"traces": [], "startup_failure": RunFailure.verifier(
        "unknown-phase", Refusal("verifier", "not startup")).as_dict()},
])
def test_response_must_be_closed_and_cannot_hide_failure(document):
    with pytest.raises((ValueError, TypeError)):
        read_trace_document(json.dumps(document), max_traces=1)


def test_request_bound_is_not_a_coercible_or_unbounded_integer():
    for bound in (-1, True, None, 1.5, "1"):
        with pytest.raises(ValueError):
            read_trace_document('{"traces":[]}', max_traces=bound)


@pytest.mark.parametrize("payload", [
    '{"traces":[],"traces":[{}]}',
    '{"traces":[{"records":[],"records":[{}]}]}',
    '{"traces":[{"passed":true,"passed":false}]}',
])
def test_duplicates_on_the_wire_are_not_last_field_wins(payload):
    with pytest.raises(ValueError, match="duplicate"):
        read_trace_document(payload, max_traces=1)


def test_atomic_prefix_publication_preserves_the_previous_record_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "driven.json"
    write_trace_document(path, [])
    assert read_trace_document(path.read_bytes(), max_traces=2) == ([], None)
    trace = {"records": [], "marker": "first"}
    write_trace_document(path, [trace])
    before = path.read_bytes()
    assert read_trace_document(before, max_traces=2) == ([trace], None)
    assert not path.with_suffix(".partial").exists()

    # An unserializable record cannot destroy the already completed prefix.
    with pytest.raises((ValueError, TypeError)):
        write_trace_document(path, [trace, {"bad": object()}])
    assert path.read_bytes() == before

    def no_replace(_source, _target):
        raise OSError("test-only rename failure")

    monkeypatch.setattr(type(path), "replace", no_replace)
    with pytest.raises(OSError, match="rename"):
        write_trace_document(path, [trace, {"marker": "second"}])
    assert path.read_bytes() == before


def test_startup_no_result_is_read_without_coercing_it_to_a_boolean(tmp_path):
    path = tmp_path / "driven.json"
    failure = _startup()
    write_trace_document(path, [], startup_failure=failure)
    traces, decoded = read_trace_document(path.read_bytes(), max_traces=1)
    assert traces == [] and decoded.as_dict() == failure.as_dict()
    with pytest.raises(ValueError, match="before any trace"):
        write_trace_document(path, [{}], startup_failure=failure)


def test_all_trace_arrays_keep_exact_canonical_evidence_bytes(tmp_path):
    from behave.sim.core.record_value import canonical_json

    trace = {"records": [{"outputs": [["y", 2**256]], "hits": []}] * 50,
             "source_evidence": {"intervals": [
                 {"phase": "control", "events": "abc", "callbacks": 1}] * 50},
             "resolved_stimulus": {"slots": [{"reset": False}] * 50}}
    path = tmp_path / "driven.json"
    write_trace_document(path, [trace, trace])
    decoded, failure = read_trace_document(path.read_bytes(), max_traces=2)
    assert failure is None
    assert canonical_json({"traces": decoded}) == canonical_json({"traces": [trace, trace]})
    assert len(path.read_bytes()) < len(canonical_json({"traces": [trace, trace]})) // 4
    assert decoded[0]["records"][0] is decoded[0]["records"][49]
    assert trace["records"][1] == trace["records"][0]  # caller rows were not replaced


def test_slot_decoder_keeps_shared_rows_without_sharing_mutable_input(tmp_path):
    from behave.sim import api
    from behave.sim.core.driver import check_channel_shape
    from behave.sim.core.hit_vector import HIT_VECTOR_ENCODING, HitVector
    from behave.sim.rtl.execution_policy import execution_policy
    from behave.sim.rtl.trace_decoder import TraceDecoder

    from .test_rtl_execution_policy import _contract

    slot = api.EventSlot(controls={"rst": 0, "out_ready": 1})
    stimulus = api.Stimulus({}, [slot] * 128)
    row = api.SlotRecord(outputs={"out_valid": 0, "y": 0}).as_dict()
    row["hits"] = HitVector.from_names(()).as_compact_dict()
    changed = {**row, "outputs": [["out_valid", 0], ["y", 9]]}
    document = {"execution_policy": execution_policy(),
                "records": [row] * 126 + [changed, row],
                "structural_refusals": {}, "hit_encoding": HIT_VECTOR_ENCODING,
                "hit_catalogue": []}
    path = tmp_path / "trace.json"
    write_trace_document(path, [document])
    traces, _ = read_trace_document(path.read_bytes(), max_traces=1)
    decoder = TraceDecoder(None, {})
    decoded = decoder.decode(_contract(), stimulus, traces[0], check_channel_shape, "dut")
    assert type(decoded) is api.AuditedTrace, decoded
    assert len(decoded.records) == 128
    assert decoded.records[0] is decoded.records[125]
    assert decoded.records[0] is decoded.records[127]
    assert dict(decoded.records[126].outputs)["y"] == 9
    assert dict(decoded.records[127].outputs)["y"] == 0
    # Each invocation owns its decoding; sharing never hides a later bad row.
    traces[0]["records"][-1] = {**row, "unknown": True}
    refused = decoder.decode(_contract(), stimulus, traces[0], check_channel_shape, "dut")
    assert type(refused) is Refusal and refused.kind == "rtl-artifact-structural-record-malformed"
    traces[0]["records"][0]["outputs"][1][1] = 42
    assert dict(decoded.records[0].outputs)["y"] == 0


@pytest.mark.parametrize("capacity", [0, 1, 2, 256])
def test_record_reference_eviction_changes_only_representation(tmp_path, monkeypatch, capacity):
    from behave.core import runtime_defaults as defaults

    monkeypatch.setattr(defaults, "SIM_RTL_TRACE_RECORD_CACHE_ENTRIES", capacity)
    first, second = {"sample": [1, -0.0, True]}, {"sample": [2**256]}
    trace = {"records": [first, second, first, first, second, dict(first)]}
    path = tmp_path / "driven.json"
    write_trace_document(path, [trace])
    decoded, failure = read_trace_document(path.read_bytes(), max_traces=1)
    assert decoded == [trace] and failure is None
    wire = json.loads(path.read_text())["traces"][0]["records"]
    assert type(wire[-1]) is dict  # equal but separately owned rows need not share
    if capacity == 0:
        assert all(type(row) is dict for row in wire)
    else:
        assert type(wire[3]) is int


@pytest.mark.parametrize("bad", [-1, 0, 1, True, 0.0, None, "0", [], {"ref": 0}])
def test_references_are_not_coerced_or_forward_resolved(bad):
    document = {"record_encoding": "rtl-trace-record-references/1", "traces": [{"records": [bad]}]}
    # A literal object is not a reference and is left to the bound slot decoder.
    if type(bad) is dict:
        assert read_trace_document(json.dumps(document), max_traces=1)[0][0]["records"] == [bad]
    else:
        with pytest.raises(ValueError, match="earlier row"):
            read_trace_document(json.dumps(document), max_traces=1)


def test_reference_scope_cannot_cross_trace_or_schema_arrays():
    for traces in ([{"records": [{"x": 1}]}, {"records": [0]}],
                   [{"records": [{"x": 1}], "source_evidence": {"intervals": [0]}}]):
        with pytest.raises(ValueError, match="earlier row"):
            read_trace_document(json.dumps({"record_encoding": "rtl-trace-record-references/1",
                                            "traces": traces}), max_traces=2)


def test_unknown_encoding_and_late_invalid_record_do_not_pass(tmp_path):
    with pytest.raises(ValueError, match="unknown record encoding"):
        read_trace_document('{"traces":[],"record_encoding":"future"}', max_traces=1)
    path = tmp_path / "driven.json"
    write_trace_document(path, [{"records": [{"x": 1}]}])
    before = path.read_bytes()
    with pytest.raises(ValueError, match="object rows"):
        write_trace_document(path, [{"records": [{"x": 1}, 0]}])
    assert path.read_bytes() == before


def test_record_write_failure_cannot_replace_completed_prefix(tmp_path, monkeypatch):
    import behave.sim.rtl.trace_document as module

    path = tmp_path / "driven.json"
    write_trace_document(path, [{"records": [{"a": 1}]}])
    before = path.read_bytes()

    def fail_after_beginning(document, stream, **kwargs):
        stream.write('{"traces":[')
        raise OSError("test-only disk failure")

    monkeypatch.setattr(module, "write_canonical_json", fail_after_beginning)
    with pytest.raises(OSError, match="disk failure"):
        write_trace_document(path, [{"records": [{"a": 2}]}])
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode,defect", [
    (mode, defect) for mode in ("single", "batch")
    for defect in (None, "extra-trace", "unknown-field", "failure-and-trace")
] + [("batch", "short-prefix")])
def test_production_readers_validate_envelope_before_any_slot(
        tmp_path, monkeypatch, mode, defect):
    from behave.sim import api
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.core.hit_vector import HIT_VECTOR_ENCODING, HitVector
    from behave.sim.rtl import tool_isolation
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.execution_policy import execution_policy
    from behave.sim.rtl.observation_binding import ObservationBinding, PreparedObservation
    from behave.sim.rtl.trace_decoder import TraceDecoder

    from .test_rtl_execution_policy import _contract
    from .test_rtl_tool_isolation import _command_row, _completed

    record = api.SlotRecord(outputs={"out_valid": 0, "y": 0}).as_dict()
    record["hits"] = HitVector.from_names(()).as_compact_dict()
    trace = {"execution_policy": execution_policy(), "records": [record],
             "structural_refusals": {}, "hit_encoding": HIT_VECTOR_ENCODING,
             "hit_catalogue": []}
    document = {"traces": [trace]}
    if defect == "extra-trace":
        document["traces"].append(trace)
    elif defect == "unknown-field":
        document["silently_ignored_fault"] = "test-only extra evidence"
    elif defect == "failure-and-trace":
        document["startup_failure"] = _startup().as_dict()
    payload = json.dumps(document).encode()

    def simulate(identifier):
        return _completed(commands=(_command_row(identifier),), outputs={"driven.json": payload})

    def single(workspace, _groups, *, role, **_kwargs):
        if role == "verilator-build":
            build = workspace / "build"
            build.mkdir()
            (build / "Vtop").write_bytes(b"pytest-only, never executed")
            return _completed(commands=(_command_row("verilator-build"),),
                              outputs={"build/Vtop": b"pytest-only, never executed"})
        return simulate("verilator-simulate")

    def batch(_workspace, **_kwargs):
        return _completed(), simulate("verilator-batch:simulate")

    monkeypatch.setattr(tool_isolation, "run_workspace", single)
    monkeypatch.setattr(tool_isolation, "run_verilator_workspace", batch)
    design = tmp_path / "candidate.sv"
    design.write_text("module M(); endmodule")
    artifact = RTLArtifact(design, "M", tmp_path / "run")
    # This test owns simulator response bytes, not a compiler response. Freeze
    # its empty observation before mocking the tool service used by replay.
    catalogue = CatalogueManifest(())
    artifact._observation = PreparedObservation(ObservationBinding(
        artifact.identity, _contract().digest, catalogue.digest()), None)
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_: catalogue)
    interpreted = []
    # Both serial and postprocessed replay use this decoder. The artifact's
    # compatibility wrapper is no longer on the per-process replay path.
    decode = TraceDecoder.decode

    def checked(*args, **kwargs):
        interpreted.append(True)
        return decode(*args, **kwargs)

    monkeypatch.setattr(TraceDecoder, "decode", checked)
    stimulus = api.Stimulus({}, [api.EventSlot(controls={"rst": 0, "out_ready": 1})])
    if defect == "short-prefix":
        first, missing = artifact.traces(_contract(), [stimulus, stimulus], verify_batch=False)
        assert type(first) is api.AuditedTrace, first
        assert type(missing) is Refusal and missing.kind == "rtl-artifact-batch-stopped-short"
        assert interpreted == [True]
        return
    result = (artifact.traces_per_process(_contract(), [stimulus], workers=1)[0]
              if mode == "single" else artifact.traces(_contract(), [stimulus], verify_batch=False)[0])
    if defect is None:
        assert type(result) is api.AuditedTrace, result
        assert interpreted == [True]
    else:
        if mode == "single":
            assert type(result) is RunFailure, result
            assert not result.determines_score
            result = result.reason
        assert type(result) is Refusal and result.kind == "rtl-artifact-trace-malformed"
        assert interpreted == []
