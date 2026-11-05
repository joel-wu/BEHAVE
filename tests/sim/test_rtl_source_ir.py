"""Source activation values AND occurrence hits, independent of RTLIL actions."""

from dataclasses import replace

import pytest

from behave.sim.symbolic import encoder, interp, ranges
from behave.sim.core.refusal import Refused
from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_declarations import _nodes
from behave.sim.rtl.source_ir import lower_source_process
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_observation_frontend import _installed
from .test_rtl_source_locations import _collector

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


def _lower(tmp_path, source):
    (tmp_path / "design.sv").write_text(source)
    parsed = parse_observation_source(tmp_path, "M", run=_installed)
    assert parsed is not None
    (cst, ast), _ = parsed
    plan = plan_source(source, cst, semantic_document=ast)
    context, = plan.declarations.contexts
    lowered = lower_source_process(source, ast, plan, context, filename="design.sv")
    symbols = {str(node["addr"]): node for node in _nodes(ast) if "addr" in node}
    names = {symbols[identity]["name"]: name for name, identity in lowered.storage}
    return lowered, names, cst, ast


def _encode(lowered):
    proc = lowered.procedure
    encoded = encoder.encode_step(proc, env=ranges.fixpoint(proc, proc.state_ranges))
    assert {mark for mark, _, _ in lowered.occurrences} <= encoded.hits.keys()
    return encoded


def _run_activation(lowered, names, values, enc, *, error=""):
    import z3

    proc = lowered.procedure
    state = {name: values[source] for source, name in names.items()}
    post, outputs, hits, actual_error = interp.run_step(proc, {}, state=state)
    assert actual_error == error
    assert outputs.count == int(not error)
    pending = {}
    if not error:
        for name, buffer in outputs.buffers.items():
            pending[name], = buffer
    solver = z3.Solver()
    solver.add(enc.constraint, *(term == value for name, value in state.items()
                                for term, value in _value_leaves(enc.pre[name], value)))
    assert solver.check() == z3.sat
    model = solver.model()
    assert z3.is_true(model.eval(enc.ok)) == (not error)
    assert {goal for goal, term in enc.hits.items() if z3.is_true(model.eval(term))} == hits
    for name, value in {**post, **pending}.items():
        for term, cell in _value_leaves(enc.post[name], value):
            actual = model.eval(term, model_completion=True)
            if z3.is_bool(actual):
                assert z3.is_true(actual) == cell
            else:
                assert actual.as_long() == cell % (1 << actual.size())
    return post, pending, {occurrence for mark, occurrence, _ in lowered.occurrences if mark in hits}


def _value_leaves(term, value):
    """Compare every scalar of dense or exact compact recursive state."""
    import z3

    from behave.sim.symbolic.functional_array import FunctionalArray

    if isinstance(term, FunctionalArray):
        assert term.length == len(value)
        for index, cell in enumerate(value):
            yield term.select(z3.BitVecVal(index, max(1, term.length.bit_length()+1))), cell
    elif isinstance(term, list):
        for child, cell in zip(term, value, strict=True):
            yield from _value_leaves(child, cell)
    else:
        yield term, value


@pytest.mark.parametrize('width', [4, 32, 65])
@pytest.mark.parametrize('signed', [False, True])
def test_clog2_uses_unsigned_self_determined_bits(tmp_path, width, signed):
    source = f'''module M(input clk,input {'signed' if signed else ''} [{width-1}:0] a,
      output reg [31:0] q); always @(posedge clk) q=$clog2(a); endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for bits in (0, 1, 2, 3, (1 << (width-1))-1, 1 << (width-1), (1 << width)-1):
        value = bits - (1 << width) if signed and bits >= 1 << (width-1) else bits
        post, _, _ = _run_activation(lowered, names, {'a': value, 'q': 0}, encoded)
        expected = (bits-1).bit_length() if bits else 0
        assert post[names['q']] == expected


@pytest.mark.parametrize('expression', ['$clog2(a)', '$clog2($signed(a))', '$clog2(tap(a))'])
@pytest.mark.eda('verilator', 'make')
def test_clog2_matches_native_values_and_argument_events(tmp_path, expression):
    source = f'''module M(input clk,input [3:0] a,output reg [31:0] q);
      function automatic [3:0] tap(input [3:0] x); if(x==3) return 8; return x; endfunction
      always @(posedge clk) q={expression}; endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('operation', ['^~', '~^'])
