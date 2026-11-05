"""Operation declarations, native hits and exact predicates have one meaning."""

from dataclasses import replace
from itertools import product

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.behavior.source_operations import SourceOperationGoal
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.contract import FiniteType
from behave.sim.core.observation_goal import GoalCondition as C
from behave.sim.core.observation_goal import GoalField as F
from behave.sim.core.operation_goal import OperationGoal, OperationValue
from behave.sim.core.value_type import ValueType


def _values(count=2):
    return tuple(OperationValue(f"operand{i}") for i in range(count)) + (OperationValue("result"),)


def _plan(source, condition, *, kind="Add"):
    plan = plan_source(source_model(source), source)
    site, = (site for site in plan.operations if site.operator == kind)
    count = 1 if kind in ("UAdd", "USub", "Invert", "Not") else 2
    binding = SourceOperationGoal(site, OperationGoal("target", condition).bind(_values(count)))
    return plan.with_operation_goals((binding,)), binding


def test_native_bound_catalogue_keeps_structural_hits_and_operand_types():
    source = """class M:
 def process(self, a, b):
  return {'y': (a + b) if a else 0}
"""
    plan, binding = _plan(source, C("and", C("eq", F("result"), 17), C("lt", F("operand0"), 10)))
    plain, _, old_hits = instrumented.load_source(source)
    native, _, hits = instrumented.load_source(source, goal_plan=plan)
    for a, b in ((0, 17), (2, 15), (12, 5), (2, 14), (2, 15)):
        hits.clear()
        old_hits.clear()
        assert native["M"]().process(a, b) == plain["M"]().process(a, b)
        assert [goal for goal in hits if goal != binding.id] == old_hits
        assert hits.count(binding.id) == int(a != 0 and a < 10 and a + b == 17)
    goal = plan.manifest[binding.id]
    assert goal.family == "operation-event" and goal.cls == "coverage"
    assert OperationGoal.from_dict(binding.predicate.rule.as_dict()) == binding.predicate.rule
    assert not plan_source(source_model(source), source).operation_goals


def test_native_failure_has_no_operation_completion_hit():
    source = "class M:\n def process(self, a, b): return {'y': a // b}\n"
    plan, binding = _plan(source, C("eq", F("operand1"), 0), kind="FloorDiv")
    native, _, hits = instrumented.load_source(source, goal_plan=plan)
    with pytest.raises(ZeroDivisionError):
        native["M"]().process(7, 0)
    assert binding.id not in hits


def test_types_history_and_undeclared_flags_are_rejected_at_binding():
    with pytest.raises(ValueError, match="previous"):
        OperationGoal("x", C("eq", F("result", previous=True), 0))
    with pytest.raises(ValueError, match="does not declare"):
        OperationGoal("x", C("eq", F("overflow"), True)).bind(_values())
    with pytest.raises(TypeError, match="matching types"):
        OperationGoal("x", C("eq", F("result"), True)).bind(_values())
    with pytest.raises(ValueError, match="in-range"):
        OperationGoal("x", C("eq", F("result", bit=8), True)).bind((OperationValue("result", width=8),))
    source = "class M:\n def process(self, a, b): return {'y': a + b}\n"
    plan, binding = _plan(source, C("eq", F("result"), 0))
    bare = plan_source(source_model(source), source)
    with pytest.raises(ValueError, match="outside"):
        bare.with_operation_goals((replace(binding, site=replace(binding.site, line=100)),))
    with pytest.raises(ValueError, match="once"):
        plan.with_operation_goals((binding,))


@pytest.mark.solver
def test_finite_signed_unsigned_and_wide_literals_match_native_predicate():
    import z3

    values = (OperationValue("operand0", width=4, signed=True),
              OperationValue("operand1", width=4, signed=False),
              OperationValue("result", width=130, signed=False),
              OperationValue("flag", "boolean"))
    conditions = (C("lt", F("operand0"), F("operand1")),
                  C("eq", F("result"), 1 << 100), C("gt", F("result"), -1),
                  C("eq", F("result", bit=100), True),
                  C("and", C("ne", F("operand1"), 0), C("eq", F("flag"), True)))
    for condition in conditions:
        bound = OperationGoal("x", condition).bind(values)
        for a, b, result, flag in product((-8, -1, 0, 7), (0, 8, 15), (0, 1 << 100), (False, True)):
            native = (a, b, result, flag)
            symbolic = (z3.BitVecVal(a, 4), z3.BitVecVal(b, 4), z3.BitVecVal(result, 130), z3.BoolVal(flag))
            assert z3.is_true(z3.simplify(bound.symbolic_hit(True, symbolic))) == bound.observe(native)
            assert z3.is_false(z3.simplify(bound.symbolic_hit(False, symbolic)))
    assert OperationGoal("x", C("or", C("eq", F("operand0"), 1), C("eq", F("result"), 0))).bind(
        values).observe((None, 0, 0, False)) is None


