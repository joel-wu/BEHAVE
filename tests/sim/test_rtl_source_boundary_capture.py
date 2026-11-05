"""First-presentation query observations do not become architectural state."""

import json

import pytest

from behave.sim import api
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.rtl.source_boundary_capture import BoundaryCapture, boundaries_of, observation_fields


class Signal:
    def __init__(self, value, width=8):
        self.value, self.width = value, width

    def __len__(self):
        return self.width


def test_capture_does_not_resample_a_stalled_input_or_add_drives(monkeypatch):
    dut = {"x": Signal(17), "wrong_width": Signal(1, 2)}
    reads = []

    def read(handle):
        reads.append(handle)
        return None if handle is None else handle.value

    monkeypatch.setattr("behave.sim.rtl.source_handles.read_handle", read)
    capture = BoundaryCapture(dut, (("x", 8), ("missing", 8), ("wrong_width", 8)))
    capture.observe(0, {"in": None}, {"in": 0})
    assert not reads
    capture.observe(1, {"in": (3,)}, {"in": 0})
    dut["x"].value = 42
    capture.observe(2, {"in": (3,)}, {"in": 0})
    capture.observe(3, {"in": (7,)}, {"in": 1})
    assert len(reads) == 6
    assert boundaries_of(capture.as_dict()) == {
        1: {"x": 17, "missing": None, "wrong_width": None},
        3: {"x": 42, "missing": None, "wrong_width": None}}


def test_query_boundary_roundtrip_is_separate_from_reset_state_and_hits():
    dut = {"x": Signal(19)}
    capture = BoundaryCapture(dut, (("x", 8),))
    capture.observe(0, {"in": (3,)}, {"in": 0})
    seed = api.Stimulus({"in": [(3,)]}, [api.EventSlot(offers=("in",))])
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(accepted={"in": (3,)})],
                             query_boundaries=capture.as_dict())
    restored = api.AuditedTrace.from_dict(trace.as_dict())
    assert not restored.initial_state and not restored.records[0].state
    assert not restored.records[0].hits
    assert boundaries_of(restored.query_boundaries)[0]["x"] == 19
    assert restored.fingerprint == trace.fingerprint


def test_scoped_capture_keeps_paths_distinct_from_literal_identifiers():
    dut = {"lanes": {4: {"value": Signal(15, 4)}, 8: {"value": Signal(174)}},
           "lanes[4].value": Signal(99)}
    fields = (("lanes[4].value", 8), (("lanes", 4, "value"), 4), (("lanes", 8, "value"), 8))
    capture = BoundaryCapture(dut, json.loads(json.dumps(fields)))
    capture.observe(0, {"in": (0,)}, {"in": 0})
    assert boundaries_of(json.loads(json.dumps(capture.as_dict())))[0] == {
        "lanes[4].value": 99, ("lanes", 4, "value"): 15, ("lanes", 8, "value"): 174}


@pytest.mark.eda("slang")
def test_generated_storage_uses_compiler_scopes_and_actual_indices(tmp_path):
    from behave.sim.rtl.shared_frontend import parse_observation_source

    from .test_rtl_observation_frontend import _installed

    (tmp_path / "design.sv").write_text('''module M(input [7:0] a, output [7:0] y);
      wire [7:0] value;
      assign value=a;
      for(genvar k=8;k>=4;k-=4) begin: lanes
        if(1) begin: body
          wire [k-1:0] value;
          assign value=a[k-1:0];
        end
      end
      assign y=value ^ lanes[4].body.value ^ lanes[8].body.value;
    endmodule''')
    (_, ast), _ = parse_observation_source(tmp_path, "M", run=_installed)
    assert dict(observation_fields(ast)) == {"a": 8, "y": 8, "value": 8,
        ("lanes", 4, "body", "value"): 4, ("lanes", 8, "body", "value"): 8}


def test_query_storage_limitation_does_not_refuse_native_preparation(monkeypatch):
    def unsupported(*args, **kwargs):
        raise Refused(Refusal("rtl-source-storage-instance-unresolved", "query representation unavailable"))

    monkeypatch.setattr("behave.sim.rtl.source_storage.storage_bindings", unsupported)
    assert observation_fields({"design": {}}) == ()