@pytest.mark.parametrize('signed', [False, True])
@pytest.mark.parametrize('widths', [(1, 1, 1), (3, 5, 7), (7, 3, 4)])
def test_binary_xnor_preserves_compiler_width_and_sign(tmp_path, operation, signed, widths):
    a_width, b_width, q_width = widths
    qualifier = 'signed' if signed else ''
    source = f'''module M(input clk,input {qualifier} [{a_width-1}:0] a,
      input {qualifier} [{b_width-1}:0] b,output reg {qualifier} [{q_width-1}:0] q);
      always @(posedge clk) q = a {operation} b;
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)

    def wrap(value, width):
        value %= 1 << width
        return value - (1 << width) if signed and value >= 1 << (width-1) else value

    for a in (0, 1, (1 << a_width)-1, 1 << (a_width-1)):
        for b in (0, 1, (1 << b_width)-1, 1 << (b_width-1)):
            a, b = wrap(a, a_width), wrap(b, b_width)
            post, _, _ = _run_activation(lowered, names, {'a': a, 'b': b, 'q': 0}, encoded)
            assert post[names['q']] == wrap(~(a ^ b), q_width)


@pytest.mark.parametrize("expression,called", [
    ("f(a)", lambda a: True),
    ("a[0] && f(a)", lambda a: bool(a & 1)),
    ("a[0] || f(a)", lambda a: not a & 1),
    ("f(0)", lambda a: True),
    ("f(a)+f(0)", lambda a: True),
])
def test_source_calls_keep_lazy_constant_and_early_return_semantics(tmp_path, expression, called):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        int x=v+1,y=x+2;
        if(v[0]) return y;
        return x;
      endfunction
      always @(posedge clk) q <= {expression};
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    assert len(lowered.deferred) == 1
    write, = lowered.deferred
    target, data, valid = write.target, write.data, write.valid
    assert target == names["q"]
    for a in range(16):
        post, outputs, hits = _run_activation(lowered, names, {"a": a, "q": 99}, enc)
        f = a + (3 if a & 1 else 1)
        expected = (int(bool(a & 1) and bool(f)) if "&&" in expression else
                    int(bool(a & 1) or bool(f)) if "||" in expression else
                    1 if expression == "f(0)" else f + 1 if "+" in expression else f)
        assert post[target] == 99  # An NBA does not become an immediate write.
        assert outputs[data] == expected and outputs[valid] is True
        locations = {source.encode()[hit.path[2]:hit.path[3]].decode()
                     for hit in hits if hit.path[1] == "location"}
        assert ("x" in locations) == called(a)
        if expression == "f(0)":
            assert "return x;" in locations and "return y;" not in locations
        if expression == "f(a)+f(0)":
            assert len([hit for hit in hits if hit.path[1] == "location"
                        and source.encode()[hit.path[2]:hit.path[3]] == b"x"]) == 2


def test_blocking_reads_and_deferred_values_use_their_program_points(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q,r);
      always @(posedge clk) begin
        q=a+1;
        if(q[0]) r<=q; else r<=a;
        q=a;
      end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    write, = lowered.deferred
    target, data, valid = write.target, write.data, write.valid
    for a in range(16):
        post, outputs, _ = _run_activation(lowered, names, {"a": a, "q": 7, "r": 9}, enc)
        assert post[names["q"]] == a and post[target] == 9
        assert outputs[data] == (a+1 if not a & 1 else a)
        assert outputs[valid] is True


@pytest.mark.parametrize("header,iterations", [
    ("int i=0;i<0;i++", 0),
    ("int i=0;i<3;i++", 3),
    ("int i=4;i>0;i=i-2", 2),
])
def test_finite_loop_reuses_the_source_plan_proof(tmp_path, header, iterations):
    source = f"""module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) begin
        q=a;
        for({header}) q=q+1;
      end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {"a": a, "q": 13}, enc)
        assert post[names["q"]] == (a+iterations) % 16
        assert {hit.role for hit in hits if hit.path[1] == "loop-activation"} == {
            "nonzero" if iterations else "zero"}
        assert (any(hit.path[1] == "location" and
                    source.encode()[hit.path[2]:hit.path[3]] == b"q=q+1;" for hit in hits)) == bool(iterations)


@pytest.mark.parametrize("default", ["", "default: q=9;"])
def test_case_first_match_default_and_no_match(tmp_path, default):
    source = f"""module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) case(a)
        1,2: q=3;
        2,4: q=5;
        {default}
      endcase
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {"a": a, "q": 13}, enc)
        assert post[names["q"]] == (3 if a in (1, 2) else 5 if a == 4 else 9 if default else 13)
        assert {hit.role for hit in hits if hit.path[1] == "control-choice"} == {
            "arm_0" if a in (1, 2) else "arm_1" if a == 4 else "default" if default else "no-match"}


@pytest.mark.parametrize("qualifier", ["unique", "unique0", "priority"])
@pytest.mark.parametrize("labels", ["2'd0,2'd1,2'd2,2'd3", "0,1,2,3"])
def test_proved_case_checks_preserve_even_unhit_catalogue_outcomes(tmp_path, qualifier, labels):
    source = f"""module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) {qualifier} case(a[1:0])
        {labels}: q=a;
      endcase
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    assert any(hit.role == "no-match" for _, hit, _ in lowered.occurrences)
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {"a": a, "q": 13}, enc)
        assert post[names["q"]] == a
        assert {hit.role for hit in hits if hit.path[1] == "control-choice"} == {"arm_0"}


@pytest.mark.parametrize("qualifier,body,a,q,error", [
    ("unique", "0:q=1;", 1, 0, "RTLCaseNoMatch"),
    ("priority", "0:q=1;", 1, 0, "RTLCaseNoMatch"),
    ("unique", "0:q=1; 0:q=2; default:q=3;", 0, 1, "RTLCaseOverlap"),
    ("unique0", "0:q=1; 0:q=2;", 0, 1, "RTLCaseOverlap"),
])
def test_unproved_case_checks_are_terminal_failures(tmp_path, qualifier, body, a, q, error):
    source = f"module M(input clk,input [3:0] a,output reg [3:0] q); always @(posedge clk) {qualifier} case(a) {body} endcase endmodule"
    lowered, names, _, _ = _lower(tmp_path, source)
    post, _, hits = _run_activation(lowered, names, {'a': a, 'q': 0}, _encode(lowered), error=error)
    assert post[names['q']] == q
    assert sum(hit.path[1] == 'artifact-failure' for hit in hits) == 1
    assert not any(hit.path[1] == 'control-choice' and hit.role == 'no-match' for hit in hits)


def test_dynamic_case_check_labels_need_native_order(tmp_path):
    source = 'module M(input clk,input [3:0] a,output reg [3:0] q); always @(posedge clk) unique case(a) q:q=1; default:q=2; endcase endmodule'
    with pytest.raises(Refused, match='constant labels'):
        _lower(tmp_path, source)


def test_intermediate_width_and_conditional_choice(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) q=(a+4'd15)<a ? 4'd3 : 4'd7;
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {"a": a, "q": 0}, enc)
        assert post[names["q"]] == (3 if a else 7)
        assert {hit.role for hit in hits if hit.path[1] == "control-choice"} == {"true" if a else "false"}


def test_signed_arithmetic_and_return_conversion(tmp_path):
    source = """module M(input clk,input signed [3:0] a,output reg signed [3:0] q);
      function automatic signed [3:0] f(input signed [3:0] v);
        if(v<0) return v-1;
        return v+1;
      endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(-8, 8):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, enc)
        raw = (a + (-1 if a < 0 else 1)) % 16
        assert post[names["q"]] == (raw-16 if raw >= 8 else raw)


@pytest.mark.parametrize("dtype,width,signed", [
    ("int", 32, True), ("int unsigned", 32, False),
    ("longint unsigned", 64, False), ("integer unsigned", 32, False),
])
def test_compiler_integer_type_signedness(tmp_path, dtype, width, signed):
    source = f"module M(input clk,input {dtype} a,output reg q); always @(posedge clk) q=a<0; endmodule"
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in ((-1, 0, (1 << (width-1))-1) if signed else (0, 1, (1 << width)-1)):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, enc)
        assert post[names["q"]] == int(a < 0)


def test_recovering_assertion_marks_failure_then_runs_action_and_remainder(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) begin
        q=0;
        assert(a[0]) q=2; else q=5;
        q=q+1;
      end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {"a": a, "q": 13}, enc)
        assert post[names["q"]] == (3 if a & 1 else 6)
        assert any(hit.path[1] == "artifact-failure" for hit in hits) == (not a & 1)


def test_constant_dead_goal_has_a_false_term_not_a_missing_catalogue_row(tmp_path):
    import z3

    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      function automatic [3:0] f(input [3:0] v);
        if(v[0]) return 1;
        return 2;
      endfunction
      always @(posedge clk) q=f(0)+a;
    endmodule"""
    lowered, _, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    choices = {occurrence.role: mark for mark, occurrence, _ in lowered.occurrences
               if occurrence.path[1] == "control-choice"}
    assert set(choices) == {"true", "false"}
    assert set(choices.values()) <= set(enc.hits)
    query = z3.Solver()
    query.add(enc.constraint, enc.hits[choices["true"]])
    assert query.check() == z3.unsat
    query = z3.Solver()
    query.add(enc.constraint, z3.Not(enc.hits[choices["false"]]))
    assert query.check() == z3.unsat


@pytest.mark.parametrize("expression,expected", [
    ("{3{a}}", lambda a: (a & 15) * 0x111),
    ("{{31{1'b0}},a}", lambda a: a & 15),
    ("{{0{1'b0}},a}", lambda a: a & 15),
    ("{a,{0{1'b1}},a}", lambda a: (a & 15) * 17),
    ("{2{a[3],{2{a[1:0]}}}}", lambda a:
        (((a & 8) << 1) | ((a & 3) * 5)) * 33),
])
def test_replication_preserves_typed_bits_without_expanding_activations(tmp_path, expression, expected):
    source = f"""module M(input clk,input signed [3:0] a,output reg [11:0] q);
      always @(posedge clk) q={expression};
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(-8, 8):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, enc)
        assert post[names["q"]] == expected(a)


@pytest.mark.parametrize("expression,expected", [
    ("$signed(a)<0", lambda a: int(a >= 8)),
    ("$signed(a)>>>1", lambda a: ((a - 16 if a >= 8 else a) >> 1) & 255),
    ("$unsigned($signed(a))", lambda a: a),
    ("-$signed({1'b0,a})", lambda a: (-a) & 255),
])
def test_system_signedness_casts_use_self_determined_bits(tmp_path, expression, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) q={expression};
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, enc)
        assert post[names["q"]] == expected(a)


@pytest.mark.parametrize("declaration,selection", [
    ("[3:0]", "a[i*2+:2]"), ("[3:0]", "a[3-i*2-:2]"),
    ("[0:3]", "a[i*2+:2]"), ("[0:3]", "a[3-i*2-:2]"),
    ("[7:4]", "a[4+i*2+:2]"), ("[4:7]", "a[7-i*2-:2]"),
])
def test_indexed_packed_read_reuses_finite_loop_bounds(tmp_path, declaration, selection):
    source = f"""module M(input clk,input {declaration} a,output reg [3:0] q);
      always @(posedge clk) begin
        q=0;
        for(int i=0;i<2;i++) q=q+{selection};
      end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, enc)
        assert post[names["q"]] == (a & 3) + (a >> 2)


@pytest.mark.parametrize("selection", ["a[i*2+:3]", "a[i*2-:2]"])
def test_indexed_packed_read_does_not_assume_out_of_range_bits(tmp_path, selection):
    source = f"""module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) for(int i=0;i<2;i++) q={selection};
    endmodule"""
    with pytest.raises(Refused) as got:
        _lower(tmp_path, source)
    assert got.value.refusal.kind == "rtl-source-ir-packed-read-domain-unproved"
    assert got.value.refusal.detail["domain_proof"] == "sat"
    assert got.value.refusal.detail["selected_width"] == (3 if "+:3" in selection else 2)
    assert got.value.refusal.detail["source_width"] == 4


@pytest.mark.eda("verilator", "make")
def test_indexed_packed_read_native_order_and_loop_hits(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) begin
        q=0;
        for(int i=0;i<2;i++) q={q[1:0],a[2*i+:2]};
      end
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_replication_evaluates_helper_once_with_the_same_native_hits(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [11:0] q);
      function automatic [3:0] f(input [3:0] v);
        q=q+1;
        if(v[0]) return q[3:0];
        return v;
      endfunction
      always @(posedge clk) q={3{f(a)}};
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('operand,neighbor', [("1'b0", 'a'), ('f(a)', 'a'), ('f(a)', 'q[3:0]')])
@pytest.mark.eda("verilator", "make")
def test_zero_replication_preserves_native_values_and_events(tmp_path, operand, neighbor):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      localparam N=4;
      function automatic [3:0] f(input [3:0] v);
        q=q+1;
        if(v[0]) return q[3:0];
        return v;
      endfunction
      always @(posedge clk) q={{{neighbor},{{(N-4){{{operand}}}}},a}};
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_system_casts_preserve_native_helper_effects_and_hits(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [11:0] q);
      function automatic [3:0] f(input [3:0] v);
        q=q+1;
        if(v[0]) return q[3:0];
        return v;
      endfunction
      always @(posedge clk) q=$unsigned($signed(f(a))>>>1);
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('selection', ['x[3]', '((x >> 3) & 1)'])
@pytest.mark.parametrize('storage_bits', [4, 64])
@pytest.mark.eda('verilator', 'make')
def test_packed_normalization_branch_preserves_native_values_and_events(tmp_path, selection, storage_bits):
    source = f'''module M(input clk,input [3:0] a,output reg [3:0] q);
      function automatic [3:0] normalize(input [3:0] value);
        reg [{storage_bits-1}:0] x;
        begin
          x=value|4'b1;
          for(integer i=0;i<4;i=i+1) if(!{selection}) x=x<<1;
          normalize=x;
        end
      endfunction
      always @(posedge clk) q=normalize(a);
    endmodule'''
    _native_agreement(tmp_path, source)


def test_conditional_nba_does_not_offer_a_write_on_the_other_branch(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) if(a[0]) begin q<=a+1; q<=a+2; end
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    enc = _encode(lowered)
    write, = lowered.deferred
    target, data, valid = write.target, write.data, write.valid
    for a in range(16):
        post, outputs, _ = _run_activation(lowered, names, {"a": a, "q": 7}, enc)
        assert post[target] == 7
        assert outputs[valid] == bool(a & 1)
        assert outputs[data] == ((a+2) % 16 if a & 1 else 7)


@pytest.mark.parametrize("statement", ["#1 q=a;", "@(negedge clk) q=a;",
                                       "q=#1 a;", "q<=#1 a;"])
def test_nested_suspension_is_not_silently_removed(tmp_path, statement):
    source = f"module M(input clk,a,output reg q); always @(posedge clk) begin {statement} end endmodule"
    with pytest.raises(Refused, match="rtl-source-ir-operation-unsupported"):
        _lower(tmp_path, source)


def _commit_pending(lowered, post, outputs):
    for write in lowered.deferred:
        if outputs[write.valid]:
            mask = outputs[write.mask]
            bits = (post[write.target] & ~mask) | (outputs[write.data] & mask)
            post[write.target] = bits & ((1 << write.width) - 1)
            if write.signed and post[write.target] & (1 << (write.width - 1)):
                post[write.target] -= 1 << write.width


def _native_agreement(tmp_path, source, *, initial=None):
    from behave.sim.rtl.source_integral import integral_type, typed_symbols

    lowered, names, cst, ast = _lower(tmp_path, source)
    symbols = typed_symbols(ast)
    identity = next(identity for name, identity in lowered.storage if name == names["q"])
    output_type = integral_type(symbols[identity]["type"], symbols)
    enc = _encode(lowered)
    plan = plan_source(source, cst, semantic_document=ast)
    collector, _ = _collector(plan)
    words = (collector.bindings.event_count + 63) // 64
    driver = f"""
      for(unsigned a=0;a<16;a++) {{
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0;dut.a=a;dut.eval();dut.clk=1;dut.eval();
        uint64_t bits[{words}]={{}},calls=0;
        if(behave_native_events_end_v1(a,bits,{words},&calls)) return 2;
        std::cout<<unsigned(dut.q);
        for(auto word:bits) std::cout<<" "<<word;
        std::cout<<" "<<calls<<"\\n";
      }}
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver, event_words=words)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver,
                    native=True, event_words=words)
    assert len(plain) == len(native) == 16
    previous = 0
    for a, (unmodified, instrumented) in enumerate(zip(plain, native, strict=True)):
        post, outputs, predicted = _run_activation(
            lowered, names, {**(initial or {}), "a": a, "q": previous}, enc)
        seen = {event for event in range(collector.bindings.event_count) if instrumented[1] >> event & 1}
        actual = collector.bindings.decode(seen)
        assert unmodified[0] == instrumented[0]
        assert predicted == actual.occurrences, {
            "input": a, "native_output": instrumented[0], "ir_post": post,
            "missing": [key.as_dict() for key in predicted - actual.occurrences],
            "extra": [key.as_dict() for key in actual.occurrences - predicted],
        }
        _commit_pending(lowered, post, outputs)
        assert output_type.decode(unmodified[0]) == post[names["q"]]
        previous = post[names["q"]]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("expression", ["a[0] && f(a)", "f(0)"])
def test_source_ir_matches_real_native_occurrences(tmp_path, expression):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        if(v[0]) return v+1;
        return v+2;
      endfunction
      always @(posedge clk) q <= {expression};
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("body", [
    "begin q=a; for(int i=0;i<3;i++) if(q[0]) q=q+1; else q=q+2; end",
    "begin q=a; for(int i=0;i<0;i++) q=q+1; end",
    "case(a) 1,2:q=3; 2,4:q=5; default:q=9; endcase",
    "unique case(a[1:0]) 0:q=3; 1:q=5; 2,3:q=9; endcase",
    "unique case(a) 1:q=3; 2:q=5; default:q=9; endcase",
    "unique0 case(a) 1:q=3; 2:q=5; endcase",
    "priority case(a) 1,2:q=3; 2,4:q=5; default:q=9; endcase",
    "begin q=0; assert(a[0]) q=2; else q=5; q=q+1; end",
    "q=a[0] && (a[1] || a[2]);",
])
def test_source_control_occurrences_match_the_real_native_producer(tmp_path, body):
    source = f"module M(input clk,input [3:0] a,output reg [7:0] q); always @(posedge clk) {body} endmodule"
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("dtype", ["int", "int unsigned", "longint unsigned"])
def test_source_integer_conversion_and_choices_match_native(tmp_path, dtype):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        {dtype} x=-1;
        if(x<0) return v+1;
        return v+2;
      endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_return_stops_remaining_loop_activations(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        for(int i=0;i<3;i++) if(i==v) return 7;
        return 9;
      endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_predefined_integer_bits_follow_compiler_width(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        int x=-1;
        return x[31]+v;
      endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize("body", ["if(v) return 1;", "f=f+1;", "return f;", "int x=f; f=x;"])
def test_undefined_source_return_is_not_replaced_with_scratch_zero(tmp_path, body):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v); {body} endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    with pytest.raises(Refused, match="rtl-source-ir-return-undefined"):
        _lower(tmp_path, source)


@pytest.mark.parametrize("expression", ["change(a)+q", "q+change(a)", "combine(change(a),q)"])
def test_conflicting_operand_effects_do_not_acquire_an_invented_order(tmp_path, expression):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] change(input [3:0] v); q=v; return v+1; endfunction
      function automatic [7:0] combine(input [7:0] x,y); return x+y; endfunction
      always @(posedge clk) q={expression};
    endmodule"""
    with pytest.raises(Refused, match="operand effects need a qualified evaluation order"):
        _lower(tmp_path, source)


def test_independent_operand_effects_are_not_blanket_refused(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q,r,s);
      function automatic [7:0] f(input [3:0] v); q=v+1; return q; endfunction
      function automatic [7:0] g(input [3:0] v); r=v+2; return r; endfunction
      always @(posedge clk) s=f(a)+g(a);
    endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0, "r": 0, "s": 0}, encoded)
        assert post[names["q"]] == a+1
        assert post[names["r"]] == a+2
        assert post[names["s"]] == 2*a+3


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("op", ["&&", "||"])
def test_short_circuit_operand_effects_keep_the_qualified_native_order(tmp_path, op):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic logic change(input [3:0] v); q=v; return q[0]; endfunction
      always @(posedge clk) q=change(a) {op} change(a+1);
    endmodule"""
    _native_agreement(tmp_path, source)
