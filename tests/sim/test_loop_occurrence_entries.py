"""Loop position carriers use every visit's source predecessor, not its join."""

import itertools

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import encoder, interp, plan
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
from behave.sim.engines.local_ir import local_hit_any
from behave.sim.engines.probing import Outcome

pytestmark = pytest.mark.solver
z3 = pytest.importorskip("z3")


SOURCE = """def power(value):
    magnitude = -value if value < 0 else value
    negative = -magnitude
    if negative <= -8:
        return 0
    exponent = negative // 2
    if exponent >= 0:
        return 1 << exponent
    return 1 << (-exponent)

def merge(a, b):
    difference = abs(a - b)
    return 0 if difference >= 8 else power(-difference)

class M:
    def reset(self): pass
    def process(self, a, b):
        values = [0, 0]
        for i in range(2):
            values[i] = a + merge(values[i], b)
        return {'y': (values[0] + values[1]) & 65535}
"""


@pytest.fixture(scope="module")
def model():
    schema = TransactionSchema(
        [Endpoint("a", "input", 16), Endpoint("b", "input", 16),
         Endpoint("y", "output", 16)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    return lower(SOURCE, schema, bound=1)


def test_loop_local_carriers_retain_helper_guards(model):
    prepared = plan.plan_encoding(model.proc, model.env)
    assert prepared.complete, [item.as_dict() for item in prepared.unresolved]
    encoder.encode_step(model.proc, env=model.env, plan=prepared)


def test_loop_source_native_ir_and_symbolic_hits_agree(model):
    goal_plan = plan_source(source_model(SOURCE), SOURCE)
    namespace, _, native_hits = instrumented.load_source(SOURCE, goal_plan=goal_plan)
    native = namespace["M"]()
    encoded = encoder.encode_step(model.proc, env=model.env)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for a, b in itertools.product((0, 1, 3, 7, 8, 16, 65535), repeat=2):
        native.reset()
        native_hits.clear()
        expected = native.process(a, b)
        _state, output, hits, error = interp.run_step(model.proc, {"a": a, "b": b})
        assert not error
        assert output.transactions == [expected]
        assert hits == set(native_hits)
        solver.push()
        solver.add(encoded.pre["a"] == a, encoded.pre["b"] == b)
        assert solver.check() == z3.sat
        solver.add(z3.Or(
            z3.Not(encoded.ok),
            z3.BV2Int(encoded.post["y"][0], is_signed=True) != expected["y"],
            *(term != (goal in hits) for goal, term in encoded.hits.items())))
        assert solver.check() == z3.unsat
        solver.pop()


def test_every_helper_goal_has_local_answer_and_sat_native_confirmation(model):
    goal_plan = plan_source(source_model(SOURCE), SOURCE)
    namespace, _, hits = instrumented.load_source(SOURCE, goal_plan=goal_plan)
    native = namespace["M"]()
    for goal in goal_plan.manifest:
        result = local_hit_any(model.proc, model.env, {}, (goal,),
            timeout_ms=60_000)
        assert result.outcome in (Outcome.SAT, Outcome.LOCAL_UNSAT), (goal, result.detail)
        if result.outcome is Outcome.SAT:
            native.reset()
            hits.clear()
            native.process(**result.candidate)
            assert goal in hits


@pytest.mark.parametrize("nested", [False, True])
def test_later_failing_iteration_is_not_replaced_by_first_visit(nested):
    prefix = ("        for i in range(2):\n            for j in range(2):\n" if nested else
              "        for i in range(4):\n")
    indent = "                " if nested else "            "
    source = ("class M:\n    def reset(self): pass\n"
              "    def process(self, a):\n        count = 0\n"
              "        values = [1, 2, 3]\n        result = 0\n" + prefix +
              f"{indent}result += values[count]\n{indent}count += a\n"
              "        return {'y': result}\n")
    schema = TransactionSchema(
        [Endpoint("a", "input", 3), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    model = lower(source, schema, bound=1)
    goal_plan = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=goal_plan)
    native = namespace["M"]()
    encoded = encoder.encode_step(model.proc, env=model.env)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for value in range(8):
        native_hits.clear()
        failed = False
        try:
            native.process(value)
        except IndexError as error:
            failed = True
            attributed = instrumented.attribute_safety_failure(error, goal_plan.safety_sites)
            assert attributed is not None
            native_hits.append(attributed)
        _, _, hits, error = interp.run_step(model.proc, {"a": value})
        assert bool(error) == failed
        assert hits == set(native_hits)
        solver.push()
        solver.add(encoded.pre["a"] == value)
        assert solver.check() == z3.sat
        solver.add(z3.Or(encoded.ok != (not failed),
                         *(term != (goal in hits) for goal, term in encoded.hits.items())))
        assert solver.check() == z3.unsat
        solver.pop()


def test_a_later_unrepresentable_shift_still_refuses():
    source = """class M:
    def reset(self): pass
    def process(self, a):
        shift = 0
        value = 0
        for i in range(2):
            value = 1 << shift
            shift = a
        return {'y': value & 255}
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 16), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    model = lower(source, schema, bound=1)
    prepared = plan.plan_encoding(model.proc, model.env)
    assert not prepared.complete
    assert any("left-shift" in item.detail for item in prepared.unresolved)
