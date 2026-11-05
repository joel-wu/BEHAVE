"""Typed loop bounds preserve actual tests, every goal and native witnesses."""

import pytest

from behave.sim.core.refusal import Refused
from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_integral import typed_symbols
from behave.sim.rtl.source_ir import lower_source_process
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_observation_frontend import _installed
from .test_rtl_source_ir import _commit_pending, _encode, _run_activation, _value_leaves
from .test_rtl_source_locations import _prepare_clocked_replay

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


def _lower(where, source):
    (where / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(where, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast,
                      sensitivity_document=sensitivity, enumerate_iterations=False)
    context, = plan.declarations.contexts
    lowered = lower_source_process(source, ast, plan, context, filename="design.sv")
    symbols = typed_symbols(ast)
    names = {symbols[identity]["name"]: name for name, identity in lowered.storage}
    return plan, lowered, names


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("header,expected", [
    ("int i=a;i<15;i++", lambda a: 15-a),
    ("int i=a;i>0;i-=2", lambda a: (a+1)//2),
    ("int i=4'(a+1);i<15;i++", lambda a: 15-(a+1)%16),
    ("int i=-1;i<a;i++", lambda a: 0),  # unsigned comparison is false
])
def test_runtime_initial_value_keeps_typed_conversion_and_every_goal(
        tmp_path, record_property, header, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for({header}) begin q=q+1; if(a==7) q=q; if(1'b0) q=99; end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("transfer", ["", "if(j==2) continue;", "if(j==3) break;"])
def test_outer_counter_initializes_inner_loop_without_invented_coordinates(
        tmp_path, record_property, transfer):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for(int i=0;i<3;i++) begin
          for(int j=i+1;j<4;j++) begin
            {transfer}
            q=q+1; if(a==7 && j==3) q=q; if(1'b0) q=99;
          end
        end
      end
    endmodule"""
    expected = 6 if not transfer else 4 if "continue" in transfer else 3
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("outer,inner,expected", [
    ("int limit=5;limit>0;limit--", "int j=0;j<limit;j++", 15),
    ("int limit=-3;limit<0;limit++", "int j=0;j>limit;j--", 6),
    ("int limit=0;limit<3;limit++", "int j=0;j<=limit+1;j++", 9),
    # The size cast retains the signed int operand: 2, 3, 4 become -2, -1, 0.
    ("int limit=1;limit<=3;limit++", "int j=0;j<2'(limit+1);j++", 0),
    ("int limit=1;limit<=3;limit++", "int j=0;j<$unsigned(2'(limit+1));j++", 5),
])
def test_enclosing_counter_bounds_inner_loop_and_preserves_all_goals(
        tmp_path, record_property, monkeypatch, outer, inner, expected):
    from behave.sim.rtl import source_ir

    progress = source_ir.for_progress
    cutoffs = []

    def observe(*args, **kwargs):
        result = progress(*args, **kwargs)
        if kwargs.get("bound_ranges"):
            cutoffs.append(result.cutoff)
        return result

    monkeypatch.setattr(source_ir, "for_progress", observe)
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for({outer}) begin
          for({inner}) begin q=q+1; if(a==7) q=q; if(1'b0) q=99; end
        end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: expected)
    assert cutoffs and all(-5 <= cutoff <= 5 for cutoff in cutoffs)


def test_mutating_enclosing_limit_is_not_used_to_truncate_nested_execution(tmp_path):
    source = """module M(input clk,output reg [7:0] q);
      logic [3:0] limit;
      always @(posedge clk) begin
        q=0;
        for(limit=3;limit>0;limit--) begin
          for(int j=0;j<limit;j++) begin
            if(q==0) limit=4;
            q=q+1;
          end
        end
      end
    endmodule"""
    # The body increases the outer ranking distance. Its original three-step
    # certificate must fail, not silently discard the final live iteration.
    with pytest.raises(Refused, match="rtl-source-ir-loop-domain-unproved"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("outer,test,step,posttest,expected", [
    ("int limit=5;limit>0;limit--", "j<limit", "j++", False, 15),
    ("int limit=-3;limit<0;limit++", "j>limit", "j--", False, 6),
    ("int limit=-1;limit<=1;limit++", "j<limit", "j++", False, 1),
    ("int limit=-1;limit<=1;limit++", "j<limit", "j++", True, 3),
])
def test_enclosing_counter_bounds_pre_and_post_tested_loops(
        tmp_path, record_property, outer, test, step, posttest, expected):
    body = f"begin q=q+1; {step}; if(a==1) q=q; if(1'b0) q=99; end"
    loop = f"do {body} while({test});" if posttest else f"while({test}) {body}"
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for({outer}) begin int j; j=0; {loop} end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(4), lambda a: expected)


@pytest.mark.eda("verilator", "make")
def test_dynamic_initializer_and_final_test_effects_execute_once(tmp_path, record_property):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic int first(input [3:0] v); q=q+10; return v; endfunction
      function automatic [3:0] last(); q=q+1; return 15; endfunction
      always @(posedge clk) begin
        q=0;
        for(int i=first(a);i<last();i++) begin
          q=q+1; if(1'b0) q=99;
        end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: 11+2*(15-a))


def test_step_helper_cannot_hide_an_extra_counter_update(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      int i;
      function automatic int second(); i--; return 0; endfunction
      always @(posedge clk) begin
        q=0;
        for(i=a;i<15;i++,q=second()) begin q=q+1; end
      end
    endmodule"""
    with pytest.raises(Refused, match="for step changes its counter"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("header,expected", [
    ("int i=0;i<a;i++", lambda a: a),
    ("int i=0;i<=a;i+=2", lambda a: a // 2 + 1),
    ("int i=15;i>a;i--", lambda a: 15 - a),
    ("int i=15;i>=int'(a);i-=2", lambda a: (15 - a) // 2 + 1),
    ("int i=0;i<a[1:0];i++", lambda a: a % 4),
    ("int i=0;i<(a&3);i++", lambda a: a & 3),
    ("int i=0;i<(a+2);i++", lambda a: a + 2),
    ("int i=0;i<(a*2);i++", lambda a: a * 2),
    ("int i=0;i<(a[0]?3:0);i++", lambda a: 3 if a & 1 else 0),
    ("int i=0;i<4'(a+1);i++", lambda a: (a + 1) % 16),
    ("logic [4:0] i=0;i<a;i++", lambda a: a),
    ("logic [3:0] i=15;i>a;i--", lambda a: 15 - a),
    ("logic [3:0] i=8;i<12;i++", lambda a: 4),
    ("int i=0;i<4 && i<a;i++", lambda a: min(4, a)),
    ("int i=0;a!=0 && i<4;i++", lambda a: 4 if a else 0),
    ("int i=0;i<4 && (a!=0 && i<a);i++", lambda a: min(4, a)),
    ("int i=7;i>3 && i>int'(a);i--", lambda a: max(0, 7-max(3, a))),
])
def test_every_declared_goal_has_exact_local_answer_and_native_witness(
        tmp_path, record_property, header, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for({header}) begin
          q=q+1;
          if(a==7 && i==1) q=q;
          if(1'b0) q=99;
        end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("condition,nonzero", [
    ("i<3 && more(a)", 33),
    ("more(a) && i<3", 34),
])
def test_for_ranking_preserves_short_circuit_effects_and_final_test(
        tmp_path, record_property, condition, nonzero):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic bit more(input [3:0] v); q=q+1; return v!=0; endfunction
      always @(posedge clk) begin
        q=0;
        for(int i=0;{condition};i++) begin q=q+10; if(1'b0) q=99; end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: nonzero if a else 1)


def test_for_disjunction_is_not_a_termination_certificate(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for(int i=0;i<3 || a!=0;i++) q=q+1;
      end
    endmodule"""
    with pytest.raises(Refused, match="typed scalar ranking comparison"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("header,expected", [
    ("logic signed [4:0] i=-8;i<int'(a);i++", lambda a: a + 8),
    ("int i=0;i<(a&3);i++", lambda a: a & 3),
    ("int i=0;i<4'(a+1);i++", lambda a: max(0, a + 1) if a < 7 else 0),
    ("int i=0;i<(a&4'b1111);i++", lambda a: a % 16),
])
def test_signed_narrowing_and_contextual_conversion_keep_every_goal(
        tmp_path, record_property, header, expected):
    source = f"""module M(input clk,input signed [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        for({header}) begin
          q=q+1;
          if(a==-3 && i==1) q=q;
          if(1'b0) q=99;
        end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(-8, 8), expected)


def _assert_closure(tmp_path, record_property, source, inputs, expected, *, fresh_inputs=False):
    import json

    import z3

    from behave.sim.symbolic import typecheck as types

    def zero(kind):
        if isinstance(kind, types.Array):
            return [zero(kind.elem) for _ in range(kind.cells)]
        return False if kind == types.BOOLEAN else 0

    plan, lowered, names = _lower(tmp_path, source)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    declaration = manifest.as_dict()
    encoded = _encode(lowered)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    if fresh_inputs:
        # Stateful fixtures query the declared zero-state boundary. Each input
        # gets a fresh simulator process; compilation is reused, evidence isn't.
        samples = [replay([value]) for value in inputs]
        values = [value[0] for value, _ in samples]
        native_hits = [hits[0] for _, hits in samples]
    else:
        values, native_hits = replay(inputs)
    assert values == [expected(a) for a in inputs]
    by_occurrence = {occurrence: goal for _, occurrence, goal in lowered.occurrences}
    boundary = {name: zero(lowered.procedure.types[variable]) for name, variable in names.items()}
    for a, value, hits in zip(inputs, values, native_hits, strict=True):
        state = {**boundary, **({'a': a} if 'a' in names else {})}
        post, pending, occurrences = _run_activation(lowered, names, state, encoded)
        _commit_pending(lowered, post, pending)
        assert post[names["q"]] == value
        assert {by_occurrence[item] for item in occurrences} == hits

    # Query EVERY catalogue entry, including dead branches and zero-trip goals.
    # This is a finite local boundary, not a whole-design unreachability proof.
    assert set(by_occurrence.values()) == set(manifest)
    solver = z3.Solver()
    solver.add(encoded.constraint, *(term == cell for name, value in boundary.items() if name != 'a'
        for term, cell in _value_leaves(encoded.pre[names[name]], value)))
    if "a" in names:
        solver.add(encoded.pre[names["a"]] >= min(inputs), encoded.pre[names["a"]] <= max(inputs))
    sat = unsat = 0
    answers = []
    for goal in manifest:
        solver.push()
        solver.add(z3.Or(*(encoded.hits[mark] for mark, _, public in lowered.occurrences
                           if public == goal)))
        answer = solver.check()
        possible = any(goal in hits for hits in native_hits)
        assert answer == (z3.sat if possible else z3.unsat), goal
        row = {"goal": goal, "answer": str(answer)}
        if possible:
            # Constant activations need no symbolic input. Replay a member of
            # the same finite domain; do not add an artificial RTL dependency.
            witness = (solver.model().eval(encoded.pre[names["a"]], model_completion=True).as_signed_long()
                       if "a" in names else min(inputs))
            _, confirmed = replay([witness])
            assert goal in confirmed[0]
            row.update(witness=witness, native_hits=sorted(confirmed[0]))
            sat += 1
        else:
            unsat += 1
        answers.append(row)
        solver.pop()
    assert sat and unsat and sat + unsat == len(manifest)
    assert manifest.as_dict() == declaration
    (tmp_path / "goal_audit.json").write_text(json.dumps({
        "catalogue": declaration, "queries": answers,
        "observations": [{"input": a, "output": value, "hits": sorted(hits)}
                         for a, value, hits in zip(inputs, values, native_hits, strict=True)]}, indent=2))
    record_property("local_goal_closure", {"declared": len(manifest), "sat_replayed": sat,
                                           "local_unsat_retained": unsat})


def test_literal_disabled_helper_copies_retain_every_goal_query(tmp_path, record_property):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        if(v==17) return 19;
        if(v==9) return 13; return v;
      endfunction
      always @(posedge clk) q<=f(0)+f(0)+f(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: 13 if a == 9 else a)


@pytest.mark.parametrize("signed,header,body,expected", [
    ("signed", "int i=0;i<a;i++", "q=q+1;", lambda a: max(0, a)),
    ("signed", "int i=-8;i<a;i++", "q=q+1;", lambda a: a + 8),
    ("", "int i=0;i<b;i++", "q=q+1; b=b-1;", lambda a: (a + 1) // 2),
])
def test_runtime_tests_are_not_replaced_by_the_maximum(tmp_path, signed, header, body, expected):
    source = f"""module M(input clk,input {signed} [3:0] a,output reg [7:0] q);
      reg [3:0] b;
      always @(posedge clk) begin q=0; b=a; for({header}) begin {body} end end
    endmodule"""
    _, lowered, names = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in (range(-8, 8) if signed else range(16)):
        state = {name: (a if name == "a" else 0) for name in names}
        post, _, _ = _run_activation(lowered, names, state, encoded)
        assert post[names["q"]] == expected(a)


@pytest.mark.parametrize("header,body", [
    ("int i=0;i<a;i++", "i=i-1;"),
    ("int i=0;i<a;i--", "q=q+1;"),
    ("int i=15;i>=a;i-=2", "q=q+1;"),
    ("int i=0;i<(a+i++);i++", "q=q+1;"),
    ("logic [3:0] i=0;i<=a;i++", "q=q+1;"),  # may wrap at 15
    ("logic [3:0] i=15;i>=a;i--", "q=q+1;"),  # may underflow at 0
    ("logic signed [3:0] i=0;i<a;i++", "q=q+1;"),
    ("logic [3:0] i=14;i<15;i+=2", "q=q+1;"),
])
def test_unproved_mutation_direction_and_unsigned_conversion_remain_refused(tmp_path, header, body):
    source = f"module M(input clk,input [3:0] a,output reg [7:0] q); always @(posedge clk) for({header}) begin {body} end endmodule"
    with pytest.raises(Refused, match="rtl-source-ir-(loop-domain-unproved|operation-unsupported)"):
        _lower(tmp_path, source)


@pytest.mark.parametrize("assignment", [False, True])
def test_runtime_loop_does_not_assume_a_helper_return_is_initialized(tmp_path, assignment):
    initialization = "f=0;" if assignment else ""
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        {initialization}
        for(int i=0;i<v;i++) begin
          if(i==2) return 9;
          f=i+1;
        end
      endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    if not assignment:
        with pytest.raises(Refused, match="return-undefined"):
            _lower(tmp_path, source)
        return
    _, lowered, names = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        state = {name: (a if name == "a" else 0) for name in names}
        post, _, occurrences = _run_activation(lowered, names, state, encoded)
        assert post[names["q"]] == (a if a < 3 else 9)
        assert any(hit.role == "zero" for hit in occurrences) == (a == 0)


@pytest.mark.parametrize("declaration,header", [
    ("typedef logic [4:0] count_t;", "count_t i=0;i<a;i++"),
    ("logic [4:0] i;", "i=0;i<a;i++"),
    ("", "int unsigned i=0;i<a;i++"),
])
def test_counter_types_come_from_compiler_binding_not_keyword_spelling(tmp_path, declaration, header):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration}
      always @(posedge clk) begin q=0; for({header}) q=q+1; end
    endmodule"""
    _, lowered, names = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        state = {name: (a if name == "a" else 0) for name in names}
        post, _, _ = _run_activation(lowered, names, state, encoded)
        assert post[names["q"]] == a
