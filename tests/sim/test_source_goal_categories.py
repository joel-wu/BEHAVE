"""Small source programs with independently specified per-step goal outcomes.

These check automatic source categories, not the extension schema registry or
whole-design search. The two languages share observations, not source IDs.
"""

import subprocess
import sys
from dataclasses import dataclass, replace

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.goal_provider import goal_providers
from behave.sim.behavior.isolated import isolated_model
from behave.sim.behavior.source_plan import plan_source as behavior_plan
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_plan import plan_source as rtl_plan

from .test_rtl_source_locations import _observation_documents, _prepare_clocked_replay

BEHAVIOR = """class M:
    def reset(self):
        pass

    def helper(self, v):
        if v & 1:
            return 9
        return 4

    def unused(self, v):
        return 99

    def process(self, a):
        q = 0
        if (a & 1) and (a & 2):
            q = 10
        for i in range(a & 3):
            q = q + 1
        if a & 4:
            q = q + self.helper(a)
        if False:
            q = 99
        return {"q": q}
"""

RTL = """module M(input clk, input [3:0] a, output reg [7:0] q);
  function automatic [7:0] helper(input [3:0] v);
    if(v & 1) return 9;
    return 4;
  endfunction
  function automatic [7:0] unused(input [3:0] v); return 99; endfunction
  always @(posedge clk) begin
    q=0;
    if((a & 1) && (a & 2)) q=10;
    for(int i=0; i<(a & 3); i++) q=q+1;
    if(a & 4) q=q+helper(a);
    if(1'b0) q=99;
  end
endmodule
"""

# Include repeat inputs and miss-after-hit, rather than checking only a union.
VALUES = (0, 3, 1, 7, 2, 6, 4, 5, 0, 0)


def test_native_category_oracles_do_not_require_the_solver_package():
    code = ("import sys; sys.modules['z3'] = None; "
            "import tests.sim.test_source_goal_categories")
    subprocess.run([sys.executable, "-c", code], check=True)


@dataclass(frozen=True)
class Check:
    schema: str
    behavior: str
    rtl: str
    event: str
    hits: frozenset[int]


CHECKS = (
    Check("location", "q = 10", "q=10;", "entry", frozenset({3, 7})),
    Check("location", "q = 99", "q=99;", "entry", frozenset()),
    Check("control-choice", "(a & 1) and (a & 2)", "if((a & 1) && (a & 2)) q=10;", "true", frozenset({3, 7})),
    Check("control-choice", "(a & 1) and (a & 2)", "if((a & 1) && (a & 2)) q=10;", "false", frozenset({0, 1, 2, 4, 5, 6})),
    Check("boolean-expression", "a & 1", "a & 1", "true", frozenset({1, 3, 5, 7})),
    Check("boolean-expression", "a & 1", "a & 1", "false", frozenset({0, 2, 4, 6})),
    Check("boolean-expression", "a & 2", "a & 2", "true", frozenset({3, 7})),
    Check("boolean-expression", "a & 2", "a & 2", "false", frozenset({1, 5})),
    Check("loop-activation", "range(a & 3)", "for(int i=0; i<(a & 3); i++) q=q+1;", "zero", frozenset({0, 4})),
    Check("loop-activation", "range(a & 3)", "for(int i=0; i<(a & 3); i++) q=q+1;", "nonzero", frozenset({1, 2, 3, 5, 6, 7})),
    Check("control-choice", "v & 1", "if(v & 1) return 9;", "true", frozenset({5, 7})),
    Check("control-choice", "v & 1", "if(v & 1) return 9;", "false", frozenset({4, 6})),
    Check("location", "return 9", "return 9;", "entry", frozenset({5, 7})),
    Check("location", "return 4", "return 4;", "entry", frozenset({4, 6})),
    Check("control-choice", "False", "if(1'b0) q=99;", "true", frozenset()),
    Check("control-choice", "False", "if(1'b0) q=99;", "false", frozenset(range(8))),
)


def _scenario(conjunction):
    if conjunction:
        return BEHAVIOR, RTL, CHECKS
    overrides = {
        ("location", "q = 10", "entry"): {1, 2, 3, 5, 6, 7},
        ("control-choice", "(a & 1) and (a & 2)", "true"): {1, 2, 3, 5, 6, 7},
        ("control-choice", "(a & 1) and (a & 2)", "false"): {0, 4},
        ("boolean-expression", "a & 2", "true"): {2, 6},
        ("boolean-expression", "a & 2", "false"): {0, 4},
    }
    return (BEHAVIOR.replace(" and ", " or "), RTL.replace("&&", "||"), tuple(
        replace(check, behavior=check.behavior.replace(" and ", " or "),
                rtl=check.rtl.replace("&&", "||"), hits=frozenset(overrides.get(
                    (check.schema, check.behavior, check.event), check.hits)))
        for check in CHECKS))


def _expected(a, conjunction):
    selected = a % 4 == 3 if conjunction else a % 4 != 0
    return (10 if selected else 0) + a % 4 + (
        (9 if a % 2 else 4) if a >= 4 else 0)


