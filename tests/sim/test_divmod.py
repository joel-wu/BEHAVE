"""One Python divmod occurrence across catalogue, native, IR and queries."""

import ast
import itertools

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import encoder, interp, ranges
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.source_values import (
    SCALAR, UNKNOWN, ValueContract, contract_of_expression,
)
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
from behave.sim.engines.local_ir import local_hit_any
from behave.sim.engines.probing import Outcome

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver


def _source(body, *, prefix="", helpers=""):
    return (prefix + "class M:\n    def reset(self):\n        self.n = 3\n"
            + helpers + "    def process(self, a, b):\n" + body)


SOURCES = [
    _source("""        q, r = divmod(a - 2, b - 2)
        return {'y': (q * 5 + r) & 255}
"""),
    _source("""        pair = divmod(2 - a, 2 - b)
        return {'y': (pair[0] * 5 + pair[1] + len(pair)) & 255}
"""),
    _source("""        q, r = divmod(a < 2, b == 2)
        return {'y': q * 5 + r}
"""),
    _source("""        q, r = divmod(divmod(a - 2, b - 2)[0], a - 1)
        return {'y': (q * 5 + r) & 255}
"""),
    _source("""        q, r = divmod(self.n, self.bump())
        return {'y': q * 5 + r + self.n}
""", helpers="""    def bump(self):
        self.n = (self.n + 1) & 7
        return self.n
"""),
    _source("""        q, r = divmod(self.bump(), self.bump())
        return {'y': q * 5 + r + self.n}
""", helpers="""    def bump(self):
        self.n = (self.n + 1) & 7
        return self.n
"""),
    _source("""        q, r = divmod(a, b)
        return {'y': (q * 5 + r) & 255}
""", prefix="""def divmod(x, y):
    return (x + y, x - y)
"""),
    _source("""        def divmod(x, y):
            return (x, y)
        q, r = divmod(a, b)
        return {'y': q * 5 + r}
"""),
    _source("""        q, r = self.divmod(a, b)
        return {'y': q * 5 + r}
""", helpers="""    def divmod(self, x, y):
        return (x, y)
"""),
    _source("""        q, r = divmod(self.divide(a, b), 2)
        return {'y': (q * 5 + r) & 255}
""", helpers="""    def divide(self, x, y):
        return x // y
"""),
    _source("""        q, r = divmod(a, 2)
        if r == 1:
            return {'y': q + 10}
        return {'y': q}
"""),
    _source("""        rows = sorted([a, b], key=lambda x: divmod(x, b)[1])
        return {'y': rows[0] * 4 + rows[1]}
"""),
    _source("""        total = 0
        for value in divmod(a - 2, b - 2):
            total += value
        return {'y': total & 255}
"""),
]


def _native(model, visit_log, plan, inputs):
    model.reset()
    visit_log.clear()
    try:
        output = model.process(**inputs)
    except ZeroDivisionError as error:
        goal = instrumented.attribute_safety_failure(error, plan.safety_sites)
        assert goal is not None
        return None, set(visit_log) | {goal}
    return output, set(visit_log)


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_divmod_outputs_failures_and_all_events_match_native(source, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[2, 2], lowerer=lowerer)
    env = {**proc.state_ranges, "a": ranges.Range(0, 3), "b": ranges.Range(0, 3)}
    plan = plan_source(source_model(source), source)
    namespace, _, visits = instrumented.load_source(source, goal_plan=plan)
    model = namespace["M"]()
    pre = {name: encoder.encode_concrete_value(
        proc.initial[name], env[name], name, kind=proc.types[name])
        for name in proc.state}
    encoded = encoder.encode_step(proc, pre=pre, env=env)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for a, b in itertools.product(range(4), repeat=2):
        inputs = {"a": a, "b": b}
        expected, expected_hits = _native(model, visits, plan, inputs)
        state, output, hits, error = interp.run_step(proc, inputs)
        assert bool(error) == (expected is None)
        assert hits == expected_hits
        if expected is not None:
            assert output.transactions == [expected]
            assert all(state[name] == getattr(model, name) for name in proc.state)
        solver.push()
        solver.add(encoded.pre["a"] == a, encoded.pre["b"] == b)
        assert solver.check() == z3.sat
        differences = [encoded.ok != (expected is not None)]
        differences.extend(term != (goal in hits)
                           for goal, term in encoded.hits.items())
        if expected is not None:
            differences.extend((encoded.post["#count"] != 1,
                                encoded.post["y"][0] != expected["y"]))
            differences.extend(encoded.post[name] != getattr(model, name)
                               for name in proc.state)
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize("source", SOURCES)
def test_each_public_goal_has_a_query_and_every_sat_has_a_native_witness(source):
    schema = TransactionSchema(
        [Endpoint("a", "input", 2), Endpoint("b", "input", 2),
         Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    plan = plan_source(source_model(source), source)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    namespace, _, visits = instrumented.load_source(source, goal_plan=plan)
    model = namespace["M"]()
    possible = set()
    for a, b in itertools.product(range(4), repeat=2):
        possible.update(_native(model, visits, plan, {"a": a, "b": b})[1])
    for goal in plan.manifest:
        answer = local_hit_any(exact.proc, exact.env, exact.proc.initial, (goal,),
            timeout_ms=60_000)
        expected = Outcome.SAT if goal in possible else Outcome.LOCAL_UNSAT
        assert answer.outcome is expected, (goal, answer.detail)
        if answer.outcome is Outcome.SAT:
            assert goal in _native(model, visits, plan, answer.candidate)[1]


def test_divmod_contract_is_a_pair_not_a_scalar():
    call = ast.parse("divmod(a, b)", mode="eval").body
    assert contract_of_expression(call, is_builtin=lambda *_: True) == ValueContract.positional(
        "tuple", (SCALAR, SCALAR))
    assert contract_of_expression(call, is_builtin=lambda *_: False) is UNKNOWN


def test_builtin_divmod_declares_one_error_and_shadowed_names_declare_none():
    for index in (0, 1, 2, 10):
        plan = plan_source(source_model(SOURCES[index]), SOURCES[index])
        assert [site.kind for site in plan.safety_sites
                if site.kind == "divzero"] == ["divzero"]
    for index in (6, 7, 8):
        plan = plan_source(source_model(SOURCES[index]), SOURCES[index])
        assert not plan.safety_sites


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_removing_divmod_guard_is_detected_independently(monkeypatch, lowerer):
    original = _Lowerer._guard

    def missing(self, condition, goal, code, node=None):
        if isinstance(node, ast.Call) and str(goal).endswith(":divzero"):
            return
        return original(self, condition, goal, code, node)

    monkeypatch.setattr(_Lowerer, "_guard", missing)
    with pytest.raises(LoweringError, match="neither produced nor discharged"):
        lower_with_state_ranges(SOURCES[0], input_widths=[2, 2], lowerer=lowerer)


@pytest.mark.parametrize("expression", ("divmod(a)", "divmod(a, b, 2)",
                                         "divmod(a, y=b)", "divmod([a], b)"))
def test_unsupported_call_forms_do_not_silently_change_python_semantics(expression):
    source = _source(f"        q, r = {expression}\n        return {{'y': q + r}}\n")
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[2, 2])