@pytest.mark.solver
def test_source_lowering_query_finds_native_confirmed_residual_operation():
    import z3

    from behave.sim.symbolic import encoder, interp, ranges
    from behave.sim.symbolic import plan as planning
    from behave.sim.behavior.construction import ConstructionFacts
    from behave.sim.behavior.lowering import lower_source

    source = """class M:
 def process(self, a, b):
  if a != 0:
   return {'y': a + b}
  return {'y': 0}
"""
    plan, binding = _plan(source, C("and", C("eq", F("result"), 31337),
                                   C("gt", F("operand0"), 0)))
    lowered = lower_source(source, input_widths=[16, 16],
                           operation_goals=plan.operation_goals,
                           construction=ConstructionFacts((), (), (), ()))
    assert lowered.catalogue == plan.manifest
    native, _, hits = instrumented.load_source(source, goal_plan=plan)
    baseline = [(i, i) for i in range(100)]
    for a, b in baseline:
        native["M"]().process(a, b)
    assert binding.id not in hits
    env = {**lowered.proc.state_ranges, "a": ranges.Range(0, 65535), "b": ranges.Range(0, 65535)}
    prepared = planning.plan_encoding(lowered.proc, env)
    assert prepared.complete, prepared.unresolved
    encoded = encoder.encode_goal_query(lowered.proc, (binding.id,), env=env, plan=prepared)
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.hits[binding.id])
    assert solver.check() == z3.sat
    model = solver.model()
    found = tuple(model.eval(encoded.pre[name], model_completion=True).as_long() for name in ("a", "b"))
    hits.clear()
    expected = {"y": sum(found)}
    assert native["M"]().process(*found) == expected == {"y": 31337}
    assert binding.id in hits
    _, output, ir_hits, failure = interp.run_step(lowered.proc, dict(zip(("a", "b"), found)))
    assert not failure and output.transactions == [expected] and binding.id in ir_hits
    # Matching operand values alone do not make an unexecuted addition a hit.
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.pre["a"] == 0, encoded.pre["b"] == 31337,
               encoded.hits[binding.id])
    assert solver.check() == z3.unsat


@pytest.mark.solver
@pytest.mark.parametrize("body,operator,kinds", [
    ("return {'y': a // b}", "FloorDiv", ("integer",) * 3),
    ("return {'y': a << b}", "LShift", ("integer",) * 3),
    ("return {'y': (a > 0) + b}", "Add", ("boolean", "integer", "integer")),
    ("return {'y': (a > 0) & (b > 0)}", "BitAnd", ("boolean",) * 3),
    ("return {'y': -a}", "USub", ("integer",) * 2),
    ("return {'y': not a}", "Not", ("integer", "boolean")),
    ("return {'y': not (a > b)}", "Not", ("boolean",) * 2),
    ("if False:\n   return {'y': a + b}\n  return {'y': 0}", "Add", ("integer",) * 3),
])
def test_typed_operation_marks_match_source_and_queries(body, operator, kinds):
    import z3

    from behave.sim.symbolic import encoder, interp, ranges
    from behave.sim.symbolic import plan as planning
    from behave.sim.behavior.construction import ConstructionFacts
    from behave.sim.behavior.lowering import lower_source

    source = "class M:\n def process(self, a, b):\n  " + body + "\n"
    bare = plan_source(source_model(source), source)
    site, = (site for site in bare.operations if site.operator == operator)
    values = tuple(OperationValue(name, kind) for name, kind in zip(
        [f"operand{i}" for i in range(len(kinds) - 1)] + ["result"], kinds))
    condition = C("eq", F("result"), True if kinds[-1] == "boolean" else 0)
    binding = SourceOperationGoal(site, OperationGoal("zero-or-true", condition).bind(values))
    plan = bare.with_operation_goals((binding,))
    signature = (ProcedureSignature((("a", FiniteType(3)), ("b", FiniteType(3))),
                                    (("y", ValueType.boolean()),)) if kinds[-1] == "boolean" else None)
    lowered = lower_source(source, input_widths=[3, 3], operation_goals=(binding,), signature=signature,
                           construction=ConstructionFacts((), (), (), ()))
    assert lowered.catalogue == plan.manifest
    native, _, hits = instrumented.load_source(source, goal_plan=plan)
    original, _, old_hits = instrumented.load_source(source)
    env = {**lowered.proc.state_ranges, "a": ranges.Range(0, 7), "b": ranges.Range(0, 7)}
    prepared = planning.plan_encoding(lowered.proc, env)
    assert prepared.complete, prepared.unresolved
    encoded = encoder.encode_step(lowered.proc, env=env, plan=prepared)
    for a, b in product(range(3), repeat=2):
        hits.clear()
        old_hits.clear()
        if operator == "FloorDiv" and b == 0:
            with pytest.raises(ZeroDivisionError):
                native["M"]().process(a, b)
            assert binding.id not in hits
        else:
            assert native["M"]().process(a, b) == original["M"]().process(a, b)
            assert [goal for goal in hits if goal != binding.id] == old_hits
        _, _, ir_hits, _ = interp.run_step(lowered.proc, {"a": a, "b": b})
        assert (binding.id in ir_hits) == (binding.id in hits)
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == a, encoded.pre["b"] == b)
        assert solver.check() == z3.sat
        assert z3.is_true(solver.model().eval(encoded.hits[binding.id], model_completion=True)) == (binding.id in hits)