def _behavior_id(source, plan, check):
    # Match exact fixture coordinates; provider registration owns the schema.
    start = source.index(check.behavior)
    line = source.count("\n", 0, start) + 1
    col = start - source.rfind("\n", 0, start) - 1
    event = {"true": "T", "false": "F", "nonzero": "T", "zero": "F"}.get(check.event, check.event)
    schemas = {goal.id: provider.schema.name for provider in goal_providers(plan.manifest)
               for goal in provider.catalogue.values()}
    goal, = [goal.id for goal in plan.manifest.values()
             if schemas[goal.id] == check.schema and goal.event == event
             and goal.where("line") == line and goal.where("col") == col]
    return goal


def _rtl_id(source, manifest, check):
    matches = [goal.id for goal in manifest.values()
               if goal.family == check.schema and goal.event == check.event
               and source[goal.where("start"):goal.where("end")] == check.rtl]
    assert len(matches) == 1, (check, [source[goal.where("start"):goal.where("end")]
                                     for goal in manifest.values() if goal.family == check.schema])
    return matches[0]


def _assert_rows(goals, checks, observations, manifest, conjunction):
    assert len(observations) == len(VALUES)
    assert set(goals) <= manifest.keys()
    for a, (output, hits) in zip(VALUES, observations, strict=True):
        assert output == _expected(a, conjunction)
        assert hits <= manifest.keys()
        for goal, check in zip(goals, checks, strict=True):
            assert (goal in hits) == (a in check.hits), (a, check, goal)