@pytest.mark.eda("slang")
def test_named_procedural_storage_retains_nested_scope_and_shadowed_width(tmp_path):
    from behave.sim.rtl.shared_frontend import parse_observation_source

    from .test_rtl_observation_frontend import _installed

    (tmp_path / "design.sv").write_text('''module M(input [7:0] a, output logic [7:0] y);
      logic [7:0] value;
      always_comb begin: outer
        logic [3:0] value;
        value=a[3:0];
        begin: inner
          logic [1:0] value;
          value=a[1:0];
          y=value;
        end
        y=y ^ value;
      end
      assign value=a;
    endmodule''')
    (_, ast), _ = parse_observation_source(tmp_path, "M", run=_installed)
    fields = dict(observation_fields(ast))
    assert fields["value"] == 8
    assert fields["outer", "value"] == 4
    assert fields["outer", "inner", "value"] == 2


def test_array_capture_uses_declared_indices_and_preserves_unknown_elements():
    shape = {"left": 4, "right": 5,
             "element": {"left": 3, "right": 1, "element": 8}}
    dut = {"x": {4: {3: Signal(255), 2: Signal(2), 1: Signal(3)},
                 5: {3: Signal(4), 2: Signal("xxxxxxxx"), 1: Signal(6, 4)}}}
    capture = BoundaryCapture(dut, (("x", shape),))
    capture.observe(0, {"in": (0,)}, {"in": 0})
    assert boundaries_of(capture.as_dict())[0]["x"] == [[255, 2, 3], [4, "xxxxxxxx", None]]
    # Later writes neither mutate an earlier image nor reread a stalled entry.
    dut["x"][4][3].value = 7
    capture.observe(1, {"in": (0,)}, {"in": 0})
    capture.observe(2, {"in": (1,)}, {"in": 1})
    assert list(boundaries_of(capture.as_dict())) == [0, 2]
    assert boundaries_of(capture.as_dict())[0]["x"][0][0] == 255
    assert boundaries_of(capture.as_dict())[2]["x"][0][0] == 7


@pytest.mark.eda("slang")
def test_child_storage_retains_instances_generate_indices_and_literal_names(tmp_path):
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_storage import storage_bindings

    from .test_rtl_observation_frontend import _installed

    (tmp_path / "design.sv").write_text(r'''module Leaf #(parameter W=8);
      logic [W-1:0] value;
      always_comb value=M.a;
    endmodule
    module Branch;
      Leaf leaf();
      Leaf \literal.with[3] ();
    endmodule
    module M(input [7:0] a, output [7:0] y);
      logic [7:0] value;
      Branch left();
      Branch right();
      for(genvar k=8;k>=4;k-=4) begin: lanes
        if(1) begin: body
          Leaf #(.W(k)) child();
        end
      end
      assign value=a;
      assign y=value ^ left.leaf.value ^ right.leaf.value;
    endmodule''')
    (_, ast), _ = parse_observation_source(tmp_path, "M", run=_installed)
    fields = dict(observation_fields(ast))
    assert fields == {"a": 8, "y": 8, "value": 8,
        ("left", "leaf", "value"): 8, ("right", "leaf", "value"): 8,
        ("left", "literal.with[3]", "value"): 8,
        ("right", "literal.with[3]", "value"): 8,
        ("lanes", 4, "body", "child", "value"): 4,
        ("lanes", 8, "body", "child", "value"): 8}
    bindings = storage_bindings(ast)
    assert {cell.observation for cell in bindings.values() if cell.input} == {"a"}
    values = [cell for cell in bindings.values() if cell.name == "value"]
    assert len({cell.key for cell in values}) == len({cell.observation for cell in values}) == 7


@pytest.mark.eda("slang")
def test_child_ports_are_observed_but_are_not_extra_driver_inputs(tmp_path):
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_storage import storage_bindings

    from .test_rtl_observation_frontend import _installed

    (tmp_path / "design.sv").write_text('''module C(input [3:0] a, output [3:0] q);
      assign q=a;
    endmodule
    module M(input [3:0] a, output [3:0] q); C c(a,q); endmodule''')
    (_, ast), _ = parse_observation_source(tmp_path, "M", run=_installed)
    fields = dict(observation_fields(ast))
    assert fields["c", "a"] == fields["c", "q"] == 4
    inputs = [cell for cell in storage_bindings(ast).values() if cell.input]
    assert len(inputs) == 1 and inputs[0].observation == "a"