@pytest.mark.eda("iverilog", "vvp")
@pytest.mark.solver
def test_rtl_bound_predicates_preserve_output_and_confirm_solved_input(tmp_path):
    import subprocess

    import z3

    from behave.sim.rtl.source_operation_events import CaptureType, OperationCapture

    kind = CaptureType(8, False)
    capture = OperationCapture("+", (kind, kind), kind)
    rules = (OperationGoal("sum137", C("eq", F("result"), 137)),
             OperationGoal("unsigned", C("gt", F("result"), -1)),
             OperationGoal("too-wide", C("eq", F("result"), 1 << 100)),
             OperationGoal("bit7", C("eq", F("result", bit=7), True)),
             OperationGoal("order", C("lt", F("operand0"), F("operand1"))))
    bound = rules[0].bind(capture.goal_values())
    a, b = z3.BitVecs("a b", 8)
    solver = z3.Solver()
    solver.add(bound.symbolic_hit(True, (a, b, a + b)), z3.UGT(a, 0))
    assert solver.check() == z3.sat
    found = tuple(solver.model().eval(value, model_completion=True).as_long() for value in (a, b))
    samples = [(i, i) for i in range(100)] + [found, (255, 1), (0, 137)]
    callback = capture.predicate_callback("observe", tuple(enumerate(rules)), hit="hit", unreadable="unknown")
    source = """module M;
    logic [7:0] a,b,q,plain;
    int sample;
    function automatic void hit(input int id, input int unsigned scope);
      $display("hit %0d %0d %0d", sample,id,scope);
    endfunction
    function automatic void unknown(input int id, input int unsigned scope);
      $display("unknown %0d %0d %0d", sample,id,scope);
    endfunction
    """ + callback + capture.function("compute", "observe") + "\ninitial begin\n"
    for i, (left, right) in enumerate(samples):
        source += f"sample={i}; a={left}; b={right}; plain=a+b; q=compute(a,b,37); "
        source += 'if(q !== plain) $fatal(1,"output changed");\n'
    source += f"sample={len(samples)}; a=8'bx0000000; b=1; q=compute(a,b,37);\n"
    source += "$finish; end endmodule\n"
    (tmp_path / "design.sv").write_text(source)
    subprocess.run(["iverilog", "-g2012", "-s", "M", "-o", "sim", "design.sv"],
                   cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)
    run = subprocess.run(["vvp", "sim"], cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)
    hits, unknown = set(), set()
    for line in run.stdout.splitlines():
        if not line.startswith(("hit ", "unknown ")):
            continue
        kind, sample, goal, scope = line.split()
        assert scope == "37"
        (hits if kind == "hit" else unknown).add((int(sample), int(goal)))
    expected = {(i, j) for i, (a, b) in enumerate(samples) for j, rule in enumerate(rules)
                if rule.bind(capture.goal_values()).observe((a, b, (a + b) & 255))}
    assert hits == expected
    assert all((i, 0) not in hits for i in range(100))
    assert (100, 0) in hits
    assert unknown == {(len(samples), j) for j in range(len(rules))}


@pytest.mark.solver
@pytest.mark.parametrize("source", [
    """class M:
 def process(self, a, b):
  y = 0
  for i in range(a):
   y = y + b
  return {'y': y}
""",
    """class M:
 def helper(self, a, b): return a + b
 def process(self, a, b):
  return {'y': self.helper(a, b) if a else 0}
""",
    """class M:
 def helper(self, a, b): return a + b
 def process(self, a, b):
  if False:
   return {'y': self.helper(a, b)}
  return {'y': 0}
""",
    """class M:
 def process(self, a, b):
  return {'y': (a + b) * (a - b)}
""",
])
def test_nested_loop_and_helper_bindings_have_exact_native_ir_hit_sets(source):
    from behave.sim.symbolic import interp
    from behave.sim.behavior.construction import ConstructionFacts
    from behave.sim.behavior.lowering import _Lowerer, lower_source

    bare = plan_source(source_model(source), source)
    bindings = tuple(SourceOperationGoal(site, OperationGoal("three", C("eq", F("result"), 3)).bind(_values()))
                     for site in bare.operations)
    prepared = bare.with_operation_goals(bindings)
    native, _, hits = instrumented.load_source(source, goal_plan=prepared)
    original, _, old_hits = instrumented.load_source(source)
    goals = {binding.id for binding in bindings}
    # Both CFG and term frontends bind the same operations; independent native
    # execution remains the oracle because their expression lowering is shared.
    for lowerer in (None, _Lowerer):
        lowered = lower_source(source, input_widths=[2, 2], lowerer=lowerer,
                               operation_goals=bindings, construction=ConstructionFacts((), (), (), ()))
        assert lowered.catalogue == prepared.manifest
        for a, b in product(range(4), repeat=2):
            hits.clear()
            old_hits.clear()
            expected = original["M"]().process(a, b)
            assert native["M"]().process(a, b) == expected
            assert [goal for goal in hits if goal not in goals] == old_hits
            _, output, ir_hits, error = interp.run_step(lowered.proc, {"a": a, "b": b})
            assert not error and output.transactions == [expected]
            assert set(hits) == ir_hits


@pytest.mark.solver
def test_operation_operand_precedes_later_helper_write_and_preserves_boolean_return():
    from behave.sim.symbolic import interp
    from behave.sim.behavior.construction import ConstructionFacts
    from behave.sim.behavior.lowering import lower_source

    for boolean in (False, True):
        source = f"""class M:
 def __init__(self): self.x = 1
 def bump(self, b):
  self.x = b
  return {"True" if boolean else "2"}
 def process(self, a, b):
  return {{'y': self.x + self.bump(b)}}
"""
        bare = plan_source(source_model(source), source)
        site, = bare.operations
        values = (OperationValue("operand0"), OperationValue("operand1", "boolean" if boolean else "integer"),
                  OperationValue("result"))
        condition = C("and", C("eq", F("operand0"), 1), C("eq", F("operand1"), True if boolean else 2),
                      C("eq", F("result"), 2 if boolean else 3))
        binding = SourceOperationGoal(site, OperationGoal("old-value", condition).bind(values))
        plan = bare.with_operation_goals((binding,))
        native, _, hits = instrumented.load_source(source, goal_plan=plan)
        lowered = lower_source(source, input_widths=[3, 3], operation_goals=(binding,),
                               construction=ConstructionFacts((("x", ("int", 1)),), (), (), ()))
        for b in (0, 5, 7):
            hits.clear()
            expected = native["M"]().process(0, b)
            state, output, ir_hits, error = interp.run_step(lowered.proc, {"a": 0, "b": b}, state={"x": 1})
            assert not error and output.transactions == [expected] and state["x"] == b
            assert binding.id in hits and set(hits) == ir_hits


@pytest.mark.eda("iverilog", "vvp")
def test_rtl_predicate_compares_wide_and_mixed_signed_values_without_truncation(tmp_path):
    import subprocess

    from behave.sim.rtl.source_operation_events import CaptureType, OperationCapture

    # Predicate-backend oracle, separate from the compiler-driven capture
    # oracle. These are already-captured values, not another arithmetic model.
    capture = OperationCapture("+", (CaptureType(5, True), CaptureType(8, False)), CaptureType(130, False))
    rules = (OperationGoal("mixed", C("lt", F("operand0"), F("operand1"))),
             OperationGoal("wide", C("gt", F("result"), 1 << 100)),
             OperationGoal("negative", C("lt", F("operand0"), -8)),
             OperationGoal("bit", C("eq", F("result", bit=128), True)))
    samples = tuple(product((-16, -1, 0, 15), (0, 128, 255), (0, 1 << 100, (1 << 129) - 1)))
    source = """module M;
    int sample;
    function automatic void hit(input int id, input int unsigned scope);
      $display("hit %0d %0d", sample,id);
    endfunction
    function automatic void unknown(input int id, input int unsigned scope);
      $fatal(1,"known samples became unreadable");
    endfunction
    """ + capture.predicate_callback("observe", tuple(enumerate(rules)), hit="hit", unreadable="unknown")
    source += "initial begin\n"
    for i, (a, b, result) in enumerate(samples):
        source += f"sample={i}; observe({a}, 8'd{b}, 130'd{result}, 0);\n"
    source += "$finish; end endmodule\n"
    (tmp_path / "design.sv").write_text(source)
    subprocess.run(["iverilog", "-g2012", "-s", "M", "-o", "sim", "design.sv"],
                   cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)
    run = subprocess.run(["vvp", "sim"], cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)
    observed = {tuple(map(int, line.split()[1:])) for line in run.stdout.splitlines() if line.startswith("hit ")}
    expected = {(i, j) for i, sample in enumerate(samples) for j, rule in enumerate(rules)
                if rule.bind(capture.goal_values()).observe(sample)}
    assert observed == expected