def _behavior_trace(source):
    plan = behavior_plan(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    original = {}
    exec(compile(source, "behavior.py", "exec"), original)  # noqa: S102 - fixed oracle
    plain, native = original["M"](), namespace["M"]()
    observations = []
    for a in VALUES:
        hits.clear()
        output = native.process(a)
        assert output == plain.process(a)
        observations.append((output["q"], frozenset(hits)))
    return plan, observations


@pytest.mark.parametrize("conjunction", [True, False], ids=["and", "or"])
def test_behavior_source_categories_have_positive_negative_and_unevaluated_cases(conjunction):
    source, _, checks = _scenario(conjunction)
    plan, observations = _behavior_trace(source)
    _assert_rows([_behavior_id(source, plan, check) for check in checks], checks,
                 observations, plan.manifest, conjunction)
    unused = source[:source.index("return 99")].count("\n") + 1
    assert not any(goal.where("line") == unused for goal in plan.manifest.values())


@pytest.mark.solver
@pytest.mark.parametrize("conjunction", [True, False], ids=["and", "or"])
def test_behavior_native_and_lowered_categories_agree_per_step(conjunction):
    from behave.sim.symbolic import interp
    from behave.sim.behavior.instance import lower

    source, _, _ = _scenario(conjunction)
    plan, observations = _behavior_trace(source)
    schema = TransactionSchema([Endpoint("a", "input", 4), Endpoint("q", "output", 8)],
                               authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    for a, (value, hits) in zip(VALUES, observations, strict=True):
        _, output, actual, error = interp.run_step(exact.proc, {"a": a})
        assert not error
        assert output.buffers["q"] == [value]
        assert actual == hits


@pytest.mark.solver
@pytest.mark.parametrize("conjunction", [True, False], ids=["and", "or"])
def test_every_behavior_goal_has_local_answer_and_python_witness(conjunction, record_property):
    import z3

    from behave.sim.symbolic import encoder
    from behave.sim.behavior.instance import lower

    source, _, _ = _scenario(conjunction)
    plan = behavior_plan(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=plan)
    model = namespace["M"]()
    schema = TransactionSchema([Endpoint("a", "input", 4), Endpoint("q", "output", 8)],
                               authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    encoded = encoder.encode_step(exact.proc, env=exact.env)
    oracle = []
    for a in range(16):
        native_hits.clear()
        model.process(a)
        oracle.append(frozenset(native_hits))
    assert set(plan.manifest) <= encoded.hits.keys()
    solver = z3.Solver()
    solver.add(encoded.constraint, z3.UGE(encoded.pre["a"], 0), z3.ULE(encoded.pre["a"], 15))
    sat = unsat = 0
    for goal in plan.manifest:
        solver.push()
        solver.add(encoded.hits[goal])
        possible = any(goal in hits for hits in oracle)
        assert solver.check() == (z3.sat if possible else z3.unsat), goal
        if possible:
            value = solver.model().eval(encoded.pre["a"]).as_long()
            native_hits.clear()
            model.process(value)
            assert goal in native_hits
            sat += 1
        else:
            unsat += 1
        solver.pop()
    assert sat and unsat and sat + unsat == len(plan.manifest)
    record_property("local_goal_closure", {"declared": len(plan.manifest), "sat_replayed": sat,
                                           "local_unsat_retained": unsat})


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("conjunction", [True, False], ids=["and", "or"])
def test_rtl_native_categories_have_positive_negative_and_unevaluated_cases(tmp_path, conjunction):
    _, source, checks = _scenario(conjunction)
    cst, ast = _observation_documents(tmp_path, source)
    plan = rtl_plan(source, cst, semantic_document=ast, enumerate_iterations=False)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    digest = manifest.digest()
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values, hits = replay(VALUES)
    _assert_rows([_rtl_id(source, manifest, check) for check in checks], checks,
                 list(zip(values, hits)), manifest, conjunction)
    assert not any("return 99" in source[goal.where("start"):goal.where("end")]
                   for goal in manifest.values())
    assert manifest.digest() == digest  # Neither hits nor misses change membership.


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("expression,kind,error_type,ir_error,bad,expected", [
    ("12 // (a & 3)", "divzero", ZeroDivisionError, "DivZero", {4}, {5: 12, 6: 6, 7: 4}),
    ("[7, 8][a & 3]", "indexerror", IndexError, "IndexError", {6, 7}, {4: 7, 5: 8}),
    ("1 << ((a & 3) - 1)", "negativeshift", ValueError, "NegativeShift", {4}, {5: 1, 6: 2, 7: 4}),
])
def test_behavior_failure_requires_executing_the_partial_operation(
        expression, kind, error_type, ir_error, bad, expected):
    from behave.sim.symbolic import interp
    from behave.sim.behavior.instance import lower

    source = f"""class M:
    def reset(self):
        pass
    def process(self, a):
        q = 0
        if a & 4:
            q = {expression}
        return {{"q": q}}
"""
    plan = behavior_plan(source_model(source), source)
    target, = [goal.id for goal in plan.manifest.values() if goal.family == kind]
    schema = TransactionSchema([Endpoint("a", "input", 4), Endpoint("q", "output", 8)],
                               authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    original = {}
    exec(compile(source, "behavior.py", "exec"), original)  # noqa: S102 - fixed oracle
    plain = original["M"]()
    factory = isolated_model(source)
    try:
        native = factory()
        for a in (0, 4, 5, 6, 7, 0):
            # A source fault ends this run; the next independent run starts reset.
            native.reset()
            factory.hits.clear()
            if a in bad:
                with pytest.raises(error_type):
                    plain.process(a)
                with pytest.raises(error_type):
                    native.process(a=a)
            else:
                assert plain.process(a) == native.process(a=a) == {"q": expected.get(a, 0)}
            _, output, hits, error = interp.run_step(exact.proc, {"a": a})
            assert error == (ir_error if a in bad else "")
            assert hits == set(factory.hits)
            assert (target in hits) == (a in bad)
            assert {goal for goal in hits if plan.manifest[goal].cls == "safety"} == (
                {target} if a in bad else set())
            if not error:
                assert output.buffers["q"] == [expected.get(a, 0)]
    finally:
        factory.close()


@pytest.mark.eda("slang", "verilator", "make")
def test_rtl_assertion_failure_is_an_executed_event_not_a_false_snapshot(tmp_path):
    source = """module M(input clk, input [3:0] a, output reg [7:0] q);
      always @(posedge clk) begin
        q=a;
        if(a & 4) assert(a & 3) else q=99;
        q=q+1;
      end
    endmodule"""
    cst, ast = _observation_documents(tmp_path, source)
    plan = rtl_plan(source, cst, semantic_document=ast, enumerate_iterations=False)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    failure, = [goal.id for goal in manifest.values() if goal.family == "artifact-failure"]
    action, = [goal.id for goal in manifest.values() if goal.family == "location"
               and source[goal.where("start"):goal.where("end")] == "q=99;"]
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values = (0, 4, 5, 0, 4)
    outputs, observed = replay(values)
    for a, output, hits in zip(values, outputs, observed, strict=True):
        assert output == (100 if a == 4 else a+1)
        assert (failure in hits) == (action in hits) == (a == 4)
        assert {goal for goal in hits if manifest[goal].cls == "safety"} == (
            {failure} if a == 4 else set())


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("default", [False, True])
def test_rtl_multiway_choices_retain_default_and_no_match(tmp_path, default):
    source = """module M(input clk, input [3:0] a, output reg [7:0] q);
      always @(posedge clk) begin
        q=0;
        case(a)
          0,1: q=10;
          2: q=20;
          DEFAULT
        endcase
      end
    endmodule""".replace("DEFAULT", "default:q=30;" if default else "")
    cst, ast = _observation_documents(tmp_path, source)
    plan = rtl_plan(source, cst, semantic_document=ast, enumerate_iterations=False)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    choices = {goal.id: goal.event for goal in manifest.values() if goal.family == "control-choice"}
    assert set(choices.values()) == {"arm_0", "arm_1", "default" if default else "no-match"}
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values = (0, 1, 2, 3, 0, 3)
    outputs, observed = replay(values)
    for a, output, hits in zip(values, outputs, observed, strict=True):
        expected = "arm_0" if a < 2 else "arm_1" if a == 2 else "default" if default else "no-match"
        assert {choices[goal] for goal in hits & choices.keys()} == {expected}
        assert output == (10 if a < 2 else 20 if a == 2 else 30 if default else 0)
