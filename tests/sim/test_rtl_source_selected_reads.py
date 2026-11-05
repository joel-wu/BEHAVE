"""Selected reads retain HDL values/events without snapshotting whole arrays."""

import pytest

from behave.sim.symbolic import encoder, ir, ranges, typecheck as types
from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_ir import _Lowerer

from .test_rtl_source_ir import _encode, _lower, _native_agreement, _run_activation, _value_leaves

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.parametrize("declaration,selection,contents,expected", [
    ("[4:7]", "x[4+a[1:0]]", [11, 22, 33, 44], [11, 22, 33, 44]),
    ("[7:4]", "x[4+a[1:0]]", [11, 22, 33, 44], [44, 33, 22, 11]),
    ("[5:4][2:3]", "x[4+a[1]][2+a[0]]", [[11, 22], [33, 44]], [33, 44, 11, 22]),
])
def test_named_array_reads_do_not_copy_unselected_cells(tmp_path, declaration, selection, contents, expected):
    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      logic [7:0] x{declaration};
      always @(posedge clk) q={selection};
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    proc = lowered.procedure
    assert not [command for command in ir.walk(proc.body)
                if isinstance(command, ir.Assign) and isinstance(proc.types[command.var], types.Array)]
    encoded = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 255, "x": contents}, encoded)
        assert post[names["q"]] == expected[a % 4]
        assert post[names["x"]] == contents


def test_selected_elaboration_constant_is_not_runtime_storage(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      localparam [7:0] P=8'hA5;
      always @(posedge clk) q=P[a[1:0]+:2];
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    assert "P" not in names
    encoded = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, encoded)
        assert post[names["q"]] == (0xA5 >> (a % 4)) & 3


@pytest.mark.eda("verilator", "make")
def test_selected_read_captures_effectful_index_once_and_keeps_native_hits(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      logic [7:0] x[3:0];
      logic [1:0] i;
      always @(posedge clk) begin
        x='{8'd11,8'd22,8'd33,8'd44};
        i=a[1:0];
        q=x[i++];
        q=q+i;
      end
    endmodule"""
    _native_agreement(tmp_path, source, initial={"x": [0, 0, 0, 0], "i": 0})


def test_selected_read_does_not_reorder_a_conflicting_index_helper(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      logic [7:0] x[0:3];
      function automatic [1:0] change(); x[0]=a; return 0; endfunction
      always @(posedge clk) q=x[change()];
    endmodule"""
    with pytest.raises(Refused, match="qualified evaluation order"):
        _lower(tmp_path, source)


def test_selected_computed_array_retains_value_capture(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      typedef logic [7:0] bank_t[0:3];
      function automatic bank_t bank(input [3:0] v);
        return '{8'(v),8'(v+1),8'(v+2),8'(v+3)};
      endfunction
      always @(posedge clk) q=bank(a)[a[1:0]];
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, encoded)
        assert post[names["q"]] == a + a % 4


@pytest.mark.parametrize("body", [
    "q=x[a[1:0]];",
    "q=a[0] ? x[a[1:0]] : x[3-a[1:0]];",
    "q=x[0]; x[0]=a; q=q+x[0];",
])
def test_selected_reads_equal_captured_array_for_every_input_and_state(tmp_path, monkeypatch, body):
    import z3

    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      logic [7:0] x[0:3];
      always @(posedge clk) begin {body} end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    original = tmp_path / "captured"
    original.mkdir()
    # Keep the pre-optimization expression path as the comparison, only for
    # this module-storage fixture (there are no partially initialized locals).
    with monkeypatch.context() as patch:
        patch.setattr(_Lowerer, "storage_selection", lambda *_: False)
        captured, old_names, _, _ = _lower(original, source)

    def encode(value, tag):
        proc = value.procedure
        return encoder.encode_step(proc, env=ranges.fixpoint(proc, proc.state_ranges), tag=tag)

    before, after = encode(captured, "captured"), encode(lowered, "selected")
    solver = z3.Solver()
    solver.add(before.constraint, after.constraint)
    different = [before.ok != after.ok, before.error != after.error]
    for name, field in names.items():
        for new, old in _value_leaves(after.pre[field], before.pre[old_names[name]]):
            solver.add(new == old)
        for new, old in _value_leaves(after.post[field], before.post[old_names[name]]):
            different.append(new != old)
    assert before.hits.keys() == after.hits.keys()
    different.extend(after.hits[goal] != before.hits[goal] for goal in after.hits)
    assert solver.check() == z3.sat
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat
